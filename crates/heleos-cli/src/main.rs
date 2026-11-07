#![forbid(unsafe_code)]

//! The `heleos` operator binary.
//!
//! This executable is a thin composition layer. It parses one typed command
//! line, opens the exact operating-system resources each command needs under
//! no-follow, hands owned handles and domain values to `heleos-core`, and
//! renders exactly one JSON envelope on stdout or exactly one escaped JSON
//! diagnostic on stderr. No policy, integrity, or recovery decision is made
//! here: every such decision belongs to `heleos-core`.

mod args;

use std::ffi::{OsStr, OsString};
use std::fs::File;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use cap_fs_ext::{
    DirExt, FollowSymlinks, OpenOptionsFollowExt, OpenOptionsMaybeDirExt, OpenOptionsSyncExt,
};
use cap_std::fs::Dir;
use clap::Parser;
use heleos_core::{
    ApprovedPdfGuest, Clock, DataClass, FoundationReader, HeleosError, IdGenerator, IngestEngine,
    IngestOutcome, IngestReceipt, IngestRequest, IntakeSource, PdfLimits, PdfSandboxConfig,
    ProjectCreateRequest, ProjectService, RevisionId, Sha256Digest, Store, Vault, VaultConfig,
    VaultOpenMode, VaultVerification, WasiPdfProbe,
};
use serde::Serialize;
use serde_json::Value;

use crate::args::{
    BackupCommand, BackupCreateArgs, BackupVerifyArgs, Cli, Command, DatabaseArgs, DbCommand,
    DuplicateFlag, IngestArgs, InspectCommand, InspectFoundationArgs, JobsCommand, JobsResumeArgs,
    ProjectCommand, ProjectCreateArgs, RestoreArgs, VerifyArgs,
};

const APPROVED_PDF_GUEST: &[u8] =
    include_bytes!(concat!(env!("OUT_DIR"), "/heleos_pdf_guest.wasm"));
const APPROVED_PDF_GUEST_MANIFEST: &str =
    include_str!(concat!(env!("OUT_DIR"), "/pdf_guest_manifest.toml"));

const ENVELOPE_SCHEMA: &str = "heleos.cli-envelope/v1";
const DIAGNOSTIC_SCHEMA: &str = "heleos.cli-error/v1";
const VERIFY_SCHEMA: &str = "heleos.cli-verify/v1";
const MIGRATION_SCHEMA: &str = "heleos.cli-migration/v1";

const EXIT_SUCCESS: i32 = 0;
const EXIT_USAGE: i32 = 2;
const EXIT_INTEGRITY: i32 = 20;
const EXIT_QUARANTINE: i32 = 21;
const EXIT_DENIED: i32 = 22;
const EXIT_NOT_FOUND: i32 = 23;
const EXIT_INTERNAL: i32 = 70;

/// The longest diagnostic message rendered to stderr, in characters.
const DIAGNOSTIC_MESSAGE_MAX_CHARS: usize = 512;
/// The longest accepted age identity file, in bytes.
const IDENTITY_FILE_MAX_BYTES: usize = 4096;
/// Attempts made to find an unused unpredictable sandbox child name.
const STAGING_NAME_ATTEMPTS_MAX: u32 = 128;

fn main() {
    std::process::exit(run(std::env::args_os().collect()));
}

fn run(arguments: Vec<OsString>) -> i32 {
    if let Some(duplicate) = args::duplicate_long_flag(arguments.iter().skip(1)) {
        return emit_diagnostic(
            None,
            "usage",
            EXIT_USAGE,
            &duplicate_flag_message(duplicate),
        );
    }
    let parsed = match Cli::try_parse_from(&arguments) {
        Ok(parsed) => parsed,
        Err(error) => return emit_clap_error(&error),
    };
    let command = command_name(&parsed.command);
    match execute(parsed) {
        Ok(result) => emit_success(command, &result),
        Err(failure) => emit_diagnostic(
            Some(command),
            class_name(&failure.error),
            exit_code(&failure.error),
            &failure.message,
        ),
    }
}

// ---------------------------------------------------------------------------
// Failure classification
// ---------------------------------------------------------------------------

/// A failed command: the core error class plus one static, non-secret message.
///
/// Every message here is a compile-time literal or a `HeleosError` display
/// string, both of which are constant text. Operand bytes, key material, and
/// filesystem paths therefore cannot reach the diagnostic through this type.
#[derive(Debug)]
struct Failure {
    error: HeleosError,
    message: String,
}

impl Failure {
    fn new(error: HeleosError, message: &'static str) -> Self {
        Self {
            error,
            message: message.to_owned(),
        }
    }

    fn denied(message: &'static str) -> Self {
        Self::new(HeleosError::PolicyDenied, message)
    }
}

impl From<HeleosError> for Failure {
    fn from(error: HeleosError) -> Self {
        // Every `HeleosError` display string is a constant literal, so this
        // carries no operand bytes.
        let message = error.to_string();
        Self { error, message }
    }
}

/// Maps a core error class onto its documented process exit code.
///
/// The match is exhaustive on purpose: adding a `HeleosError` variant must be
/// a compile error here rather than a silent reclassification to `70`.
const fn exit_code(error: &HeleosError) -> i32 {
    match error {
        HeleosError::Integrity
        | HeleosError::BackupIntegrity
        | HeleosError::BackupDecryption
        | HeleosError::InvalidBackupContainer => EXIT_INTEGRITY,
        HeleosError::Quarantine => EXIT_QUARANTINE,
        HeleosError::WriterBusy
        | HeleosError::IdempotencyConflict
        | HeleosError::LeaseUnavailable
        | HeleosError::PolicyDenied => EXIT_DENIED,
        HeleosError::NotFound => EXIT_NOT_FOUND,
        HeleosError::Io(_)
        | HeleosError::Database
        | HeleosError::ResourceLimit
        | HeleosError::CommitOutcomeUnknown
        | HeleosError::InvalidDigest
        | HeleosError::InvalidId
        | HeleosError::Migration
        | HeleosError::CorruptPdf
        | HeleosError::EncryptedPdf
        | HeleosError::UnsupportedPdf
        | HeleosError::SuspiciousPdf
        | HeleosError::Quota
        | HeleosError::Timeout
        | HeleosError::InvalidStateTransition
        | HeleosError::FaultInjected
        | HeleosError::SandboxTrap
        | HeleosError::Serialization(_) => EXIT_INTERNAL,
    }
}

/// Names the machine-readable failure class carried in the diagnostic.
const fn class_name(error: &HeleosError) -> &'static str {
    match error {
        HeleosError::Io(_) => "io",
        HeleosError::InvalidDigest => "invalid_digest",
        HeleosError::InvalidId => "invalid_id",
        HeleosError::Database => "database",
        HeleosError::WriterBusy => "writer_busy",
        HeleosError::Migration => "migration",
        HeleosError::Integrity => "integrity",
        HeleosError::CorruptPdf => "corrupt_pdf",
        HeleosError::EncryptedPdf => "encrypted_pdf",
        HeleosError::UnsupportedPdf => "unsupported_pdf",
        HeleosError::SuspiciousPdf => "suspicious_pdf",
        HeleosError::ResourceLimit => "resource_limit",
        HeleosError::Quota => "quota",
        HeleosError::Timeout => "timeout",
        HeleosError::InvalidStateTransition => "invalid_state_transition",
        HeleosError::IdempotencyConflict => "idempotency_conflict",
        HeleosError::LeaseUnavailable => "lease_unavailable",
        HeleosError::FaultInjected => "fault_injected",
        HeleosError::CommitOutcomeUnknown => "commit_outcome_unknown",
        HeleosError::Quarantine => "quarantine",
        HeleosError::NotFound => "not_found",
        HeleosError::BackupDecryption => "backup_decryption",
        HeleosError::BackupIntegrity => "backup_integrity",
        HeleosError::InvalidBackupContainer => "invalid_backup_container",
        HeleosError::PolicyDenied => "policy_denied",
        HeleosError::SandboxTrap => "sandbox_trap",
        HeleosError::Serialization(_) => "serialization",
    }
}

const fn command_name(command: &Command) -> &'static str {
    match command {
        Command::Db {
            command: DbCommand::Migrate(_),
        } => "db.migrate",
        Command::Project {
            command: ProjectCommand::Create(_),
        } => "project.create",
        Command::Ingest(_) => "ingest",
        Command::Inspect {
            command: InspectCommand::Foundation(_),
        } => "inspect.foundation",
        Command::Verify(_) => "verify",
        Command::Backup {
            command: BackupCommand::Create(_),
        } => "backup.create",
        Command::Backup {
            command: BackupCommand::Verify(_),
        } => "backup.verify",
        Command::Restore(_) => "restore",
        Command::Jobs {
            command: JobsCommand::Resume(_),
        } => "jobs.resume",
    }
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

/// Renders `--help` and `--version`, or one fixed usage diagnostic.
///
/// Help and version are the operator's own request for human-readable text, so
/// clap renders them to stdout unchanged. Every other clap outcome is a
/// rejection of untrusted input, and its rendering echoes the offending
/// operand: an identity supplied where a public recipient belongs, a key path,
/// or an arbitrary hostile spelling. Those are replaced here by one fixed
/// sentence chosen from a closed vocabulary of clap error kinds.
fn emit_clap_error(error: &clap::Error) -> i32 {
    if matches!(
        error.kind(),
        clap::error::ErrorKind::DisplayHelp | clap::error::ErrorKind::DisplayVersion
    ) {
        let rendered = error.render().to_string();
        let mut stdout = std::io::stdout().lock();
        if stdout.write_all(rendered.as_bytes()).is_err() || stdout.flush().is_err() {
            return EXIT_INTERNAL;
        }
        return EXIT_SUCCESS;
    }
    emit_diagnostic(None, "usage", EXIT_USAGE, usage_message(error.kind()))
}

/// One fixed sentence per clap failure kind.
///
/// The rendered clap error is deliberately unused. Nothing here interpolates a
/// value, so no operand byte can reach the diagnostic through this path.
fn usage_message(kind: clap::error::ErrorKind) -> &'static str {
    use clap::error::ErrorKind;

    match kind {
        ErrorKind::InvalidValue | ErrorKind::ValueValidation => {
            "an argument value is not valid for the option it was supplied to"
        }
        ErrorKind::UnknownArgument => "an option or flag is not recognised",
        ErrorKind::InvalidSubcommand => "the subcommand is not recognised",
        ErrorKind::NoEquals => "an option requires its value after an equals sign",
        ErrorKind::TooManyValues => "an option was given more values than it accepts",
        ErrorKind::TooFewValues | ErrorKind::WrongNumberOfValues => {
            "an option was given the wrong number of values"
        }
        ErrorKind::ArgumentConflict => "two of the supplied options cannot be combined",
        ErrorKind::MissingRequiredArgument => "a required option was not supplied",
        ErrorKind::MissingSubcommand
        | ErrorKind::DisplayHelp
        | ErrorKind::DisplayHelpOnMissingArgumentOrSubcommand
        | ErrorKind::DisplayVersion => "a subcommand is required; run --help for the command list",
        ErrorKind::InvalidUtf8 => "an argument is not valid UTF-8",
        ErrorKind::Io | ErrorKind::Format => "the command line could not be processed",
        // `ErrorKind` is non-exhaustive: a future kind stays unnamed rather
        // than falling back to clap's operand-bearing rendering.
        _ => "the command line was rejected",
    }
}

/// One fixed sentence for a repeated long flag.
///
/// A declared flag is named from the CLI's own table; anything else is
/// reported without its spelling.
fn duplicate_flag_message(duplicate: DuplicateFlag) -> String {
    match duplicate {
        DuplicateFlag::Declared(flag) => {
            format!("the option {flag} was supplied more than once")
        }
        DuplicateFlag::Undeclared => "an option was supplied more than once".to_owned(),
    }
}

fn emit_success(command: &str, result: &Value) -> i32 {
    let envelope = serde_json::json!({
        "schema": ENVELOPE_SCHEMA,
        "command": command,
        "result": result,
    });
    let Ok(mut line) = serde_json::to_string(&envelope) else {
        return emit_diagnostic(
            Some(command),
            "serialization",
            EXIT_INTERNAL,
            "the result envelope could not be rendered",
        );
    };
    line.push('\n');
    let mut stdout = std::io::stdout().lock();
    if stdout.write_all(line.as_bytes()).is_err() || stdout.flush().is_err() {
        return EXIT_INTERNAL;
    }
    EXIT_SUCCESS
}

fn emit_diagnostic(command: Option<&str>, class: &str, code: i32, message: &str) -> i32 {
    let diagnostic = serde_json::json!({
        "schema": DIAGNOSTIC_SCHEMA,
        "command": command,
        "class": class,
        "exit_code": code,
        "message": flatten_diagnostic_message(message),
    });
    // `serde_json` escapes every quote, backslash, and control character, so
    // hostile operand bytes cannot break the one-object-per-failure contract.
    let Ok(mut line) = serde_json::to_string(&diagnostic) else {
        return EXIT_INTERNAL;
    };
    line.push('\n');
    let mut stderr = std::io::stderr().lock();
    if stderr.write_all(line.as_bytes()).is_err() || stderr.flush().is_err() {
        return EXIT_INTERNAL;
    }
    code
}

/// Collapses a message into one bounded, control-character-free line.
///
/// Every message that reaches here is already a compile-time literal or a
/// constant `HeleosError` display string. Flattening is the last, unconditional
/// guarantee that the stderr contract stays exactly one bounded line, whatever
/// a future message contains.
fn flatten_diagnostic_message(message: &str) -> String {
    let mut flattened = String::new();
    let mut characters = 0_usize;
    let mut pending_space = false;
    for character in message.chars() {
        if character.is_control() || character.is_whitespace() {
            pending_space = !flattened.is_empty();
            continue;
        }
        if characters >= DIAGNOSTIC_MESSAGE_MAX_CHARS {
            flattened.push_str("...");
            break;
        }
        if pending_space {
            flattened.push(' ');
            characters += 1;
            pending_space = false;
        }
        flattened.push(character);
        characters += 1;
    }
    if flattened.is_empty() {
        "the command line was rejected".to_owned()
    } else {
        flattened
    }
}

fn json<T: Serialize>(value: &T) -> Result<Value, Failure> {
    serde_json::to_value(value).map_err(|error| Failure {
        error: HeleosError::Serialization(error),
        message: "the result could not be rendered".to_owned(),
    })
}

// ---------------------------------------------------------------------------
// Ambient services
// ---------------------------------------------------------------------------

struct SystemClock;

impl Clock for SystemClock {
    fn now_unix_ms(&self) -> i64 {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .ok()
            .and_then(|elapsed| i64::try_from(elapsed.as_millis()).ok())
            // A clock before the epoch or beyond `i64` milliseconds is not a
            // usable timestamp; core rejects the out-of-range value.
            .unwrap_or(-1)
    }
}

struct RandomIds;

impl IdGenerator for RandomIds {
    fn next_uuid(&self) -> uuid::Uuid {
        uuid::Uuid::new_v4()
    }
}

// ---------------------------------------------------------------------------
// Filesystem admission
// ---------------------------------------------------------------------------

const NOT_A_REGULAR_FILE: &str = "the supplied path is not a regular direct file";
const NOT_PRIVATE_KEY_MATERIAL: &str = "key material must be a private single-linked file";

/// The type, link, reparse, and mode facts of one admitted handle.
///
/// Every field is read from the open handle, never from a second path lookup,
/// so the object described here is exactly the object whose bytes are read.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct AdmittedFileFacts {
    is_regular: bool,
    is_reparse: bool,
    /// The link count, absent when the platform cannot report one by handle.
    links: Option<u64>,
    /// The Unix permission bits, absent on platforms without a file mode.
    mode: Option<u32>,
}

impl AdmittedFileFacts {
    const fn is_regular_direct_file(self) -> bool {
        self.is_regular && !self.is_reparse
    }

    const fn is_single_linked(self) -> bool {
        matches!(self.links, Some(1))
    }
}

/// The Unix custody policy for key material read through one admitted handle.
#[cfg(any(unix, test))]
const fn unix_key_facts_are_private(facts: AdmittedFileFacts) -> bool {
    facts.is_regular_direct_file() && facts.is_single_linked() && matches!(facts.mode, Some(0o600))
}

/// The Windows type and link policy for key material, before its DACL is read.
#[cfg(any(windows, test))]
const fn windows_key_facts_are_admissible(facts: AdmittedFileFacts) -> bool {
    facts.is_regular_direct_file() && facts.is_single_linked()
}

/// Opens `path` no-follow and nonblocking, then requires a regular direct file.
///
/// Nonblocking admission matters as much as the type check: a FIFO with no
/// writer would otherwise park the process inside `open`, before the entry's
/// type could be rejected at all. The returned handle is the only reference the
/// caller keeps, so a post-admission rename or symlink swap cannot redirect the
/// bytes that were validated.
fn open_regular_nofollow(path: &Path) -> Result<File, Failure> {
    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    let name = path
        .file_name()
        .ok_or_else(|| Failure::denied("the supplied path does not name a file"))?;
    let directory = Dir::open_ambient_dir(parent, cap_std::ambient_authority())
        .map_err(|error| Failure::from(HeleosError::Io(error)))?;
    let mut options = cap_std::fs::OpenOptions::new();
    options
        .read(true)
        .follow(FollowSymlinks::No)
        .maybe_dir(false)
        .nonblock(true);
    let file = directory
        .open_with(name, &options)
        .map_err(|error| Failure::from(HeleosError::Io(error)))?
        .into_std();
    require_regular_file(&file)?;
    Ok(file)
}

/// Opens `path` no-follow and requires a regular, private, single-linked file.
fn open_private_regular_nofollow(path: &Path) -> Result<File, Failure> {
    let file = open_regular_nofollow(path)?;
    require_private_file(&file)?;
    Ok(file)
}

fn require_regular_file(file: &File) -> Result<(), Failure> {
    if admitted_file_facts(file)?.is_regular_direct_file() {
        Ok(())
    } else {
        Err(Failure::denied(NOT_A_REGULAR_FILE))
    }
}

#[cfg(unix)]
fn admitted_file_facts(file: &File) -> Result<AdmittedFileFacts, Failure> {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};

    let metadata = file
        .metadata()
        .map_err(|error| Failure::from(HeleosError::Io(error)))?;
    Ok(AdmittedFileFacts {
        is_regular: metadata.is_file() && !metadata.file_type().is_symlink(),
        is_reparse: false,
        links: Some(metadata.nlink()),
        mode: Some(metadata.permissions().mode() & 0o777),
    })
}

#[cfg(windows)]
fn admitted_file_facts(file: &File) -> Result<AdmittedFileFacts, Failure> {
    use cap_primitives::fs::{_WindowsByHandle, Metadata};
    use windows_sys::Win32::Storage::FileSystem::{
        FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT,
    };

    // `Metadata::from_file` is `GetFileInformationByHandle`: the attributes and
    // the link count below describe this handle, not a path resolved again.
    let metadata =
        Metadata::from_file(file).map_err(|error| Failure::from(HeleosError::Io(error)))?;
    let attributes = _WindowsByHandle::file_attributes(&metadata);
    Ok(AdmittedFileFacts {
        is_regular: attributes & FILE_ATTRIBUTE_DIRECTORY == 0,
        is_reparse: attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0,
        links: _WindowsByHandle::number_of_links(&metadata).map(u64::from),
        mode: None,
    })
}

#[cfg(not(any(unix, windows)))]
fn admitted_file_facts(file: &File) -> Result<AdmittedFileFacts, Failure> {
    file.metadata()
        .map_err(|error| Failure::from(HeleosError::Io(error)))?;
    Err(Failure::denied(
        "this platform cannot admit a file by handle",
    ))
}

#[cfg(unix)]
fn require_private_file(file: &File) -> Result<(), Failure> {
    if unix_key_facts_are_private(admitted_file_facts(file)?) {
        Ok(())
    } else {
        Err(Failure::denied(NOT_PRIVATE_KEY_MATERIAL))
    }
}

/// Requires the admitted Windows handle itself to hold private key material.
///
/// The type, reparse state, link count, owner, and DACL are all read from the
/// handle that is about to be read for bytes. Nothing reopens the path, so the
/// privacy evidence and the key bytes necessarily describe one object.
#[cfg(windows)]
fn require_private_file(file: &File) -> Result<(), Failure> {
    if !windows_key_facts_are_admissible(admitted_file_facts(file)?) {
        return Err(Failure::denied(NOT_PRIVATE_KEY_MATERIAL));
    }
    let principal = windows_process_sid()?;
    let owner = windows_handle_owner(file)?;
    if owner.as_deref() != Some(principal.as_str()) {
        return Err(Failure::denied(NOT_PRIVATE_KEY_MATERIAL));
    }
    if windows_handle_dacl_matches_exact_policy(
        file,
        &windows_allowed_sids_for_process_sid(&principal),
    )? {
        Ok(())
    } else {
        Err(Failure::denied(NOT_PRIVATE_KEY_MATERIAL))
    }
}

#[cfg(not(any(unix, windows)))]
fn require_private_file(file: &File) -> Result<(), Failure> {
    admitted_file_facts(file)?;
    Err(Failure::denied(NOT_PRIVATE_KEY_MATERIAL))
}

/// Reads exactly 32 bytes followed by end of file.
fn read_exact_thirty_two_bytes(file: &mut File) -> Result<[u8; 32], Failure> {
    let mut bytes = [0_u8; 32];
    if let Err(error) = file.read_exact(&mut bytes) {
        bytes.fill(0);
        let _zeroed = std::hint::black_box(&bytes);
        return Err(if error.kind() == std::io::ErrorKind::UnexpectedEof {
            Failure::denied("key material must be exactly 32 bytes")
        } else {
            Failure::from(HeleosError::Io(error))
        });
    }
    let mut trailing = [0_u8; 1];
    match file.read(&mut trailing) {
        Ok(0) => Ok(bytes),
        Ok(_) => {
            bytes.fill(0);
            let _zeroed = std::hint::black_box(&bytes);
            Err(Failure::denied("key material must be exactly 32 bytes"))
        }
        Err(error) => {
            bytes.fill(0);
            let _zeroed = std::hint::black_box(&bytes);
            Err(Failure::from(HeleosError::Io(error)))
        }
    }
}

/// Reads the private signing key into exactly one `SigningKey`.
///
/// The scratch buffer is overwritten and dropped as soon as the key value
/// exists, so the raw seed lives in exactly one place afterwards.
fn read_signing_key(path: &Path) -> Result<ed25519_dalek::SigningKey, Failure> {
    let mut file = open_private_regular_nofollow(path)?;
    let mut bytes = read_exact_thirty_two_bytes(&mut file)?;
    let signing_key = ed25519_dalek::SigningKey::from_bytes(&bytes);
    bytes.fill(0);
    let _zeroed = std::hint::black_box(&bytes);
    Ok(signing_key)
}

fn read_trusted_signer(path: &Path) -> Result<[u8; 32], Failure> {
    let mut file = open_private_regular_nofollow(path)?;
    read_exact_thirty_two_bytes(&mut file)
}

/// Reads exactly one age X25519 identity from a private file.
fn read_age_identity(path: &Path) -> Result<age::x25519::Identity, Failure> {
    use std::str::FromStr;

    let file = open_private_regular_nofollow(path)?;
    let mut bytes = Vec::new();
    let read = file
        .take(
            u64::try_from(IDENTITY_FILE_MAX_BYTES)
                .ok()
                .and_then(|cap| cap.checked_add(1))
                .ok_or_else(|| Failure::new(HeleosError::ResourceLimit, "identity cap overflow"))?,
        )
        .read_to_end(&mut bytes);
    let outcome = (|| {
        read.map_err(|error| Failure::from(HeleosError::Io(error)))?;
        if bytes.len() > IDENTITY_FILE_MAX_BYTES {
            return Err(Failure::denied("the identity file is too large"));
        }
        let text = std::str::from_utf8(&bytes)
            .map_err(|_| Failure::denied("the identity file is not UTF-8"))?;
        let mut lines = text.lines().filter(|line| !line.trim().is_empty());
        let line = lines
            .next()
            .ok_or_else(|| Failure::denied("the identity file is empty"))?;
        if lines.next().is_some() {
            return Err(Failure::denied(
                "the identity file must hold exactly one identity",
            ));
        }
        age::x25519::Identity::from_str(line.trim())
            .map_err(|_| Failure::denied("the identity file is not an age X25519 identity"))
    })();
    bytes.fill(0);
    let _zeroed = std::hint::black_box(&bytes);
    outcome
}

// ---------------------------------------------------------------------------
// Windows owner and DACL policy
//
// The predicates below are ordinary data rules, so they are compiled and tested
// on every platform. Only the thin binding layer that reads them off a live
// handle is Windows-specific, and that layer is the sole part whose behaviour
// this repository cannot demonstrate from macOS.
// ---------------------------------------------------------------------------

/// One access-control entry reduced to the fields this policy inspects.
#[cfg(any(windows, test))]
#[derive(Clone, Debug, Eq, PartialEq)]
struct WindowsAce {
    sid: String,
    is_access_allow: bool,
    flags: u8,
    mask: u32,
}

/// `FILE_ALL_ACCESS`: the only mask an admitted private entry may carry.
#[cfg(any(windows, test))]
const WINDOWS_FILE_ALL_ACCESS: u32 = 0x001f_01ff;
#[cfg(any(windows, test))]
const WINDOWS_SYSTEM_SID: &str = "S-1-5-18";
#[cfg(any(windows, test))]
const WINDOWS_ADMINISTRATORS_SID: &str = "S-1-5-32-544";

/// The exact principals the private DACL policy admits, process first.
#[cfg(any(windows, test))]
fn windows_allowed_sids_for_process_sid(process_sid: &str) -> Vec<String> {
    let mut allowed = vec![process_sid.to_owned()];
    if process_sid != WINDOWS_SYSTEM_SID {
        allowed.push(WINDOWS_SYSTEM_SID.to_owned());
    }
    allowed
}

/// The exact protected private DACL this CLI installs on its own child.
#[cfg(any(windows, test))]
fn windows_private_dacl_sddl(allowed: &[String]) -> String {
    let mut sddl = String::from("D:P");
    for sid in allowed {
        sddl.push_str("(A;;FA;;;");
        sddl.push_str(sid);
        sddl.push(')');
    }
    sddl
}

/// Requires the rendered DACL to be protected, so no inherited ACE applies.
#[cfg(any(windows, test))]
fn windows_dacl_is_protected(sddl: &str) -> bool {
    let Some(dacl) = sddl.strip_prefix("D:") else {
        return false;
    };
    let Some((mut flags, aces)) = dacl.split_once('(') else {
        return false;
    };
    if aces.is_empty() {
        return false;
    }
    let mut protected = false;
    while !flags.is_empty() {
        if let Some(rest) = flags.strip_prefix('P') {
            if protected {
                return false;
            }
            protected = true;
            flags = rest;
        } else if let Some(rest) = flags
            .strip_prefix("AR")
            .or_else(|| flags.strip_prefix("AI"))
        {
            flags = rest;
        } else {
            return false;
        }
    }
    protected
}

/// Requires the observed entries to be exactly one full-control allow ACE per
/// admitted principal, with no extra, duplicated, inherited, or deny entry.
#[cfg(any(windows, test))]
fn windows_dacl_entries_match_exact_policy(entries: &[WindowsAce], allowed: &[String]) -> bool {
    if entries.len() != allowed.len() {
        return false;
    }
    allowed.iter().all(|expected| {
        let mut matching = entries.iter().filter(|entry| entry.sid == *expected);
        matching.next().is_some_and(|entry| {
            matching.next().is_none()
                && entry.is_access_allow
                && entry.flags == 0
                && entry.mask == WINDOWS_FILE_ALL_ACCESS
        })
    })
}

// ---------------------------------------------------------------------------
// Windows shared-parent namespace-safety policy
//
// A shared default temporary parent is never rewritten by Heleos, so it is
// admitted rather than hardened. Admission is owner **and** DACL: the owner
// must be a machine principal, and the parent's present, non-null, revision-2
// DACL must contain no effective or inheritable dangerous allow grant to an
// untrusted SID. This is the same governed grammar and predicate the core
// retained snapshot parent applies, adapted privately to this binary; the CLI
// adds no core API and never touches the parent.
// ---------------------------------------------------------------------------

/// `CREATOR OWNER`, trusted only as a narrow inherit-only template entry.
#[cfg(any(windows, test))]
const WINDOWS_CREATOR_OWNER_SID: &str = "S-1-3-0";
/// Rights an untrusted allow entry may not hold on the parent itself:
/// `FILE_DELETE_CHILD`, `DELETE`, `WRITE_DAC`, `WRITE_OWNER`, `GENERIC_ALL`.
#[cfg(any(windows, test))]
const WINDOWS_PARENT_DANGEROUS_MASK: u32 = 0x100d_0040;
/// Rights an untrusted allow entry may not pass down to a created child: the
/// parent set plus `GENERIC_WRITE`, every file/directory add and write bit, and
/// the extended-attribute writes.
#[cfg(any(windows, test))]
const WINDOWS_INHERITED_DANGEROUS_MASK: u32 = 0x500d_0156;
#[cfg(any(windows, test))]
const WINDOWS_ACE_OBJECT_INHERIT: u8 = 0x01;
#[cfg(any(windows, test))]
const WINDOWS_ACE_CONTAINER_INHERIT: u8 = 0x02;
#[cfg(any(windows, test))]
const WINDOWS_ACE_INHERIT_ONLY: u8 = 0x08;
#[cfg(any(windows, test))]
const WINDOWS_PARENT_SDDL_MAX_BYTES: usize = 1_048_576;
#[cfg(any(windows, test))]
const WINDOWS_PARENT_ACE_MAX_RECORDS: usize = 65_535;

/// The record kind the bounded flat SDDL preflight recovered.
#[cfg(any(windows, test))]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ParsedParentAceKind {
    Allow,
    Deny,
}

/// One preflight record: only the fields the exact SDDL text already binds.
#[cfg(any(windows, test))]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ParsedParentAce {
    kind: ParsedParentAceKind,
    flags: u8,
}

/// One retained parent access-control entry. A deny record stays inert: the
/// exact SDDL already binds its fields, so no further `Ace` method is called
/// for it and it grants nothing.
#[cfg(any(windows, test))]
#[derive(Clone, Debug, Eq, PartialEq)]
enum WindowsParentAce {
    Allow {
        flags: u8,
        mask: u32,
        trustee_sid: String,
    },
    Deny,
}

/// Accepts the nonduplicated ACL flag set `{P, AR, AI}` and nothing else.
#[cfg(any(windows, test))]
fn parse_windows_parent_acl_flags(input: &[u8]) -> Option<()> {
    let mut cursor = 0_usize;
    let mut seen = 0_u8;
    while cursor < input.len() {
        let (flag, consumed) = match input[cursor] {
            b'P' => (0x01, 1),
            b'A' if input.get(cursor + 1) == Some(&b'R') => (0x02, 2),
            b'A' if input.get(cursor + 1) == Some(&b'I') => (0x04, 2),
            _ => return None,
        };
        if seen & flag != 0 {
            return None;
        }
        seen |= flag;
        cursor = cursor.checked_add(consumed)?;
    }
    Some(())
}

/// Accepts the nonduplicated ACE flag set `{CI, OI, NP, IO, ID}` and nothing
/// else, returning the exact raw bits the direct `Ace::flags()` must match.
#[cfg(any(windows, test))]
fn parse_windows_parent_ace_flags(input: &[u8]) -> Option<u8> {
    let mut cursor = 0_usize;
    let mut flags = 0_u8;
    while cursor < input.len() {
        let value = match (input.get(cursor), input.get(cursor + 1)) {
            (Some(b'C'), Some(b'I')) => WINDOWS_ACE_CONTAINER_INHERIT,
            (Some(b'O'), Some(b'I')) => WINDOWS_ACE_OBJECT_INHERIT,
            (Some(b'N'), Some(b'P')) => 0x04,
            (Some(b'I'), Some(b'O')) => WINDOWS_ACE_INHERIT_ONLY,
            (Some(b'I'), Some(b'D')) => 0x10,
            _ => return None,
        };
        if flags & value != 0 {
            return None;
        }
        flags |= value;
        cursor = cursor.checked_add(2)?;
    }
    Some(flags)
}

/// The bounded, iterative, full-consumption flat `A`/`D` preflight.
///
/// This runs *before* any direct ACL access, so a callback, object, audit,
/// unknown, malformed, revision-DS, or otherwise unparseable parent is refused
/// without ever reaching `GetSecurityDescriptorDacl`.
#[cfg(any(windows, test))]
fn parse_windows_parent_dacl_sddl(sddl: &str) -> Result<Vec<ParsedParentAce>, Failure> {
    let refused = || Failure::denied(SHARED_PARENT_UNSUPPORTED_DACL);

    let bytes = sddl.as_bytes();
    if !(2..=WINDOWS_PARENT_SDDL_MAX_BYTES).contains(&bytes.len())
        || !bytes.is_ascii()
        || !bytes.starts_with(b"D:")
    {
        return Err(refused());
    }

    let mut cursor = 2_usize;
    let flags_end = bytes[cursor..]
        .iter()
        .position(|byte| *byte == b'(')
        .and_then(|offset| cursor.checked_add(offset))
        .unwrap_or(bytes.len());
    parse_windows_parent_acl_flags(&bytes[cursor..flags_end]).ok_or_else(refused)?;
    cursor = flags_end;

    let mut records = Vec::new();
    while cursor < bytes.len() {
        if bytes[cursor] != b'(' {
            return Err(refused());
        }
        let body_start = cursor.checked_add(1).ok_or_else(refused)?;
        let mut body_end = body_start;
        while body_end < bytes.len() && bytes[body_end] != b')' {
            if bytes[body_end] == b'(' {
                return Err(refused());
            }
            body_end = body_end.checked_add(1).ok_or_else(refused)?;
        }
        if body_end == bytes.len() {
            return Err(refused());
        }

        let fields: Vec<&[u8]> = bytes[body_start..body_end]
            .split(|byte| *byte == b';')
            .collect();
        let [
            ace_type,
            ace_flags,
            rights,
            object_guid,
            inherited_object_guid,
            sid,
        ] = fields.as_slice()
        else {
            return Err(refused());
        };
        let kind = match *ace_type {
            b"A" => ParsedParentAceKind::Allow,
            b"D" => ParsedParentAceKind::Deny,
            _ => return Err(refused()),
        };
        let flags = parse_windows_parent_ace_flags(ace_flags).ok_or_else(refused)?;
        // The rights field intentionally admits lowercase hexadecimal, because
        // Windows renders numeric masks such as `0x7800003F` that way. Only the
        // type, ACL-flag, ACE-flag, and SID fields reject lowercase.
        if !rights.iter().all(u8::is_ascii_alphanumeric)
            || !object_guid.is_empty()
            || !inherited_object_guid.is_empty()
            || sid.is_empty()
            || !sid
                .iter()
                .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || *byte == b'-')
        {
            return Err(refused());
        }
        if records.len() == WINDOWS_PARENT_ACE_MAX_RECORDS {
            return Err(refused());
        }
        records.push(ParsedParentAce { kind, flags });
        cursor = body_end.checked_add(1).ok_or_else(refused)?;
    }
    Ok(records)
}

/// The retained owner half of the shared-parent policy.
///
/// Heleos never rewrites that parent, so the owner must already be a machine
/// principal: this process, `SYSTEM`, or the local administrators group that
/// owns the machine-wide temporary root.
#[cfg(any(windows, test))]
fn windows_shared_parent_owner_is_admissible(owner_sid: &str, process_sid: &str) -> bool {
    owner_sid == process_sid
        || owner_sid == WINDOWS_SYSTEM_SID
        || owner_sid == WINDOWS_ADMINISTRATORS_SID
}

/// The retained DACL half of the shared-parent policy.
///
/// A shared temporary root legitimately grants access to principals Heleos
/// does not control, so this is not the exact private-child rule. It is the
/// namespace-safety rule: no untrusted allow entry may hold a dangerous right
/// on the parent itself, and none may pass a dangerous right down to the child
/// this process is about to create. The check is mandatory *before* creation,
/// because hardening the child afterwards cannot revoke a handle an inherited
/// grant already allowed another principal to open.
#[cfg(any(windows, test))]
fn windows_shared_parent_dacl_is_namespace_safe(
    process_sid: &str,
    aces: &[WindowsParentAce],
) -> bool {
    aces.iter().all(|ace| {
        let WindowsParentAce::Allow {
            flags,
            mask,
            trustee_sid,
        } = ace
        else {
            // A deny record grants nothing, so it stays inert here.
            return true;
        };
        let inheritable = flags & (WINDOWS_ACE_CONTAINER_INHERIT | WINDOWS_ACE_OBJECT_INHERIT) != 0;
        let inherit_only = flags & WINDOWS_ACE_INHERIT_ONLY != 0;
        let base_trusted = trustee_sid == process_sid
            || trustee_sid == WINDOWS_SYSTEM_SID
            || trustee_sid == WINDOWS_ADMINISTRATORS_SID;
        // `CREATOR OWNER` is trusted only as a pure inherit-only template, which
        // resolves to the creating principal. `CREATOR GROUP` is never trusted.
        let creator_owner_narrow =
            trustee_sid == WINDOWS_CREATOR_OWNER_SID && inherit_only && inheritable;
        if base_trusted || creator_owner_narrow {
            return true;
        }
        let dangerous_on_parent = !inherit_only && mask & WINDOWS_PARENT_DANGEROUS_MASK != 0;
        let dangerous_on_child = inheritable && mask & WINDOWS_INHERITED_DANGEROUS_MASK != 0;
        !dangerous_on_parent && !dangerous_on_child
    })
}

/// Owner **and** namespace-safe DACL. Neither half admits a parent alone.
#[cfg(any(windows, test))]
fn windows_shared_parent_policy_accepts(
    owner_sid: &str,
    process_sid: &str,
    aces: &[WindowsParentAce],
) -> bool {
    windows_shared_parent_owner_is_admissible(owner_sid, process_sid)
        && windows_shared_parent_dacl_is_namespace_safe(process_sid, aces)
}

/// Requires the exact `ConvertStringSidToSid`/`ConvertSidToStringSid` round
/// trip, so only an owned canonical value ever enters a marker or a predicate.
#[cfg(windows)]
fn canonical_windows_sid(input: &str) -> Result<String, Failure> {
    use std::ffi::OsStr;
    use windows_permissions::wrappers::{ConvertSidToStringSid, ConvertStringSidToSid};

    let parsed = ConvertStringSidToSid(input)
        .map_err(|_| Failure::denied(SHARED_PARENT_UNSUPPORTED_DACL))?;
    let canonical = ConvertSidToStringSid(parsed.as_ref())
        .map_err(|_| Failure::denied(SHARED_PARENT_UNSUPPORTED_DACL))?;
    if canonical.as_os_str() != OsStr::new(input) {
        return Err(Failure::denied(SHARED_PARENT_UNSUPPORTED_DACL));
    }
    Ok(input.to_owned())
}

/// Captures one complete Windows shared-parent policy snapshot.
///
/// Exactly one owned security descriptor supplies every fact below, and it
/// stays live for the whole snapshot. A recheck obtains a new descriptor,
/// repeats this sequence in the same order, and compares owned markers; no
/// borrowed `Sid`, `Acl`, `Ace`, or descriptor pointer escapes this scope.
/// `windows-acl` is deliberately not used for the parent.
#[cfg(windows)]
fn windows_parent_marker(file: &File, process_sid: &str) -> Result<SharedParentMarker, Failure> {
    use windows_permissions::constants::{
        AccessRights, AceType, AclRevision, SeObjectType, SecurityInformation,
    };
    use windows_permissions::wrappers::{
        ConvertSecurityDescriptorToStringSecurityDescriptor, ConvertSidToStringSid, GetAce,
        GetAclInformationSize, GetSecurityDescriptorDacl, GetSecurityDescriptorOwner,
        GetSecurityInfo, IsValidAcl,
    };

    let refused = || Failure::denied(SHARED_PARENT_UNSUPPORTED_DACL);

    let descriptor = GetSecurityInfo(
        file,
        SeObjectType::SE_FILE_OBJECT,
        SecurityInformation::Owner | SecurityInformation::Dacl,
    )
    .map_err(|_| refused())?;

    let Some(borrowed_owner) =
        GetSecurityDescriptorOwner(descriptor.as_ref()).map_err(|_| refused())?
    else {
        return Err(refused());
    };
    let owner_text = ConvertSidToStringSid(borrowed_owner).map_err(|_| refused())?;
    let owner_sid = canonical_windows_sid(owner_text.to_str().ok_or_else(refused)?)?;

    let sddl = ConvertSecurityDescriptorToStringSecurityDescriptor(
        descriptor.as_ref(),
        SecurityInformation::Dacl,
    )
    .map_err(|_| refused())?;
    let dacl_sddl = sddl.to_str().ok_or_else(refused)?.to_owned();
    if dacl_sddl.contains("NO_ACCESS_CONTROL") {
        return Err(refused());
    }
    // Bounded flat preflight strictly before any direct ACL access.
    let parsed = parse_windows_parent_dacl_sddl(&dacl_sddl)?;

    let Some(acl) = GetSecurityDescriptorDacl(descriptor.as_ref()).map_err(|_| refused())? else {
        return Err(refused());
    };
    if !IsValidAcl(acl) || acl.revision_level() != AclRevision::ACL_REVISION {
        return Err(refused());
    }
    let acl_revision = AclRevision::ACL_REVISION as u8;
    let information = GetAclInformationSize(acl).map_err(|_| refused())?;
    if usize::try_from(information.AceCount).map_err(|_| refused())? != parsed.len() {
        return Err(refused());
    }
    if AccessRights::All.bits() != u32::MAX {
        return Err(refused());
    }

    let mut aces = Vec::with_capacity(parsed.len());
    for (index, parsed_ace) in parsed.into_iter().enumerate() {
        let index = u32::try_from(index).map_err(|_| refused())?;
        let ace = GetAce(acl, index).map_err(|_| refused())?;
        let expected_type = match parsed_ace.kind {
            ParsedParentAceKind::Allow => AceType::ACCESS_ALLOWED_ACE_TYPE,
            ParsedParentAceKind::Deny => AceType::ACCESS_DENIED_ACE_TYPE,
        };
        // The type is read first and matched before any other `Ace` method.
        if ace.ace_type() != expected_type {
            return Err(refused());
        }
        match parsed_ace.kind {
            ParsedParentAceKind::Deny => aces.push(WindowsParentAce::Deny),
            ParsedParentAceKind::Allow => {
                let flags = ace.flags().bits();
                if flags != parsed_ace.flags {
                    return Err(refused());
                }
                let mask = ace.mask().bits();
                let borrowed_sid = ace.sid().ok_or_else(refused)?;
                let trustee_text = ConvertSidToStringSid(borrowed_sid).map_err(|_| refused())?;
                let trustee_sid =
                    canonical_windows_sid(trustee_text.to_str().ok_or_else(refused)?)?;
                aces.push(WindowsParentAce::Allow {
                    flags,
                    mask,
                    trustee_sid,
                });
            }
        }
    }

    if !windows_shared_parent_policy_accepts(&owner_sid, process_sid, &aces) {
        return Err(Failure::denied(SHARED_PARENT_FAILS_POLICY));
    }
    Ok(SharedParentMarker {
        owner_sid,
        dacl_sddl,
        acl_revision,
        aces,
    })
}

/// Reads the process identity from the operating system, not from any file.
#[cfg(windows)]
fn windows_process_sid() -> Result<String, Failure> {
    stellar_agent_windows_identity::current_user_sid_string()
        .map_err(|_| Failure::denied("the process identity could not be established"))
}

/// Reads the owner SID of one live handle.
#[cfg(windows)]
fn windows_handle_owner(file: &File) -> Result<Option<String>, Failure> {
    use windows_permissions::constants::{SeObjectType, SecurityInformation};
    use windows_permissions::wrappers::{
        ConvertSidToStringSid, GetSecurityDescriptorOwner, GetSecurityInfo,
    };

    let descriptor = GetSecurityInfo(
        file,
        SeObjectType::SE_FILE_OBJECT,
        SecurityInformation::Owner,
    )
    .map_err(|error| Failure::from(HeleosError::Io(error)))?;
    let Some(owner) = GetSecurityDescriptorOwner(descriptor.as_ref())
        .map_err(|error| Failure::from(HeleosError::Io(error)))?
    else {
        return Ok(None);
    };
    let owner =
        ConvertSidToStringSid(owner).map_err(|error| Failure::from(HeleosError::Io(error)))?;
    Ok(owner.to_str().map(str::to_owned))
}

/// Reads one live handle's DACL and applies the exact private policy to it.
#[cfg(windows)]
fn windows_handle_dacl_matches_exact_policy(
    file: &File,
    allowed: &[String],
) -> Result<bool, Failure> {
    use std::os::windows::io::AsRawHandle;
    use windows_acl::acl::{ACL, AceType};
    use windows_permissions::constants::{SeObjectType, SecurityInformation};
    use windows_permissions::wrappers::{
        ConvertSecurityDescriptorToStringSecurityDescriptor, GetSecurityInfo,
    };

    let descriptor = GetSecurityInfo(
        file,
        SeObjectType::SE_FILE_OBJECT,
        SecurityInformation::Dacl,
    )
    .map_err(|error| Failure::from(HeleosError::Io(error)))?;
    let sddl =
        ConvertSecurityDescriptorToStringSecurityDescriptor(&descriptor, SecurityInformation::Dacl)
            .map_err(|error| Failure::from(HeleosError::Io(error)))?;
    let Some(sddl) = sddl.to_str() else {
        return Ok(false);
    };
    if !windows_dacl_is_protected(sddl) {
        return Ok(false);
    }
    let acl = ACL::from_file_handle(file.as_raw_handle().cast(), false)
        .map_err(|_| Failure::denied("the access control list could not be read"))?;
    let entries = acl
        .all()
        .map_err(|_| Failure::denied("the access control list could not be read"))?
        .into_iter()
        .map(|entry| WindowsAce {
            sid: entry.string_sid,
            is_access_allow: entry.entry_type == AceType::AccessAllow,
            flags: entry.flags,
            mask: entry.mask,
        })
        .collect::<Vec<_>>();
    Ok(windows_dacl_entries_match_exact_policy(&entries, allowed))
}

// ---------------------------------------------------------------------------
// Approved PDF guest
// ---------------------------------------------------------------------------

/// Builds the sandbox trust root from the build-embedded approved bytes.
///
/// `build.rs` already proved the embedded wasm matches the tracked manifest.
/// This re-checks the binding the binary actually carries so a mismatched
/// embed can never reach `WasiPdfProbe`.
fn approved_pdf_guest() -> Result<ApprovedPdfGuest, Failure> {
    require_embedded_guest_binding(APPROVED_PDF_GUEST, APPROVED_PDF_GUEST_MANIFEST)?;
    ApprovedPdfGuest::load_tracked(APPROVED_PDF_GUEST).map_err(Failure::from)
}

fn require_embedded_guest_binding(wasm: &[u8], manifest: &str) -> Result<(), Failure> {
    const MISMATCH: &str = "the embedded PDF guest does not match its tracked manifest";

    if manifest_field(manifest, "schema") != Some("heleos.pdf-guest-manifest/v1") {
        return Err(Failure::new(HeleosError::Integrity, MISMATCH));
    }
    let digest = Sha256Digest::hash_reader(std::io::Cursor::new(wasm)).map_err(Failure::from)?;
    if manifest_field(manifest, "wasm_sha256") != Some(digest.to_string().as_str()) {
        return Err(Failure::new(HeleosError::Integrity, MISMATCH));
    }
    if manifest_field(manifest, "wasm_byte_length") != Some(wasm.len().to_string().as_str()) {
        return Err(Failure::new(HeleosError::Integrity, MISMATCH));
    }
    Ok(())
}

/// Reads one scalar field from the tracked manifest's top-level table.
///
/// The manifest is a frozen, `\n`-terminated TOML document produced by the
/// reviewed build gate, so a line scan is sufficient and keeps a TOML parser
/// out of the runtime dependency set.
fn manifest_field<'a>(manifest: &'a str, key: &str) -> Option<&'a str> {
    for line in manifest.lines() {
        if line.starts_with('[') {
            break;
        }
        let Some((name, value)) = line.split_once('=') else {
            continue;
        };
        if name.trim() != key {
            continue;
        }
        let value = value.trim();
        return Some(
            value
                .strip_prefix('"')
                .map_or(value, |rest| rest.strip_suffix('"').unwrap_or(rest)),
        );
    }
    None
}

// ---------------------------------------------------------------------------
// PDF sandbox staging
// ---------------------------------------------------------------------------

const SHARED_PARENT_NOT_A_DIRECTORY: &str = "the shared temporary parent is not a direct directory";
const SHARED_PARENT_FAILS_POLICY: &str =
    "the shared temporary parent fails the retained owner and access policy";
#[cfg(any(windows, test))]
const SHARED_PARENT_UNSUPPORTED_DACL: &str =
    "the shared temporary parent has an unsupported access control list";
const SHARED_PARENT_REBOUND: &str = "the shared temporary parent changed after it was admitted";
const STAGING_CHILD_NOT_PRIVATE: &str =
    "the sandbox staging child is not the private child this run created";
const STAGING_CHILD_NOT_EMPTY: &str = "the sandbox staging child is not the empty child it was";
const STAGING_NAME_REBOUND: &str = "the sandbox staging name no longer binds to the retained child";
const STAGING_NAME_NOT_ONE_COMPONENT: &str =
    "the sandbox staging name is not one relative component";
const STAGING_AUTHORITY_LOST: &str = "the sandbox staging cleanup authority was not retained";
const STAGING_IDENTITY_UNPROVEN: &str =
    "the sandbox staging child was never proven to be the child this run created";
const STAGING_ALREADY_SETTLED: &str = "the sandbox staging cleanup already made its decision";

/// The identity of one retained filesystem object, read from its handle.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct RetainedIdentity {
    volume: u64,
    object: u64,
}

/// What the staging guard still owes the filesystem.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum StagingCleanup {
    /// The child exists and has not yet been through checked cleanup.
    Owed,
    /// Checked cleanup ran, or the child was deliberately preserved. Either
    /// decision is final: the destructor must never revisit it.
    Settled,
}

/// The identity this process runs as, established from the operating system.
#[cfg(unix)]
type ProcessPrincipal = u32;
#[cfg(windows)]
type ProcessPrincipal = String;
#[cfg(not(any(unix, windows)))]
type ProcessPrincipal = ();

/// The exact policy evidence captured from the retained shared parent.
///
/// The same marker binds pre-create admission and every later lifecycle
/// recheck, so a shared parent that was replaced or re-permissioned after
/// admission fails closed instead of silently carrying the staging child.
#[cfg(unix)]
#[derive(Clone, Debug, Eq, PartialEq)]
struct SharedParentMarker {
    owner_uid: u32,
    mode: u32,
}

#[cfg(windows)]
#[derive(Clone, Debug, Eq, PartialEq)]
struct SharedParentMarker {
    owner_sid: String,
    dacl_sddl: String,
    acl_revision: u8,
    aces: Vec<WindowsParentAce>,
}

#[cfg(not(any(unix, windows)))]
#[derive(Clone, Debug, Eq, PartialEq)]
struct SharedParentMarker;

/// The shared temporary parent this run captured, admitted, and retained.
///
/// The selector is read and canonicalized exactly once, and the captured
/// canonical path, the retained handle's strong identity, and the exact policy
/// marker are then held together. Each alone is insufficient: the handle proves
/// what the object *is*, while the canonical name is the only thing that can be
/// handed to a component which must open the staging tree by path. A recheck
/// therefore re-reads the retained handle **and** re-binds that same captured
/// name to it. Nothing here is ever recaptured, re-canonicalized, or reopened
/// through a fallback: a name that no longer denotes the retained parent is
/// refused instead of silently adopted.
struct RetainedTemporaryParent {
    directory: Dir,
    path: PathBuf,
    identity: RetainedIdentity,
    marker: SharedParentMarker,
}

impl RetainedTemporaryParent {
    /// Admits the shared parent before this process creates anything inside it.
    fn admit(principal: &ProcessPrincipal) -> Result<Self, Failure> {
        let path = std::fs::canonicalize(std::env::temp_dir())
            .map_err(|error| Failure::from(HeleosError::Io(error)))?;
        let directory = open_shared_temporary_parent(&path)?;
        // Admission strictly precedes mutation: the policy marker and the
        // strong identity are both captured from the retained handle before
        // this process creates anything in it.
        let marker = admit_shared_temporary_parent(&directory, principal)?;
        let identity = retained_identity(&directory)?;
        let parent = Self {
            directory,
            path,
            identity,
            marker,
        };
        // The captured name is bound to that identity immediately, so the
        // admitted object and the path this run will hand out are one object
        // from the first checkpoint onwards.
        parent.recheck(principal)?;
        Ok(parent)
    }

    fn directory(&self) -> &Dir {
        &self.directory
    }

    /// The captured canonical path: evidence, never authority on its own.
    fn path(&self) -> &Path {
        &self.path
    }

    /// Repeats the full admission against the retained handle, then requires
    /// the captured canonical name to still denote exactly that object.
    fn recheck(&self, principal: &ProcessPrincipal) -> Result<(), Failure> {
        self.recheck_retained(principal)?;
        self.recheck_captured_name(principal)
    }

    /// Directory/type/non-reparse, exact policy, and strong identity, all read
    /// back through the handle this run has held since before the child existed.
    fn recheck_retained(&self, principal: &ProcessPrincipal) -> Result<(), Failure> {
        recheck_shared_temporary_parent(&self.directory, principal, &self.marker)?;
        if retained_identity(&self.directory)? == self.identity {
            Ok(())
        } else {
            Err(Failure::denied(SHARED_PARENT_REBOUND))
        }
    }

    /// Binds the captured canonical name back to the retained parent.
    ///
    /// This reopens exactly the path captured at admission — never a freshly
    /// read selector and never an alternate fallback — and requires the object
    /// behind it to be the retained parent under the same policy. A name that
    /// cannot be opened, that is no longer a direct directory, that denotes a
    /// different object, or whose policy changed is refused; the replacement is
    /// never adopted. Like every other check here this is a checkpoint, not
    /// continuous protection of the interval that follows it.
    fn recheck_captured_name(&self, principal: &ProcessPrincipal) -> Result<(), Failure> {
        let ambient = std::fs::symlink_metadata(&self.path)
            .map_err(|_| Failure::denied(SHARED_PARENT_REBOUND))?;
        if !ambient.file_type().is_dir() {
            return Err(Failure::denied(SHARED_PARENT_NOT_A_DIRECTORY));
        }
        let rebound = open_shared_temporary_parent(&self.path)
            .map_err(|_| Failure::denied(SHARED_PARENT_REBOUND))?;
        if retained_identity(&rebound)? != self.identity {
            return Err(Failure::denied(SHARED_PARENT_REBOUND));
        }
        // The object the name denotes must satisfy the same admission, which
        // repeats its directory/type/non-reparse checks, not only the marker.
        recheck_shared_temporary_parent(&rebound, principal, &self.marker)
    }
}

/// An unpredictable private child of the shared default temporary parent.
///
/// The shared parent is admitted through one retained directory handle *before*
/// anything is created, and that same handle then creates, opens, hardens, and
/// finally removes the child. Heleos never changes the shared parent's owner,
/// mode, or DACL: only its own child is made private.
struct SandboxStaging {
    parent: RetainedTemporaryParent,
    name: String,
    /// The retained child handle, taken once cleanup or preservation settles.
    child: Option<Dir>,
    identity: RetainedIdentity,
    principal: ProcessPrincipal,
    path: PathBuf,
    cleanup: StagingCleanup,
    /// The first refused handoff checkpoint, retained until `finish` reports it.
    handoff_refusal: Option<Failure>,
}

impl SandboxStaging {
    fn create() -> Result<Self, Failure> {
        // The process principal and the temporary selector are each captured
        // exactly once; neither is read again for the life of this guard.
        let principal = process_principal()?;
        let parent = RetainedTemporaryParent::admit(&principal)?;

        let name = create_unused_private_child(parent.directory())?;
        // The staging path is derived from the captured canonical parent path,
        // which every later checkpoint re-binds to the retained parent handle.
        let path = parent.path().join(&name);
        // From the instant the name exists, the provisional guard owns it and
        // every construction failure below settles through its checked cleanup.
        let mut provisional = ProvisionalStagingChild::new(parent, principal, name);
        let constructed = (|| -> Result<RetainedIdentity, Failure> {
            let child = open_child_handle(provisional.parent()?.directory(), provisional.name())?;
            // Handle authority is retained before anything else can fail.
            provisional.record_child(child);
            // Identity authority follows immediately, so no later failure can
            // reach a name-based removal without knowing what it created.
            let identity = retained_identity(provisional.child()?)?;
            provisional.record_identity(identity);
            // Only the child is hardened, and only through its own handle.
            harden_staging_child(provisional.child()?, provisional.principal())?;
            provisional.record_hardened();
            require_staging_child(provisional.child()?, identity, provisional.principal())?;
            // The name must resolve to exactly the child that was just hardened.
            require_name_binding(
                provisional.parent()?.directory(),
                provisional.name(),
                identity,
            )?;
            Ok(identity)
        })();
        let identity = match constructed {
            Ok(identity) => identity,
            Err(primary) => return Err(provisional.settle(primary)),
        };
        let (parent, child, principal, name) = provisional.into_parts()?;
        Ok(Self {
            parent,
            name,
            child: Some(child),
            identity,
            principal,
            path,
            cleanup: StagingCleanup::Owed,
            handoff_refusal: None,
        })
    }

    /// The staging path, handed out only at a checkpoint that re-proves it.
    ///
    /// The sandbox is given a *path* and opens its own anchor from it, so the
    /// path leaves this guard only after the retained parent, its captured
    /// canonical name, the retained child's identity and privacy, and the name
    /// binding have all just been re-verified against this run's authority.
    /// This is the same class of checkpoint as the final one before removal: it
    /// does not defend the interval that follows it. An observed refusal is
    /// final: preserve the child and retain that exact failure for `finish`.
    fn checked_path(&mut self) -> Result<&Path, &Failure> {
        if self.handoff_refusal.is_none() {
            let checked = (|| {
                self.parent.recheck(&self.principal)?;
                let child = self
                    .child
                    .as_ref()
                    .ok_or_else(|| Failure::denied(STAGING_AUTHORITY_LOST))?;
                require_staging_child(child, self.identity, &self.principal)?;
                require_name_binding(self.parent.directory(), &self.name, self.identity)
            })();
            if let Err(failure) = checked {
                self.handoff_refusal = Some(failure);
                self.preserve();
            }
        }
        match &self.handoff_refusal {
            Some(failure) => Err(failure),
            None => Ok(&self.path),
        }
    }

    /// Settles the staging child and folds its cleanup into the command outcome.
    ///
    /// Precedence, in order: an uncertain commit is preserved first and keeps
    /// its own class, because its staging tree is evidence; next a retained
    /// handoff refusal stays final. Otherwise any cleanup failure overrides a
    /// success or primary failure, and the original result is returned only
    /// after cleanup is certain. No operation outcome exists when handoff was
    /// refused; the guard owns the original failure rather than copying it.
    fn finish(
        mut self,
        outcome: impl Into<Option<Result<Value, Failure>>>,
    ) -> Result<Value, Failure> {
        let outcome = match outcome.into() {
            Some(Err(failure)) if matches!(failure.error, HeleosError::CommitOutcomeUnknown) => {
                self.preserve();
                return Err(failure);
            }
            outcome => outcome,
        };
        if let Some(refusal) = self.handoff_refusal.take() {
            return Err(refusal);
        }
        let Some(outcome) = outcome else {
            self.preserve();
            return Err(Failure::denied(STAGING_AUTHORITY_LOST));
        };
        match self.remove_checked() {
            Ok(()) => outcome,
            Err(cleanup) => Err(cleanup),
        }
    }

    /// Deliberately leaves the child in place and stops the destructor.
    fn preserve(&mut self) {
        self.cleanup = StagingCleanup::Settled;
        self.child = None;
    }

    /// Removes the child relative to the retained parent, or preserves it.
    ///
    /// The decision is latched before the first fallible step, so a refusal
    /// below preserves the tree and neither this method nor the destructor will
    /// try again once a checked cleanup has decided.
    ///
    /// The removal itself is the pinned `cap-std` retained-parent-relative,
    /// one-component recursive removal. On Windows that primitive internally
    /// resolves its own handle and path, so the final checkpoint above and the
    /// removal are two operations, not one atomic validated deletion; Foundation
    /// 0.1 explicitly excludes continuous same-principal protection across that
    /// interval.
    fn remove_checked(&mut self) -> Result<(), Failure> {
        self.cleanup = StagingCleanup::Settled;
        let Some(child) = self.child.take() else {
            return Err(Failure::denied(STAGING_AUTHORITY_LOST));
        };
        require_one_component(&self.name)?;
        // The parent recheck repeats directory/type/non-reparse, exact policy,
        // strong identity, and the captured canonical name's binding to that
        // same retained object.
        self.parent.recheck(&self.principal)?;
        require_staging_child(&child, self.identity, &self.principal)?;
        require_name_binding(self.parent.directory(), &self.name, self.identity)?;
        // Final retained-parent-relative checkpoint reached. Release the child
        // handle before the removal so the pinned primitive is not racing our
        // own open handle on Windows.
        drop(child);
        remove_and_verify_absence(self.parent.directory(), &self.name)
    }
}

impl Drop for SandboxStaging {
    fn drop(&mut self) {
        // A fallback for paths that never reached checked cleanup, such as a
        // panic between construction and `finish`. It runs the same checked
        // removal rather than an unchecked name-based one. A settled guard is
        // never revisited: a checked cleanup that already refused, and a
        // deliberate preservation, both stay as they are.
        if self.cleanup != StagingCleanup::Owed {
            return;
        }
        drop(self.remove_checked());
    }
}

/// The staging child between its creation and a fully proven `SandboxStaging`.
///
/// Construction is the window in which a name exists but nothing about it has
/// been proven yet. This guard retains the parent capability, the parent policy
/// marker, and — as soon as each becomes available — the child handle and its
/// strong identity, so every construction failure settles through one explicit
/// checked decision instead of an unchecked name-based removal.
struct ProvisionalStagingChild {
    parent: Option<RetainedTemporaryParent>,
    principal: ProcessPrincipal,
    name: String,
    child: Option<Dir>,
    identity: Option<RetainedIdentity>,
    hardened: bool,
    armed: bool,
}

impl ProvisionalStagingChild {
    fn new(parent: RetainedTemporaryParent, principal: ProcessPrincipal, name: String) -> Self {
        Self {
            parent: Some(parent),
            principal,
            name,
            child: None,
            identity: None,
            hardened: false,
            armed: true,
        }
    }

    fn parent(&self) -> Result<&RetainedTemporaryParent, Failure> {
        self.parent
            .as_ref()
            .ok_or_else(|| Failure::denied(STAGING_AUTHORITY_LOST))
    }

    fn child(&self) -> Result<&Dir, Failure> {
        self.child
            .as_ref()
            .ok_or_else(|| Failure::denied(STAGING_AUTHORITY_LOST))
    }

    fn name(&self) -> &str {
        &self.name
    }

    fn principal(&self) -> &ProcessPrincipal {
        &self.principal
    }

    fn record_child(&mut self, child: Dir) {
        self.child = Some(child);
    }

    fn record_identity(&mut self, identity: RetainedIdentity) {
        self.identity = Some(identity);
    }

    fn record_hardened(&mut self) {
        self.hardened = true;
    }

    /// Hands the proven child to `SandboxStaging` and disarms this guard.
    fn into_parts(
        mut self,
    ) -> Result<(RetainedTemporaryParent, Dir, ProcessPrincipal, String), Failure> {
        let (Some(parent), Some(child)) = (self.parent.take(), self.child.take()) else {
            // Unreachable from `create`, and deliberately non-destructive: an
            // unproven name is preserved rather than removed.
            return Err(Failure::denied(STAGING_AUTHORITY_LOST));
        };
        self.armed = false;
        let principal = std::mem::take(&mut self.principal);
        let name = std::mem::take(&mut self.name);
        Ok((parent, child, principal, name))
    }

    /// Settles a construction failure, propagating cleanup uncertainty above
    /// the primary error exactly as `SandboxStaging::finish` does.
    fn settle(mut self, primary: Failure) -> Failure {
        match self.cleanup() {
            Ok(()) => primary,
            Err(cleanup) => cleanup,
        }
    }

    /// The single checked cleanup decision for a construction failure.
    ///
    /// It is a single attempt: the decision is latched before the first
    /// fallible step so `Drop` never re-decides against a namespace that may
    /// have changed since. A name whose binding was refused, or that was never
    /// proven to be this run's child, is preserved rather than removed.
    fn cleanup(&mut self) -> Result<(), Failure> {
        if !self.armed {
            return Err(Failure::denied(STAGING_ALREADY_SETTLED));
        }
        self.armed = false;
        require_one_component(&self.name)?;
        // Deletion authority comes from the created object's own identity, and
        // it is established here — while the retained child handle can still
        // supply it — before anything is released or reopened.
        let expected = self.creation_identity()?;
        // Release the provisional child handle before any name-relative work.
        self.child.take();
        let parent = self
            .parent
            .as_ref()
            .ok_or_else(|| Failure::denied(STAGING_AUTHORITY_LOST))?;
        parent.recheck(&self.principal)?;
        let bound = parent
            .directory()
            .open_dir_nofollow(&self.name)
            .map_err(|_| Failure::denied(STAGING_NAME_REBOUND))?;
        let observed = retained_identity(&bound)?;
        if observed != expected {
            // A binding refusal never authorises deleting the name it refused.
            return Err(Failure::denied(STAGING_NAME_REBOUND));
        }
        if self.hardened {
            require_staging_child(&bound, observed, &self.principal)?;
        }
        // Nothing is ever written into the child during construction, so a
        // non-empty entry here is not the object this run created.
        if bound
            .entries()
            .map_err(|error| Failure::from(HeleosError::Io(error)))?
            .next()
            .transpose()
            .map_err(|error| Failure::from(HeleosError::Io(error)))?
            .is_some()
        {
            return Err(Failure::denied(STAGING_CHILD_NOT_EMPTY));
        }
        drop(bound);
        remove_and_verify_absence(parent.directory(), &self.name)
    }

    /// Establishes what this run created, from retained authority only.
    ///
    /// The identity recorded during construction is used when it exists. If the
    /// failure arrived before that point, the still-retained child handle — the
    /// object this run opened through its own retained parent — may supply it.
    /// Nothing else may: an identity read back from the name, or inferred from
    /// an empty directory, is exactly the substitution this refuses. When
    /// neither source is available, the child is preserved and the cleanup is
    /// reported as uncertain rather than treated as deletion authority.
    fn creation_identity(&mut self) -> Result<RetainedIdentity, Failure> {
        if let Some(identity) = self.identity {
            return Ok(identity);
        }
        let Some(child) = self.child.as_ref() else {
            return Err(Failure::denied(STAGING_IDENTITY_UNPROVEN));
        };
        let identity =
            retained_identity(child).map_err(|_| Failure::denied(STAGING_IDENTITY_UNPROVEN))?;
        self.identity = Some(identity);
        Ok(identity)
    }
}

impl Drop for ProvisionalStagingChild {
    fn drop(&mut self) {
        if self.armed {
            drop(self.cleanup());
        }
    }
}

/// Requires a staging name to be exactly one normal relative component.
fn require_one_component(name: &str) -> Result<(), Failure> {
    let mut components = Path::new(name).components();
    match (components.next(), components.next()) {
        (Some(std::path::Component::Normal(only)), None) if only == OsStr::new(name) => Ok(()),
        _ => Err(Failure::denied(STAGING_NAME_NOT_ONE_COMPONENT)),
    }
}

/// Removes one component relative to the retained parent and proves it is gone.
fn remove_and_verify_absence(parent: &Dir, name: &str) -> Result<(), Failure> {
    parent.remove_dir_all(name).map_err(|error| {
        if error.kind() == std::io::ErrorKind::NotFound {
            Failure::denied(STAGING_NAME_REBOUND)
        } else {
            Failure::from(HeleosError::Io(error))
        }
    })?;
    match parent.symlink_metadata(name) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Ok(_) => Err(Failure::denied(STAGING_NAME_REBOUND)),
        Err(error) => Err(Failure::from(HeleosError::Io(error))),
    }
}

/// Creates one unpredictable private child through the retained parent handle.
fn create_unused_private_child(parent: &Dir) -> Result<String, Failure> {
    for _ in 0..STAGING_NAME_ATTEMPTS_MAX {
        let name = format!("heleos-pdf-{}", uuid::Uuid::new_v4());
        match create_private_child(parent, &name) {
            Ok(()) => return Ok(name),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(error) => return Err(Failure::from(HeleosError::Io(error))),
        }
    }
    Err(Failure::denied(
        "no unused sandbox staging name was available",
    ))
}

/// Requires the child's name to still resolve to the retained child object.
fn require_name_binding(
    parent: &Dir,
    name: &str,
    identity: RetainedIdentity,
) -> Result<(), Failure> {
    let bound = parent
        .open_dir_nofollow(name)
        .map_err(|_| Failure::denied(STAGING_NAME_REBOUND))?;
    if retained_identity(&bound)? == identity {
        Ok(())
    } else {
        Err(Failure::denied(STAGING_NAME_REBOUND))
    }
}

#[cfg(unix)]
fn process_principal() -> Result<ProcessPrincipal, Failure> {
    // The identity comes from the process, never from the metadata of a file
    // this process is about to create or admit.
    Ok(rustix::process::geteuid().as_raw())
}

#[cfg(windows)]
fn process_principal() -> Result<ProcessPrincipal, Failure> {
    windows_process_sid()
}

#[cfg(not(any(unix, windows)))]
fn process_principal() -> Result<ProcessPrincipal, Failure> {
    Err(Failure::denied(
        "this platform cannot establish the process identity",
    ))
}

#[cfg(unix)]
fn open_shared_temporary_parent(shared: &Path) -> Result<Dir, Failure> {
    Dir::open_ambient_dir(shared, cap_std::ambient_authority())
        .map_err(|error| Failure::from(HeleosError::Io(error)))
}

/// Opens the shared parent with the exact rights the owner check needs.
#[cfg(windows)]
fn open_shared_temporary_parent(shared: &Path) -> Result<Dir, Failure> {
    use std::os::windows::fs::OpenOptionsExt;
    use windows_sys::Win32::Foundation::GENERIC_READ;
    use windows_sys::Win32::Storage::FileSystem::{
        FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ, FILE_SHARE_WRITE, READ_CONTROL,
    };

    let mut options = std::fs::OpenOptions::new();
    options
        .access_mode(GENERIC_READ | READ_CONTROL | FILE_READ_ATTRIBUTES)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT);
    let file = options
        .open(shared)
        .map_err(|error| Failure::from(HeleosError::Io(error)))?;
    Ok(Dir::from_std_file(file))
}

#[cfg(not(any(unix, windows)))]
fn open_shared_temporary_parent(shared: &Path) -> Result<Dir, Failure> {
    let _ = shared;
    Err(Failure::denied(SHARED_PARENT_NOT_A_DIRECTORY))
}

#[cfg(unix)]
fn admit_shared_temporary_parent(
    parent: &Dir,
    principal: &ProcessPrincipal,
) -> Result<SharedParentMarker, Failure> {
    use cap_std::fs::MetadataExt;

    // The metadata comes from the retained handle, so the object admitted here
    // is the object every later operation is relative to.
    let metadata = parent
        .dir_metadata()
        .map_err(|error| Failure::from(HeleosError::Io(error)))?;
    if !metadata.is_dir() {
        return Err(Failure::denied(SHARED_PARENT_NOT_A_DIRECTORY));
    }
    let marker = SharedParentMarker {
        owner_uid: metadata.uid(),
        mode: metadata.mode() & 0o7777,
    };
    if shared_temporary_parent_is_admissible(marker.owner_uid, marker.mode, *principal) {
        Ok(marker)
    } else {
        Err(Failure::denied(SHARED_PARENT_FAILS_POLICY))
    }
}

/// The retained Unix owner and mode policy for a shared temporary parent.
///
/// The parent must be owned by this process or by root, and must either deny
/// group and other writes outright or carry the sticky bit that prevents
/// cross-principal renames of our child.
#[cfg(unix)]
const fn shared_temporary_parent_is_admissible(
    owner_uid: u32,
    mode: u32,
    effective_uid: u32,
) -> bool {
    (owner_uid == effective_uid || owner_uid == 0) && (mode & 0o022 == 0 || mode & 0o1000 != 0)
}

/// Admits the shared parent under the exact owner **and** namespace-safe DACL
/// policy, before any child is created and without ever changing that parent.
#[cfg(windows)]
fn admit_shared_temporary_parent(
    parent: &Dir,
    principal: &ProcessPrincipal,
) -> Result<SharedParentMarker, Failure> {
    use cap_primitives::fs::_WindowsByHandle;
    use windows_sys::Win32::Storage::FileSystem::{
        FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT,
    };

    let metadata = parent
        .dir_metadata()
        .map_err(|error| Failure::from(HeleosError::Io(error)))?;
    let attributes = _WindowsByHandle::file_attributes(&metadata);
    if attributes & FILE_ATTRIBUTE_DIRECTORY == 0 || attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
    {
        return Err(Failure::denied(SHARED_PARENT_NOT_A_DIRECTORY));
    }
    let handle = parent
        .try_clone()
        .map_err(|error| Failure::from(HeleosError::Io(error)))?
        .into_std_file();
    windows_parent_marker(&handle, principal)
}

#[cfg(not(any(unix, windows)))]
fn admit_shared_temporary_parent(
    parent: &Dir,
    principal: &ProcessPrincipal,
) -> Result<SharedParentMarker, Failure> {
    let _ = (parent, principal);
    Err(Failure::denied(SHARED_PARENT_NOT_A_DIRECTORY))
}

/// Repeats the complete admission and requires the exact same marker.
///
/// The shared parent is never rewritten, so a difference here means the object
/// behind the retained handle changed after admission. Every lifecycle step
/// that is about to act on the parent — including each checked cleanup —
/// re-runs this before it touches a name.
fn recheck_shared_temporary_parent(
    parent: &Dir,
    principal: &ProcessPrincipal,
    expected: &SharedParentMarker,
) -> Result<(), Failure> {
    if &admit_shared_temporary_parent(parent, principal)? == expected {
        Ok(())
    } else {
        Err(Failure::denied(SHARED_PARENT_REBOUND))
    }
}

/// Creates the child privately from the first instant it exists.
#[cfg(unix)]
fn create_private_child(parent: &Dir, name: &str) -> std::io::Result<()> {
    use cap_std::fs::DirBuilderExt;

    let mut builder = cap_std::fs::DirBuilder::new();
    builder.mode(0o700);
    parent.create_dir_with(name, &builder)
}

#[cfg(not(unix))]
fn create_private_child(parent: &Dir, name: &str) -> std::io::Result<()> {
    parent.create_dir(name)
}

#[cfg(unix)]
fn open_child_handle(parent: &Dir, name: &str) -> Result<Dir, Failure> {
    parent
        .open_dir_nofollow(name)
        .map_err(|error| Failure::from(HeleosError::Io(error)))
}

/// Opens the child with the exact rights its hardening and readback need.
#[cfg(windows)]
fn open_child_handle(parent: &Dir, name: &str) -> Result<Dir, Failure> {
    use cap_fs_ext::OpenOptionsExt;
    use windows_sys::Win32::Foundation::GENERIC_READ;
    use windows_sys::Win32::Storage::FileSystem::{
        FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ, FILE_SHARE_WRITE, READ_CONTROL, WRITE_DAC,
    };

    let mut options = cap_std::fs::OpenOptions::new();
    options
        .read(true)
        .access_mode(GENERIC_READ | READ_CONTROL | WRITE_DAC | FILE_READ_ATTRIBUTES)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
        .follow(FollowSymlinks::No)
        .maybe_dir(true);
    let file = parent
        .open_with(name, &options)
        .map_err(|error| Failure::from(HeleosError::Io(error)))?
        .into_std();
    Ok(Dir::from_std_file(file))
}

#[cfg(not(any(unix, windows)))]
fn open_child_handle(parent: &Dir, name: &str) -> Result<Dir, Failure> {
    let _ = (parent, name);
    Err(Failure::denied(STAGING_CHILD_NOT_PRIVATE))
}

#[cfg(unix)]
fn retained_identity(directory: &Dir) -> Result<RetainedIdentity, Failure> {
    use cap_std::fs::MetadataExt;

    let metadata = directory
        .dir_metadata()
        .map_err(|error| Failure::from(HeleosError::Io(error)))?;
    Ok(RetainedIdentity {
        volume: metadata.dev(),
        object: metadata.ino(),
    })
}

#[cfg(windows)]
fn retained_identity(directory: &Dir) -> Result<RetainedIdentity, Failure> {
    use cap_primitives::fs::_WindowsByHandle;

    let metadata = directory
        .dir_metadata()
        .map_err(|error| Failure::from(HeleosError::Io(error)))?;
    let (Some(volume), Some(object)) = (
        _WindowsByHandle::volume_serial_number(&metadata),
        _WindowsByHandle::file_index(&metadata),
    ) else {
        return Err(Failure::denied(
            "the retained directory identity is unavailable",
        ));
    };
    Ok(RetainedIdentity {
        volume: u64::from(volume),
        object,
    })
}

#[cfg(not(any(unix, windows)))]
fn retained_identity(directory: &Dir) -> Result<RetainedIdentity, Failure> {
    let _ = directory;
    Err(Failure::denied(
        "the retained directory identity is unavailable",
    ))
}

/// Makes the child private through its own retained handle.
#[cfg(unix)]
fn harden_staging_child(child: &Dir, principal: &ProcessPrincipal) -> Result<(), Failure> {
    use cap_std::fs::MetadataExt;
    use std::os::unix::fs::PermissionsExt;

    let _ = principal;
    let metadata = child
        .dir_metadata()
        .map_err(|error| Failure::from(HeleosError::Io(error)))?;
    if metadata.mode() & 0o777 != 0o700 {
        // A restrictive umask can narrow the creation mode; widen it back to
        // exactly `0o700` through the handle, never by path.
        child
            .try_clone()
            .map_err(|error| Failure::from(HeleosError::Io(error)))?
            .into_std_file()
            .set_permissions(std::fs::Permissions::from_mode(0o700))
            .map_err(|error| Failure::from(HeleosError::Io(error)))?;
    }
    Ok(())
}

/// Installs the exact protected private DACL through the child's own handle.
#[cfg(windows)]
fn harden_staging_child(child: &Dir, principal: &ProcessPrincipal) -> Result<(), Failure> {
    use windows_permissions::constants::{SeObjectType, SecurityInformation};
    use windows_permissions::wrappers::{
        ConvertStringSecurityDescriptorToSecurityDescriptor, GetSecurityDescriptorDacl,
        SetSecurityInfo,
    };

    let mut handle = child
        .try_clone()
        .map_err(|error| Failure::from(HeleosError::Io(error)))?
        .into_std_file();
    let allowed = windows_allowed_sids_for_process_sid(principal);
    let descriptor =
        ConvertStringSecurityDescriptorToSecurityDescriptor(&windows_private_dacl_sddl(&allowed))
            .map_err(|_| Failure::denied(STAGING_CHILD_NOT_PRIVATE))?;
    let Some(dacl) = GetSecurityDescriptorDacl(descriptor.as_ref())
        .map_err(|_| Failure::denied(STAGING_CHILD_NOT_PRIVATE))?
    else {
        return Err(Failure::denied(STAGING_CHILD_NOT_PRIVATE));
    };
    SetSecurityInfo(
        &mut handle,
        SeObjectType::SE_FILE_OBJECT,
        SecurityInformation::Dacl | SecurityInformation::ProtectedDacl,
        None,
        None,
        Some(dacl),
        None,
    )
    .map_err(|_| Failure::denied(STAGING_CHILD_NOT_PRIVATE))?;
    Ok(())
}

#[cfg(not(any(unix, windows)))]
fn harden_staging_child(child: &Dir, principal: &ProcessPrincipal) -> Result<(), Failure> {
    let _ = (child, principal);
    Err(Failure::denied(STAGING_CHILD_NOT_PRIVATE))
}

/// Requires the retained child to still be this run's private child.
#[cfg(unix)]
fn require_staging_child(
    child: &Dir,
    identity: RetainedIdentity,
    principal: &ProcessPrincipal,
) -> Result<(), Failure> {
    use cap_std::fs::MetadataExt;

    let metadata = child
        .dir_metadata()
        .map_err(|error| Failure::from(HeleosError::Io(error)))?;
    let observed = RetainedIdentity {
        volume: metadata.dev(),
        object: metadata.ino(),
    };
    if !metadata.is_dir()
        || metadata.uid() != *principal
        || metadata.mode() & 0o777 != 0o700
        || observed != identity
    {
        return Err(Failure::denied(STAGING_CHILD_NOT_PRIVATE));
    }
    Ok(())
}

#[cfg(windows)]
fn require_staging_child(
    child: &Dir,
    identity: RetainedIdentity,
    principal: &ProcessPrincipal,
) -> Result<(), Failure> {
    use cap_primitives::fs::_WindowsByHandle;
    use windows_sys::Win32::Storage::FileSystem::{
        FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT,
    };

    let metadata = child
        .dir_metadata()
        .map_err(|error| Failure::from(HeleosError::Io(error)))?;
    let attributes = _WindowsByHandle::file_attributes(&metadata);
    if attributes & FILE_ATTRIBUTE_DIRECTORY == 0
        || attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || retained_identity(child)? != identity
    {
        return Err(Failure::denied(STAGING_CHILD_NOT_PRIVATE));
    }
    let handle = child
        .try_clone()
        .map_err(|error| Failure::from(HeleosError::Io(error)))?
        .into_std_file();
    if windows_handle_owner(&handle)?.as_deref() != Some(principal.as_str()) {
        return Err(Failure::denied(STAGING_CHILD_NOT_PRIVATE));
    }
    if windows_handle_dacl_matches_exact_policy(
        &handle,
        &windows_allowed_sids_for_process_sid(principal),
    )? {
        Ok(())
    } else {
        Err(Failure::denied(STAGING_CHILD_NOT_PRIVATE))
    }
}

#[cfg(not(any(unix, windows)))]
fn require_staging_child(
    child: &Dir,
    identity: RetainedIdentity,
    principal: &ProcessPrincipal,
) -> Result<(), Failure> {
    let _ = (child, identity, principal);
    Err(Failure::denied(STAGING_CHILD_NOT_PRIVATE))
}

// ---------------------------------------------------------------------------
// Command execution
// ---------------------------------------------------------------------------

fn execute(cli: Cli) -> Result<Value, Failure> {
    match cli.command {
        Command::Db {
            command: DbCommand::Migrate(arguments),
        } => migrate(arguments),
        Command::Project {
            command: ProjectCommand::Create(arguments),
        } => project_create(arguments),
        Command::Ingest(arguments) => ingest(arguments),
        Command::Inspect {
            command: InspectCommand::Foundation(arguments),
        } => inspect_foundation(arguments),
        Command::Verify(arguments) => verify(arguments),
        Command::Backup {
            command: BackupCommand::Create(arguments),
        } => backup_create(arguments),
        Command::Backup {
            command: BackupCommand::Verify(arguments),
        } => backup_verify(arguments),
        Command::Restore(arguments) => restore(arguments),
        Command::Jobs {
            command: JobsCommand::Resume(arguments),
        } => jobs_resume(arguments),
    }
}

#[derive(Serialize)]
struct MigrationView {
    schema: &'static str,
    from_version: i64,
    to_version: i64,
    applied_versions: Vec<i64>,
}

fn migrate(arguments: DatabaseArgs) -> Result<Value, Failure> {
    let mut store = Store::open_writer(&arguments.database)?;
    let report = store.migrate()?;
    json(&MigrationView {
        schema: MIGRATION_SCHEMA,
        from_version: report.from_version,
        to_version: report.to_version,
        applied_versions: report.applied_versions,
    })
}

fn project_create(arguments: ProjectCreateArgs) -> Result<Value, Failure> {
    let mut store = Store::open_writer(&arguments.database)?;
    let receipt = ProjectService::new(&mut store, &SystemClock, &RandomIds)?.create(
        ProjectCreateRequest {
            project_id: arguments.id,
            name: arguments.name,
            actor: arguments.actor,
            data_class: DataClass::Internal,
        },
    )?;
    json(&receipt)
}

/// Classifies one durable intake receipt at the operator boundary.
///
/// Core records a quarantined intake as a committed `Ok` receipt: the evidence,
/// the audit chain, and the quarantine record are all durable, and only the
/// operator-facing outcome differs from an acceptance. The documented CLI
/// contract nevertheless reports quarantine as exit 21, including when a later
/// idempotent replay or a resume returns the same retained quarantine.
fn quarantine_message(outcome: IngestOutcome, quarantine_retained: bool) -> Option<&'static str> {
    match outcome {
        IngestOutcome::QuarantinedCorrupt => Some("the intake was quarantined as a corrupt PDF"),
        IngestOutcome::QuarantinedEncrypted => {
            Some("the intake was quarantined as an encrypted PDF")
        }
        IngestOutcome::QuarantinedUnsupported => {
            Some("the intake was quarantined as an unsupported PDF")
        }
        IngestOutcome::QuarantinedSuspicious => {
            Some("the intake was quarantined as a suspicious PDF")
        }
        IngestOutcome::QuarantinedLimit => Some("the intake was quarantined by an intake limit"),
        IngestOutcome::AcceptedNew
        | IngestOutcome::AcceptedDuplicate
        | IngestOutcome::IdempotentReplay
        | IngestOutcome::Interrupted
        | IngestOutcome::DeniedConflict => {
            quarantine_retained.then_some("the intake remains quarantined from an earlier attempt")
        }
    }
}

fn require_unquarantined_receipt(receipt: &IngestReceipt) -> Result<(), Failure> {
    match quarantine_message(receipt.outcome, receipt.quarantine.is_some()) {
        Some(message) => Err(Failure::new(HeleosError::Quarantine, message)),
        None => Ok(()),
    }
}

fn ingest(arguments: IngestArgs) -> Result<Value, Failure> {
    let mut store = Store::open_writer(&arguments.database)?;
    let vault = Vault::open(VaultConfig {
        root: arguments.vault,
        open_mode: VaultOpenMode::CreateOrOpen,
    })?;
    // The intake source is admitted before the sandbox is built so an absent
    // or non-regular source never pays for a wasm instantiation.
    let source = IntakeSource::open(&arguments.pdf)?;
    let mut staging = SandboxStaging::create()?;
    // A refused checkpoint is retained by the guard; no operation is attempted.
    let outcome = staging.checked_path().ok().map(|path| {
        let probe = WasiPdfProbe::new(
            approved_pdf_guest()?,
            PdfSandboxConfig {
                staging_parent: path.to_owned(),
            },
        )?;
        let receipt = IngestEngine::new(&mut store, &vault, &probe, &SystemClock, &RandomIds)?
            .ingest(IngestRequest {
                project_id: arguments.project,
                source,
                idempotency_key: arguments.idempotency_key,
                actor: arguments.actor,
                pdf_limits: PdfLimits::default(),
            })?;
        require_unquarantined_receipt(&receipt)?;
        json(&receipt)
    });
    // Cleanup is checked on both the normal and the handled-error path.
    staging.finish(outcome)
}

fn inspect_foundation(arguments: InspectFoundationArgs) -> Result<Value, Failure> {
    let store = Store::open_read_only(&arguments.database)?;
    let vault = Vault::open(VaultConfig {
        root: arguments.vault,
        open_mode: VaultOpenMode::ExistingOnly,
    })?;
    let inspection = FoundationReader::new(&store, &vault).inspect_foundation(arguments.project)?;
    json(&inspection)
}

#[derive(Serialize)]
struct VerifyView {
    schema: &'static str,
    database: DatabaseIntegrityView,
    audit_chain: heleos_core::AuditChainReport,
    object: Option<VaultVerification>,
    revision: Option<RevisionVerificationView>,
}

#[derive(Serialize)]
struct DatabaseIntegrityView {
    clean: bool,
    integrity_check_violations: Vec<String>,
    integrity_check_truncated: bool,
    quick_check_violations: Vec<String>,
    quick_check_truncated: bool,
    foreign_key_violations: Vec<String>,
    foreign_key_check_truncated: bool,
}

#[derive(Serialize)]
struct RevisionVerificationView {
    evidence: heleos_core::EvidenceManifestReceipt,
    original: VaultVerification,
    manifest: VaultVerification,
}

fn verify(arguments: VerifyArgs) -> Result<Value, Failure> {
    let store = Store::open_read_only(&arguments.database)?;
    let vault = Vault::open(VaultConfig {
        root: arguments.vault,
        open_mode: VaultOpenMode::ExistingOnly,
    })?;
    let integrity = store.verify_integrity()?;
    if !integrity.is_clean() {
        return Err(Failure::new(
            HeleosError::Integrity,
            "the database reported integrity violations",
        ));
    }
    let reader = FoundationReader::new(&store, &vault);
    let audit_chain = reader.verify_audit_chain()?;
    if !audit_chain.valid {
        return Err(Failure::new(
            HeleosError::Integrity,
            "the audit chain did not verify",
        ));
    }

    let object = match arguments.object {
        Some(digest) => Some(require_verified_object(vault.verify(digest)?)?),
        None => None,
    };
    let revision = match arguments.revision {
        Some(digest) => {
            let evidence = reader.evidence_manifest_for_revision(RevisionId::from(digest))?;
            let original =
                require_verified_object(vault.verify(evidence.manifest.original.sha256)?)?;
            let manifest =
                require_verified_object(vault.verify(evidence.manifest_content_sha256)?)?;
            Some(RevisionVerificationView {
                evidence,
                original,
                manifest,
            })
        }
        None => None,
    };

    json(&VerifyView {
        schema: VERIFY_SCHEMA,
        database: DatabaseIntegrityView {
            clean: integrity.is_clean(),
            integrity_check_violations: integrity.integrity_check_violations,
            integrity_check_truncated: integrity.integrity_check_truncated,
            quick_check_violations: integrity.quick_check_violations,
            quick_check_truncated: integrity.quick_check_truncated,
            foreign_key_violations: integrity.foreign_key_violations,
            foreign_key_check_truncated: integrity.foreign_key_check_truncated,
        },
        audit_chain,
        object,
        revision,
    })
}

fn require_verified_object(verification: VaultVerification) -> Result<VaultVerification, Failure> {
    match verification {
        VaultVerification::Verified { .. } => Ok(verification),
        VaultVerification::Missing { .. } => Err(Failure::new(
            HeleosError::NotFound,
            "the vault does not hold the requested object",
        )),
        VaultVerification::Corrupt { .. }
        | VaultVerification::NonRegular { .. }
        | VaultVerification::UnexpectedLinkCount { .. }
        | VaultVerification::PermissionViolation { .. } => Err(Failure::new(
            HeleosError::Integrity,
            "the vault object failed verification",
        )),
    }
}

fn backup_create(arguments: BackupCreateArgs) -> Result<Value, Failure> {
    // Key custody is settled before the writer lock is taken.
    let signing_key = read_signing_key(&arguments.signing_key)?;
    let mut store = Store::open_writer(&arguments.database)?;
    let vault = Vault::open(VaultConfig {
        root: arguments.vault,
        open_mode: VaultOpenMode::ExistingOnly,
    })?;
    let receipt = heleos_core::BackupService::create(
        &mut store,
        &vault,
        &arguments.destination,
        arguments.recipient.as_recipient(),
        &signing_key,
    )?;
    json(&receipt)
}

fn backup_verify(arguments: BackupVerifyArgs) -> Result<Value, Failure> {
    let identity = read_age_identity(&arguments.identity)?;
    let trusted_signer = read_trusted_signer(&arguments.trusted_signer)?;
    // The ciphertext handle is opened once and moved into core; it is never
    // reopened by path, so the authenticated bytes are the admitted bytes.
    let backup = open_regular_nofollow(&arguments.backup)?;
    let report = heleos_core::BackupService::verify_container(backup, &identity, &trusted_signer)?;
    json(&report)
}

fn restore(arguments: RestoreArgs) -> Result<Value, Failure> {
    // `--verify` is the operator's acknowledgement that restore materialises a
    // new Foundation tree. It is a command-line gate only and is never passed
    // into core, which always verifies the container before writing.
    if !arguments.verify {
        return Err(Failure::denied(
            "restore requires the --verify acknowledgement",
        ));
    }
    let identity = read_age_identity(&arguments.identity)?;
    let trusted_signer = read_trusted_signer(&arguments.trusted_signer)?;
    let backup = open_regular_nofollow(&arguments.backup)?;
    let receipt =
        heleos_core::BackupService::restore(backup, &identity, &trusted_signer, &arguments.into)?;
    json(&receipt)
}

fn jobs_resume(arguments: JobsResumeArgs) -> Result<Value, Failure> {
    let mut store = Store::open_writer(&arguments.database)?;
    let vault = Vault::open(VaultConfig {
        root: arguments.vault,
        open_mode: VaultOpenMode::ExistingOnly,
    })?;
    let mut staging = SandboxStaging::create()?;
    // A refused checkpoint is retained by the guard; no operation is attempted.
    let outcome = staging.checked_path().ok().map(|path| {
        let probe = WasiPdfProbe::new(
            approved_pdf_guest()?,
            PdfSandboxConfig {
                staging_parent: path.to_owned(),
            },
        )?;
        let receipt = IngestEngine::new(&mut store, &vault, &probe, &SystemClock, &RandomIds)?
            .resume(arguments.job_id, arguments.actor)?;
        // A resumed job carries the same intake outcome, so a job that resolved
        // to quarantine reaches the operator as exit 21 here too.
        require_unquarantined_receipt(&receipt.receipt)?;
        json(&receipt)
    });
    staging.finish(outcome)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn every_error_variant() -> Vec<HeleosError> {
        vec![
            HeleosError::Io(std::io::Error::from(std::io::ErrorKind::NotFound)),
            HeleosError::InvalidDigest,
            HeleosError::InvalidId,
            HeleosError::Database,
            HeleosError::WriterBusy,
            HeleosError::Migration,
            HeleosError::Integrity,
            HeleosError::CorruptPdf,
            HeleosError::EncryptedPdf,
            HeleosError::UnsupportedPdf,
            HeleosError::SuspiciousPdf,
            HeleosError::ResourceLimit,
            HeleosError::Quota,
            HeleosError::Timeout,
            HeleosError::InvalidStateTransition,
            HeleosError::IdempotencyConflict,
            HeleosError::LeaseUnavailable,
            HeleosError::FaultInjected,
            HeleosError::CommitOutcomeUnknown,
            HeleosError::Quarantine,
            HeleosError::NotFound,
            HeleosError::BackupDecryption,
            HeleosError::BackupIntegrity,
            HeleosError::InvalidBackupContainer,
            HeleosError::PolicyDenied,
            HeleosError::SandboxTrap,
            HeleosError::Serialization(
                serde_json::from_str::<u8>("not json").expect_err("a serde_json error"),
            ),
        ]
    }

    #[test]
    fn every_error_class_maps_to_its_documented_exit_code() {
        // Break caught: a recovery-relevant class silently collapsing into the
        // generic internal-failure code, which would hide it from operators.
        let expected: Vec<(&str, i32)> = vec![
            ("io", EXIT_INTERNAL),
            ("invalid_digest", EXIT_INTERNAL),
            ("invalid_id", EXIT_INTERNAL),
            ("database", EXIT_INTERNAL),
            ("writer_busy", EXIT_DENIED),
            ("migration", EXIT_INTERNAL),
            ("integrity", EXIT_INTEGRITY),
            ("corrupt_pdf", EXIT_INTERNAL),
            ("encrypted_pdf", EXIT_INTERNAL),
            ("unsupported_pdf", EXIT_INTERNAL),
            ("suspicious_pdf", EXIT_INTERNAL),
            ("resource_limit", EXIT_INTERNAL),
            ("quota", EXIT_INTERNAL),
            ("timeout", EXIT_INTERNAL),
            ("invalid_state_transition", EXIT_INTERNAL),
            ("idempotency_conflict", EXIT_DENIED),
            ("lease_unavailable", EXIT_DENIED),
            ("fault_injected", EXIT_INTERNAL),
            ("commit_outcome_unknown", EXIT_INTERNAL),
            ("quarantine", EXIT_QUARANTINE),
            ("not_found", EXIT_NOT_FOUND),
            ("backup_decryption", EXIT_INTEGRITY),
            ("backup_integrity", EXIT_INTEGRITY),
            ("invalid_backup_container", EXIT_INTEGRITY),
            ("policy_denied", EXIT_DENIED),
            ("sandbox_trap", EXIT_INTERNAL),
            ("serialization", EXIT_INTERNAL),
        ];
        let variants = every_error_variant();
        assert_eq!(
            variants.len(),
            expected.len(),
            "the variant inventory and the expectation table must stay aligned"
        );
        for (error, (class, code)) in variants.iter().zip(expected) {
            assert_eq!(class_name(error), class, "class for {error:?}");
            assert_eq!(exit_code(error), code, "exit code for {error:?}");
        }
    }

    #[test]
    fn class_names_are_unique_and_snake_case() {
        // Break caught: two distinct classes sharing one machine-readable name.
        let mut names = every_error_variant()
            .iter()
            .map(|error| class_name(error))
            .collect::<Vec<_>>();
        let total = names.len();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), total);
        for name in names {
            assert!(
                name.bytes()
                    .all(|byte| byte.is_ascii_lowercase() || byte == b'_'),
                "{name} must be snake case"
            );
        }
    }

    #[test]
    fn every_error_message_is_constant_text_without_operands() {
        // Break caught: a display string that starts interpolating an operand,
        // a path, or an underlying error and so leaks it into the diagnostic.
        let expected = [
            "I/O operation failed",
            "invalid SHA-256 digest",
            "invalid identifier",
            "database operation failed",
            "database writer is busy",
            "migration failed",
            "integrity verification failed",
            "corrupt PDF",
            "encrypted PDF",
            "unsupported PDF",
            "suspicious PDF",
            "resource limit exceeded",
            "quota exceeded",
            "operation timed out",
            "invalid job state transition",
            "idempotency conflict",
            "job lease is unavailable",
            "fault injected",
            "transaction commit outcome is unknown",
            "content quarantined",
            "requested item was not found",
            "backup decryption failed",
            "backup integrity verification failed",
            "invalid backup container",
            "operation denied by policy",
            "sandbox trapped",
            "serialization failed",
        ];
        let variants = every_error_variant();
        assert_eq!(variants.len(), expected.len());
        for (error, message) in variants.into_iter().zip(expected) {
            assert_eq!(Failure::from(error).message, message);
        }
    }

    #[test]
    fn hostile_messages_flatten_to_one_bounded_control_free_line() {
        // Break caught: a newline or control byte from `argv` splitting the
        // single-object stderr contract into two lines.
        let flattened = flatten_diagnostic_message("a\nb\r\nc\td\u{7}e");
        assert_eq!(flattened, "a b c d e");
        assert!(!flattened.chars().any(char::is_control));

        let flattened = flatten_diagnostic_message(&"x".repeat(10_000));
        assert!(flattened.chars().count() <= DIAGNOSTIC_MESSAGE_MAX_CHARS + 3);
        assert!(flattened.ends_with("..."));

        assert_eq!(
            flatten_diagnostic_message("   \n\t  "),
            "the command line was rejected"
        );
        assert_eq!(
            flatten_diagnostic_message(""),
            "the command line was rejected"
        );
        // Non-ASCII text survives; `serde_json` handles the escaping.
        assert_eq!(
            flatten_diagnostic_message(" \u{4e2d}\u{6587} "),
            "\u{4e2d}\u{6587}"
        );
    }

    #[test]
    fn diagnostics_are_exactly_one_json_object_per_failure() {
        let diagnostic = serde_json::json!({
            "schema": DIAGNOSTIC_SCHEMA,
            "command": Some("db.migrate"),
            "class": class_name(&HeleosError::PolicyDenied),
            "exit_code": EXIT_DENIED,
            "message": flatten_diagnostic_message("bad\nline \"quoted\" \\ escaped"),
        });
        let rendered = serde_json::to_string(&diagnostic).expect("render the diagnostic");
        assert_eq!(rendered.lines().count(), 1);
        let parsed: Value = serde_json::from_str(&rendered).expect("round trip the diagnostic");
        assert_eq!(parsed["exit_code"], EXIT_DENIED);
        assert_eq!(parsed["message"], "bad line \"quoted\" \\ escaped");
    }

    #[test]
    fn the_embedded_guest_matches_its_tracked_manifest() {
        require_embedded_guest_binding(APPROVED_PDF_GUEST, APPROVED_PDF_GUEST_MANIFEST)
            .expect("the build-embedded guest and manifest must agree");
        approved_pdf_guest().expect("the approved guest trust root must load");
    }

    #[test]
    fn a_mismatched_guest_manifest_is_refused() {
        // Break caught: an embedded wasm blob that no longer matches the
        // reviewed manifest still reaching the sandbox as a trust root.
        let mut wasm = APPROVED_PDF_GUEST.to_vec();
        wasm.push(0);
        let failure = require_embedded_guest_binding(&wasm, APPROVED_PDF_GUEST_MANIFEST)
            .expect_err("a longer wasm blob must be refused");
        assert_eq!(exit_code(&failure.error), EXIT_INTEGRITY);

        let mutated = APPROVED_PDF_GUEST_MANIFEST.replace(
            "schema = \"heleos.pdf-guest-manifest/v1\"",
            "schema = \"heleos.pdf-guest-manifest/v0\"",
        );
        assert_ne!(mutated, APPROVED_PDF_GUEST_MANIFEST);
        assert!(require_embedded_guest_binding(APPROVED_PDF_GUEST, &mutated).is_err());

        assert!(require_embedded_guest_binding(APPROVED_PDF_GUEST, "").is_err());
    }

    #[test]
    fn manifest_fields_are_read_from_the_top_level_table_only() {
        let manifest =
            "schema = \"a\"\nwasm_byte_length = 12\n\n[[dependencies]]\nschema = \"b\"\n";
        assert_eq!(manifest_field(manifest, "schema"), Some("a"));
        assert_eq!(manifest_field(manifest, "wasm_byte_length"), Some("12"));
        assert_eq!(manifest_field(manifest, "absent"), None);
    }

    fn every_ingest_outcome() -> Vec<IngestOutcome> {
        vec![
            IngestOutcome::AcceptedNew,
            IngestOutcome::AcceptedDuplicate,
            IngestOutcome::IdempotentReplay,
            IngestOutcome::QuarantinedCorrupt,
            IngestOutcome::QuarantinedEncrypted,
            IngestOutcome::QuarantinedUnsupported,
            IngestOutcome::QuarantinedSuspicious,
            IngestOutcome::QuarantinedLimit,
            IngestOutcome::Interrupted,
            IngestOutcome::DeniedConflict,
        ]
    }

    #[test]
    fn every_quarantined_outcome_reaches_the_operator_as_the_quarantine_class() {
        // Break caught: a durable quarantine receipt being serialised into a
        // success envelope, which reports exit 0 for content Heleos refused.
        let quarantined = [
            IngestOutcome::QuarantinedCorrupt,
            IngestOutcome::QuarantinedEncrypted,
            IngestOutcome::QuarantinedUnsupported,
            IngestOutcome::QuarantinedSuspicious,
            IngestOutcome::QuarantinedLimit,
        ];
        for outcome in every_ingest_outcome() {
            let expected = quarantined.contains(&outcome);
            assert_eq!(
                quarantine_message(outcome, false).is_some(),
                expected,
                "fresh {outcome:?}"
            );
            // A replay or a resume that returns a retained quarantine record is
            // still a quarantine for the operator, whatever its outcome name.
            assert!(
                quarantine_message(outcome, true).is_some(),
                "retained quarantine for {outcome:?}"
            );
        }
    }

    #[test]
    fn quarantine_messages_are_distinct_constant_sentences() {
        let mut messages = every_ingest_outcome()
            .into_iter()
            .flat_map(|outcome| quarantine_message(outcome, true))
            .collect::<Vec<_>>();
        messages.sort_unstable();
        messages.dedup();
        assert_eq!(messages.len(), 6, "one sentence per quarantine reason");
        for message in messages {
            assert!(!message.is_empty());
            assert!(!message.chars().any(char::is_control));
        }
    }

    #[test]
    fn usage_diagnostics_render_fixed_text_for_every_clap_kind() {
        // Break caught: a parser rejection echoing the offending operand, which
        // may be an identity supplied where a public recipient belongs.
        use clap::error::ErrorKind;

        let secret = "AGE-SECRET-KEY-1EXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXA";
        let error = Cli::try_parse_from([
            "heleos",
            "backup",
            "create",
            "/out.age",
            "--database",
            "/db",
            "--vault",
            "/v",
            "--recipient",
            secret,
            "--signing-key",
            "/s",
        ])
        .expect_err("an identity is not a public recipient");
        assert!(
            error.render().to_string().contains(secret),
            "clap's own rendering echoes the operand, which is why it is unused"
        );
        let message = usage_message(error.kind());
        assert!(!message.contains(secret));
        assert!(!message.contains("AGE-SECRET-KEY-"));

        for kind in [
            ErrorKind::InvalidValue,
            ErrorKind::UnknownArgument,
            ErrorKind::InvalidSubcommand,
            ErrorKind::NoEquals,
            ErrorKind::ValueValidation,
            ErrorKind::TooManyValues,
            ErrorKind::TooFewValues,
            ErrorKind::WrongNumberOfValues,
            ErrorKind::ArgumentConflict,
            ErrorKind::MissingRequiredArgument,
            ErrorKind::MissingSubcommand,
            ErrorKind::InvalidUtf8,
            ErrorKind::DisplayHelp,
            ErrorKind::DisplayHelpOnMissingArgumentOrSubcommand,
            ErrorKind::DisplayVersion,
            ErrorKind::Io,
            ErrorKind::Format,
        ] {
            let message = usage_message(kind);
            assert!(!message.is_empty(), "{kind:?} must name a fixed sentence");
            assert!(!message.chars().any(char::is_control));
        }
    }

    #[test]
    fn duplicate_flag_diagnostics_name_only_declared_flags() {
        assert_eq!(
            duplicate_flag_message(DuplicateFlag::Declared("--database")),
            "the option --database was supplied more than once"
        );
        assert_eq!(
            duplicate_flag_message(DuplicateFlag::Undeclared),
            "an option was supplied more than once"
        );
    }

    const fn facts(
        is_regular: bool,
        is_reparse: bool,
        links: Option<u64>,
        mode: Option<u32>,
    ) -> AdmittedFileFacts {
        AdmittedFileFacts {
            is_regular,
            is_reparse,
            links,
            mode,
        }
    }

    #[test]
    fn admitted_handle_policies_reject_reparse_multilink_and_widened_key_material() {
        // Break caught: a hard-linked, reparse-backed, or group-readable key
        // file passing the custody predicate its platform applies.
        assert!(unix_key_facts_are_private(facts(
            true,
            false,
            Some(1),
            Some(0o600)
        )));
        assert!(!unix_key_facts_are_private(facts(
            true,
            false,
            Some(2),
            Some(0o600)
        )));
        assert!(!unix_key_facts_are_private(facts(
            true,
            false,
            Some(1),
            Some(0o640)
        )));
        assert!(!unix_key_facts_are_private(facts(
            false,
            false,
            Some(1),
            Some(0o600)
        )));
        assert!(!unix_key_facts_are_private(facts(
            true,
            true,
            Some(1),
            Some(0o600)
        )));
        assert!(!unix_key_facts_are_private(facts(
            true,
            false,
            None,
            Some(0o600)
        )));

        assert!(windows_key_facts_are_admissible(facts(
            true,
            false,
            Some(1),
            None
        )));
        assert!(!windows_key_facts_are_admissible(facts(
            true,
            true,
            Some(1),
            None
        )));
        assert!(!windows_key_facts_are_admissible(facts(
            true,
            false,
            Some(2),
            None
        )));
        assert!(!windows_key_facts_are_admissible(facts(
            true, false, None, None
        )));
        assert!(!windows_key_facts_are_admissible(facts(
            false,
            false,
            Some(1),
            None
        )));
    }

    fn allow_ace(sid: &str) -> WindowsAce {
        WindowsAce {
            sid: sid.to_owned(),
            is_access_allow: true,
            flags: 0,
            mask: WINDOWS_FILE_ALL_ACCESS,
        }
    }

    #[test]
    fn the_windows_private_policy_admits_exactly_the_process_and_system_principals() {
        let user = "S-1-5-21-1-2-3-1001";
        assert_eq!(
            windows_allowed_sids_for_process_sid(user),
            [user.to_owned(), WINDOWS_SYSTEM_SID.to_owned()]
        );
        assert_eq!(
            windows_allowed_sids_for_process_sid(WINDOWS_SYSTEM_SID),
            [WINDOWS_SYSTEM_SID.to_owned()]
        );
        assert_eq!(
            windows_private_dacl_sddl(&windows_allowed_sids_for_process_sid(user)),
            "D:P(A;;FA;;;S-1-5-21-1-2-3-1001)(A;;FA;;;S-1-5-18)"
        );
    }

    #[test]
    fn only_a_protected_non_empty_dacl_is_accepted() {
        // Break caught: an inheritable or unprotected DACL, where a later
        // parent change could silently widen access to key material.
        assert!(windows_dacl_is_protected("D:P(A;;FA;;;S-1-5-18)"));
        assert!(windows_dacl_is_protected("D:PAI(A;;FA;;;S-1-5-18)"));
        for rejected in [
            "D:(A;;FA;;;S-1-5-18)",
            "D:AI(A;;FA;;;S-1-5-18)",
            "D:PP(A;;FA;;;S-1-5-18)",
            "D:P",
            "D:",
            "O:BAD:P(A;;FA;;;S-1-5-18)",
            "",
        ] {
            assert!(!windows_dacl_is_protected(rejected), "{rejected}");
        }
    }

    #[test]
    fn the_windows_dacl_entry_policy_rejects_extra_deny_inherited_and_widened_entries() {
        let allowed = windows_allowed_sids_for_process_sid("S-1-5-21-1-2-3-1001");
        let exact = allowed.iter().map(|sid| allow_ace(sid)).collect::<Vec<_>>();
        assert!(windows_dacl_entries_match_exact_policy(&exact, &allowed));

        let mut extra = exact.clone();
        extra.push(allow_ace("S-1-1-0"));
        assert!(!windows_dacl_entries_match_exact_policy(&extra, &allowed));

        let mut duplicated = exact.clone();
        duplicated[1] = allow_ace(&allowed[0]);
        assert!(!windows_dacl_entries_match_exact_policy(
            &duplicated,
            &allowed
        ));

        let mut denied = exact.clone();
        denied[0].is_access_allow = false;
        assert!(!windows_dacl_entries_match_exact_policy(&denied, &allowed));

        let mut inherited = exact.clone();
        inherited[0].flags = 0x10;
        assert!(!windows_dacl_entries_match_exact_policy(
            &inherited, &allowed
        ));

        let mut narrowed = exact.clone();
        narrowed[0].mask = 0x0012_0089;
        assert!(!windows_dacl_entries_match_exact_policy(
            &narrowed, &allowed
        ));

        assert!(!windows_dacl_entries_match_exact_policy(
            &exact[..1],
            &allowed
        ));
        assert!(!windows_dacl_entries_match_exact_policy(&[], &allowed));
    }

    #[test]
    fn the_windows_shared_parent_owner_policy_accepts_only_the_machine_principals() {
        let user = "S-1-5-21-1-2-3-1001";
        assert!(windows_shared_parent_owner_is_admissible(user, user));
        assert!(windows_shared_parent_owner_is_admissible(
            WINDOWS_SYSTEM_SID,
            user
        ));
        assert!(windows_shared_parent_owner_is_admissible(
            WINDOWS_ADMINISTRATORS_SID,
            user
        ));
        assert!(!windows_shared_parent_owner_is_admissible(
            "S-1-5-21-1-2-3-1002",
            user
        ));
        assert!(!windows_shared_parent_owner_is_admissible("S-1-1-0", user));
    }

    fn parent_allow(sid: &str, flags: u8, mask: u32) -> WindowsParentAce {
        WindowsParentAce::Allow {
            flags,
            mask,
            trustee_sid: sid.to_owned(),
        }
    }

    #[test]
    fn the_shared_parent_flat_ace_preflight_admits_only_the_bounded_grammar() {
        // Break caught: reaching direct ACL access for a callback, object,
        // audit, malformed, or otherwise unparseable parent descriptor.
        let parsed = parse_windows_parent_dacl_sddl(
            "D:PAI(A;OICIID;FA;;;S-1-5-18)(D;;0x1301bf;;;S-1-1-0)(A;;0x7800003F;;;S-1-3-0)",
        )
        .expect("a flat A/D revision-2 DACL parses");
        assert_eq!(
            parsed,
            vec![
                ParsedParentAce {
                    kind: ParsedParentAceKind::Allow,
                    flags: WINDOWS_ACE_OBJECT_INHERIT | WINDOWS_ACE_CONTAINER_INHERIT | 0x10,
                },
                ParsedParentAce {
                    kind: ParsedParentAceKind::Deny,
                    flags: 0,
                },
                ParsedParentAce {
                    kind: ParsedParentAceKind::Allow,
                    flags: 0,
                },
            ]
        );
        assert_eq!(
            parse_windows_parent_dacl_sddl("D:").expect("an empty DACL parses"),
            Vec::new()
        );

        for refused in [
            // Wrong or absent leading section.
            "",
            "D",
            "O:BAD:(A;;FA;;;S-1-5-18)",
            "S:(AU;;FA;;;S-1-1-0)",
            // Unknown, duplicated, or malformed ACL flags.
            "D:PP(A;;FA;;;S-1-5-18)",
            "D:X(A;;FA;;;S-1-5-18)",
            // Callback, object, and audit record types.
            "D:(XA;;FA;;;S-1-1-0;(Member_of{SID(BA)}))",
            "D:(OA;;FA;bf967aba-0de6-11d0-a285-00aa003049e2;;S-1-1-0)",
            "D:(AU;;FA;;;S-1-1-0)",
            // Non-empty GUID fields and wrong field counts.
            "D:(A;;FA;bf967aba-0de6-11d0-a285-00aa003049e2;;S-1-1-0)",
            "D:(A;;FA;;;;S-1-1-0)",
            "D:(A;;FA;;;)",
            // Unknown or duplicated ACE flags.
            "D:(A;ZZ;FA;;;S-1-5-18)",
            "D:(A;CICI;FA;;;S-1-5-18)",
            // Lowercase or empty trustee, inner parenthesis, and trailing bytes.
            "D:(A;;FA;;;s-1-5-18)",
            "D:(A;;FA;;;)",
            "D:(A;;FA;;;S-1-5-18(",
            "D:(A;;FA;;;S-1-5-18)x",
            "D:(A;;FA;;;S-1-5-18",
            // Non-ASCII bytes anywhere in the descriptor.
            "D:(A;;FA;;;S-1-5-18)\u{4e2d}",
        ] {
            assert!(
                parse_windows_parent_dacl_sddl(refused).is_err(),
                "{refused:?} must be refused before direct ACL access"
            );
        }
    }

    #[test]
    fn the_shared_parent_dacl_policy_rejects_effective_and_inheritable_danger() {
        // Break caught: admitting a shared parent whose DACL lets an untrusted
        // principal delete, re-permission, or inherit write access into the
        // child this process is about to create — which hardening the child
        // afterwards cannot revoke.
        let user = "S-1-5-21-1-2-3-1001";
        let other = "S-1-5-21-1-2-3-1002";
        let inheritable = WINDOWS_ACE_CONTAINER_INHERIT | WINDOWS_ACE_OBJECT_INHERIT;

        // A conventional namespace-safe shared default: trusted full control,
        // an inert deny, a harmless untrusted traverse/list grant, and the
        // narrow inherit-only CREATOR OWNER template.
        let safe = vec![
            parent_allow(user, 0, WINDOWS_FILE_ALL_ACCESS),
            parent_allow(WINDOWS_SYSTEM_SID, 0, WINDOWS_FILE_ALL_ACCESS),
            parent_allow(WINDOWS_ADMINISTRATORS_SID, 0, WINDOWS_FILE_ALL_ACCESS),
            WindowsParentAce::Deny,
            parent_allow("S-1-5-11", 0, 0x0010_0001),
            parent_allow(
                WINDOWS_CREATOR_OWNER_SID,
                inheritable | WINDOWS_ACE_INHERIT_ONLY,
                WINDOWS_FILE_ALL_ACCESS,
            ),
            // Inherit-only with no inheritance flag grants nothing at all: it
            // is neither effective on the parent nor propagated to the child.
            parent_allow(other, WINDOWS_ACE_INHERIT_ONLY, WINDOWS_FILE_ALL_ACCESS),
        ];
        assert!(windows_shared_parent_dacl_is_namespace_safe(user, &safe));
        assert!(windows_shared_parent_policy_accepts(user, user, &safe));

        for (label, ace) in [
            // Effective on the parent itself.
            ("delete child", parent_allow(other, 0, 0x0000_0040)),
            ("delete", parent_allow(other, 0, 0x0001_0000)),
            ("write dac", parent_allow(other, 0, 0x0004_0000)),
            ("write owner", parent_allow(other, 0, 0x0008_0000)),
            ("generic all", parent_allow(other, 0, 0x1000_0000)),
            // Inheritable into the child.
            (
                "inheritable generic write",
                parent_allow(other, inheritable, 0x4000_0000),
            ),
            (
                "inheritable add file",
                parent_allow(other, WINDOWS_ACE_CONTAINER_INHERIT, 0x0000_0002),
            ),
            (
                "inheritable add subdirectory",
                parent_allow(other, WINDOWS_ACE_CONTAINER_INHERIT, 0x0000_0004),
            ),
            (
                "inherit-only write attributes",
                parent_allow(other, inheritable | WINDOWS_ACE_INHERIT_ONLY, 0x0000_0100),
            ),
            (
                "no-propagate inheritable write ea",
                parent_allow(other, WINDOWS_ACE_OBJECT_INHERIT | 0x04, 0x0000_0010),
            ),
            // CREATOR GROUP is never trusted, even under the narrow shape.
            (
                "creator group template",
                parent_allow(
                    "S-1-3-1",
                    inheritable | WINDOWS_ACE_INHERIT_ONLY,
                    WINDOWS_FILE_ALL_ACCESS,
                ),
            ),
            // CREATOR OWNER is trusted only as a pure inherit-only template.
            (
                "effective creator owner",
                parent_allow(WINDOWS_CREATOR_OWNER_SID, 0, WINDOWS_FILE_ALL_ACCESS),
            ),
        ] {
            let mut hostile = safe.clone();
            hostile.push(ace);
            assert!(
                !windows_shared_parent_dacl_is_namespace_safe(user, &hostile),
                "{label} must deny the shared parent"
            );
            assert!(
                !windows_shared_parent_policy_accepts(user, user, &hostile),
                "{label} must deny the shared parent"
            );
        }

        // Owner and DACL are independent halves: neither admits a parent alone.
        assert!(!windows_shared_parent_policy_accepts(other, user, &safe));
        let mut effectively_hostile = safe.clone();
        effectively_hostile.push(parent_allow(other, 0, 0x0004_0000));
        assert!(windows_shared_parent_owner_is_admissible(user, user));
        assert!(!windows_shared_parent_policy_accepts(
            user,
            user,
            &effectively_hostile
        ));
    }

    #[cfg(unix)]
    #[test]
    fn the_shared_temporary_parent_policy_matches_the_retained_owner_and_mode_rule() {
        // Break caught: accepting a group-writable shared parent without the
        // sticky bit, where another principal could rename our child away.
        assert!(shared_temporary_parent_is_admissible(1000, 0o700, 1000));
        assert!(shared_temporary_parent_is_admissible(0, 0o1777, 1000));
        assert!(shared_temporary_parent_is_admissible(1000, 0o1777, 1000));
        assert!(!shared_temporary_parent_is_admissible(1001, 0o700, 1000));
        assert!(!shared_temporary_parent_is_admissible(1000, 0o777, 1000));
        assert!(!shared_temporary_parent_is_admissible(1000, 0o770, 1000));
        assert!(!shared_temporary_parent_is_admissible(0, 0o777, 1000));
    }

    struct UnitScratch {
        root: PathBuf,
    }

    impl UnitScratch {
        fn new() -> Self {
            let root = std::env::temp_dir().join(format!("heleos-unit-{}", uuid::Uuid::new_v4()));
            std::fs::create_dir(&root).expect("create the scratch child");
            heleos_core::apply_private_permissions(&root).expect("harden the scratch child");
            Self { root }
        }

        fn private_file(&self, name: &str, bytes: &[u8]) -> PathBuf {
            let path = self.root.join(name);
            std::fs::write(&path, bytes).expect("write the scratch file");
            heleos_core::apply_private_permissions(&path).expect("harden the scratch file");
            path
        }
    }

    impl Drop for UnitScratch {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    #[test]
    fn key_material_is_admitted_only_at_exactly_thirty_two_bytes() {
        // Break caught: a truncated or padded key file silently producing a
        // different signing key than the operator intends.
        let scratch = UnitScratch::new();
        for length in [0_usize, 1, 31, 33, 64] {
            let path = scratch.private_file(&format!("k{length}"), &vec![7_u8; length]);
            let failure =
                read_signing_key(&path).expect_err("only exactly 32 bytes may be admitted");
            assert_eq!(exit_code(&failure.error), EXIT_DENIED);
            assert!(read_trusted_signer(&path).is_err());
        }
        let path = scratch.private_file("k32", &[7_u8; 32]);
        let key = read_signing_key(&path).expect("exactly 32 bytes is admitted");
        assert_eq!(key.to_bytes(), [7_u8; 32]);
        assert_eq!(read_trusted_signer(&path).expect("32 bytes"), [7_u8; 32]);
    }

    #[cfg(unix)]
    #[test]
    fn key_material_must_be_private_and_direct() {
        use std::os::unix::fs::PermissionsExt;

        let scratch = UnitScratch::new();
        let widened = scratch.private_file("widened", &[1_u8; 32]);
        std::fs::set_permissions(&widened, std::fs::Permissions::from_mode(0o644))
            .expect("widen the key file");
        let failure = read_signing_key(&widened).expect_err("world-readable keys are refused");
        assert_eq!(exit_code(&failure.error), EXIT_DENIED);

        let target = scratch.private_file("target", &[2_u8; 32]);
        let link = scratch.root.join("link");
        std::os::unix::fs::symlink(&target, &link).expect("create the symlink");
        assert!(read_signing_key(&link).is_err(), "the open is no-follow");

        assert!(
            read_signing_key(&scratch.root).is_err(),
            "a directory is not a key"
        );
        assert!(
            read_signing_key(Path::new("/dev/null")).is_err(),
            "a device is not a key"
        );
    }

    #[test]
    fn identities_are_admitted_only_as_a_single_age_x25519_secret() {
        use age::secrecy::ExposeSecret;

        let scratch = UnitScratch::new();
        let identity = age::x25519::Identity::generate();
        let secret = identity.to_string();
        let text = secret.expose_secret();
        let path = scratch.private_file("good.age", text.as_bytes());
        let Ok(parsed) = read_age_identity(&path) else {
            panic!("a single identity is admitted");
        };
        assert_eq!(
            parsed.to_public().to_string(),
            identity.to_public().to_string()
        );

        for (name, bytes) in [
            ("empty.age", Vec::new()),
            ("garbage.age", b"not-an-identity".to_vec()),
            ("ssh.age", b"-----BEGIN OPENSSH PRIVATE KEY-----".to_vec()),
            ("two.age", format!("{text}\n{text}\n").into_bytes()),
            ("huge.age", vec![b'a'; IDENTITY_FILE_MAX_BYTES + 1]),
        ] {
            let path = scratch.private_file(name, &bytes);
            let Err(failure) = read_age_identity(&path) else {
                panic!("{name} must be refused");
            };
            assert_eq!(exit_code(&failure.error), EXIT_DENIED, "{name}");
        }
    }

    #[test]
    fn the_sandbox_staging_child_is_private_unpredictable_and_self_cleaning() {
        // Break caught: reusing a predictable staging name, or leaving the
        // child behind after a clean exit.
        let mut first = SandboxStaging::create().expect("create the first staging child");
        let mut second = SandboxStaging::create().expect("create the second staging child");
        let first_path = first
            .checked_path()
            .expect("the first staging path is bound to its retained parent")
            .to_owned();
        let second_path = second
            .checked_path()
            .expect("the second staging path is bound to its retained parent")
            .to_owned();
        assert_ne!(first_path, second_path);
        assert!(first_path.is_dir());
        heleos_core::verify_private_permissions(&first_path)
            .expect("the staging child must be private");
        let retained = first_path;
        drop(first);
        assert!(
            !retained.exists(),
            "the destructor fallback removes an unsettled child"
        );
        let retained = second_path;
        drop(second);
        assert!(!retained.exists());
    }

    #[test]
    fn checked_cleanup_removes_the_child_and_preserves_the_command_result() {
        let mut staging = SandboxStaging::create().expect("create the staging child");
        let retained = staging
            .checked_path()
            .expect("the fresh staging path is bound to its retained parent")
            .to_owned();
        let result = staging
            .finish(Ok(Value::from("done")))
            .expect("a clean command keeps its result");
        assert_eq!(result, Value::from("done"));
        assert!(!retained.exists(), "checked cleanup removes the child");
    }

    #[test]
    fn a_handled_failure_still_cleans_up_and_keeps_its_own_cause() {
        // Break caught: a handled error path leaving its private staging tree
        // behind, or the cleanup replacing the operator's actual diagnosis.
        let mut staging = SandboxStaging::create().expect("create the staging child");
        let retained = staging
            .checked_path()
            .expect("the fresh staging path is bound to its retained parent")
            .to_owned();
        let failure = staging
            .finish(Err(Failure::new(HeleosError::NotFound, "absent")))
            .expect_err("the command failure survives cleanup");
        assert_eq!(exit_code(&failure.error), EXIT_NOT_FOUND);
        assert_eq!(failure.message, "absent");
        assert!(!retained.exists());
    }

    #[test]
    fn an_uncertain_commit_preserves_its_staging_tree_and_its_own_class() {
        // Break caught: cleanup destroying evidence an operator still needs
        // after a commit whose outcome the store could not establish.
        let mut staging = SandboxStaging::create().expect("create the staging child");
        let retained = staging
            .checked_path()
            .expect("the fresh staging path is bound to its retained parent")
            .to_owned();
        let failure = staging
            .finish(Err(Failure::from(HeleosError::CommitOutcomeUnknown)))
            .expect_err("an uncertain commit stays a failure");
        assert_eq!(exit_code(&failure.error), EXIT_INTERNAL);
        assert_eq!(class_name(&failure.error), "commit_outcome_unknown");
        assert!(
            retained.is_dir(),
            "uncertain state must be preserved, not removed"
        );
        std::fs::remove_dir_all(&retained).expect("retire the preserved fixture tree");
    }

    #[test]
    fn a_cleanup_failure_outranks_a_simultaneous_operation_failure() {
        // Break caught: an operation error concealing that its staging cleanup
        // also failed, so the operator never learns state may have been left
        // behind. This is the combination neither single-fault test covers.
        let mut staging = SandboxStaging::create().expect("create the staging child");
        let retained = staging
            .checked_path()
            .expect("the fresh staging path is bound to its retained parent")
            .to_owned();
        // A rebound identity is exactly what a checked cleanup must refuse.
        staging.identity = RetainedIdentity {
            volume: u64::MAX,
            object: u64::MAX,
        };
        let failure = staging
            .finish(Err(Failure::new(HeleosError::NotFound, "absent")))
            .expect_err("both failures cannot produce a success");
        assert_eq!(
            exit_code(&failure.error),
            EXIT_DENIED,
            "the cleanup failure, not the operation failure, reaches the operator"
        );
        assert_ne!(failure.message, "absent");
        assert!(
            retained.is_dir(),
            "a refused cleanup preserves the tree rather than forcing a removal"
        );
        std::fs::remove_dir_all(&retained).expect("retire the preserved fixture tree");
    }

    #[test]
    fn an_uncertain_commit_outranks_even_a_cleanup_that_would_have_refused() {
        // Break caught: cleanup precedence being applied above preservation, so
        // an uncertain commit loses its own class and its evidence tree.
        let mut staging = SandboxStaging::create().expect("create the staging child");
        let retained = staging
            .checked_path()
            .expect("the fresh staging path is bound to its retained parent")
            .to_owned();
        staging.identity = RetainedIdentity {
            volume: u64::MAX,
            object: u64::MAX,
        };
        let failure = staging
            .finish(Err(Failure::from(HeleosError::CommitOutcomeUnknown)))
            .expect_err("an uncertain commit stays a failure");
        assert_eq!(class_name(&failure.error), "commit_outcome_unknown");
        assert!(retained.is_dir(), "uncertain state is preserved first");
        std::fs::remove_dir_all(&retained).expect("retire the preserved fixture tree");
    }

    /// Builds a bare provisional guard over a freshly created staging name,
    /// exactly as `SandboxStaging::create` does before it proves anything.
    fn provisional_fixture() -> (ProvisionalStagingChild, PathBuf) {
        let principal = process_principal().expect("establish the process principal");
        let parent = RetainedTemporaryParent::admit(&principal).expect("admit the shared parent");
        let name =
            create_unused_private_child(parent.directory()).expect("create the staging name");
        let path = parent.path().join(&name);
        (ProvisionalStagingChild::new(parent, principal, name), path)
    }

    #[test]
    fn a_construction_failure_without_creation_identity_preserves_the_name() {
        // Break caught: treating a missing creation identity as permission to
        // delete, so a failure that occurred before any handle existed removes
        // whatever the name happens to denote now.
        let (guard, path) = provisional_fixture();
        assert!(path.is_dir(), "the staging name exists before the failure");
        let failure = guard.settle(Failure::new(HeleosError::NotFound, "primary"));
        assert_eq!(
            exit_code(&failure.error),
            EXIT_DENIED,
            "cleanup uncertainty is reported above the primary error"
        );
        assert_eq!(failure.message, STAGING_IDENTITY_UNPROVEN);
        assert!(
            path.is_dir(),
            "an unproven name is preserved rather than removed"
        );
        std::fs::remove_dir_all(&path).expect("retire the preserved fixture tree");
    }

    #[test]
    fn a_retained_child_handle_still_supplies_the_identity_cleanup_needs() {
        // Break caught: preserving a name that the one retained authority — the
        // child handle this run opened — could still have proven, or removing
        // it without ever consulting that handle.
        let (mut guard, path) = provisional_fixture();
        let child = open_child_handle(
            guard.parent().expect("retained parent").directory(),
            guard.name(),
        )
        .expect("open the staging child");
        // The identity is deliberately never recorded: cleanup must recover it
        // from the retained handle itself, not from the name it will reopen.
        guard.record_child(child);
        let failure = guard.settle(Failure::new(HeleosError::Integrity, "primary"));
        assert_eq!(
            failure.message, "primary",
            "a certain cleanup returns the primary cause"
        );
        assert!(!path.exists(), "the proven child is removed");
    }

    #[test]
    fn the_retained_parent_binds_its_captured_name_to_its_own_identity() {
        // Break caught: retaining a canonical parent path that is never proven
        // to denote the retained parent, so the path handed to the sandbox is
        // not bound to this run's authority.
        let principal = process_principal().expect("establish the process principal");
        let parent = RetainedTemporaryParent::admit(&principal).expect("admit the shared parent");
        parent
            .recheck(&principal)
            .expect("the captured name denotes the retained parent");
        // A synthetic retained identity stands in for a parent that no longer
        // denotes the admitted object; no namespace is swapped to produce it.
        let rebound = RetainedTemporaryParent {
            directory: parent
                .directory()
                .try_clone()
                .expect("clone the retained parent capability"),
            path: parent.path().to_owned(),
            identity: RetainedIdentity {
                volume: u64::MAX,
                object: u64::MAX,
            },
            marker: parent.marker.clone(),
        };
        let failure = rebound
            .recheck(&principal)
            .expect_err("a parent that is not the retained object is refused");
        assert_eq!(exit_code(&failure.error), EXIT_DENIED);
        assert_eq!(failure.message, SHARED_PARENT_REBOUND);
    }

    #[test]
    fn the_path_handed_to_the_sandbox_is_refused_once_its_parent_is_unbound() {
        // Break caught: handing the sandbox a staging path whose retained
        // parent authority no longer holds, so what the sandbox opens by path
        // is no longer what this run admitted.
        let mut staging = SandboxStaging::create().expect("create the staging child");
        let retained = staging
            .checked_path()
            .expect("the fresh staging path is bound to its retained parent")
            .to_owned();
        staging.parent.identity = RetainedIdentity {
            volume: u64::MAX,
            object: u64::MAX,
        };
        let failure = staging
            .checked_path()
            .expect_err("an unbound parent must not hand out the staging path");
        assert_eq!(exit_code(&failure.error), EXIT_DENIED);
        assert_eq!(failure.message, SHARED_PARENT_REBOUND);
        let failure = staging
            .finish(Ok(Value::Null))
            .expect_err("the same checkpoint refuses the removal");
        assert_eq!(exit_code(&failure.error), EXIT_DENIED);
        assert!(
            retained.is_dir(),
            "a refused checkpoint preserves the child rather than removing it"
        );
        std::fs::remove_dir_all(&retained).expect("retire the preserved fixture tree");
    }

    #[test]
    fn a_handoff_refusal_survives_finish_after_the_parent_is_valid_again() {
        // Break caught: a later valid checkpoint erasing the first refusal,
        // either by deleting the child or by returning a different result.
        for outcome in [
            Some(Ok(Value::Null)),
            Some(Err(Failure::new(
                HeleosError::NotFound,
                "later operation error",
            ))),
            Some(Err(Failure::from(HeleosError::CommitOutcomeUnknown))),
            None,
        ] {
            let uncertain = matches!(
                &outcome,
                Some(Err(failure)) if matches!(failure.error, HeleosError::CommitOutcomeUnknown)
            );
            let (staging, scratch) = refused_handoff_with_valid_parent_again();
            let result = staging.finish(outcome);
            assert!(
                scratch.root.is_dir(),
                "the observed refusal preserves the child"
            );
            let failure = result.expect_err("a refused handoff cannot become success");
            if uncertain {
                assert_eq!(class_name(&failure.error), "commit_outcome_unknown");
            } else {
                assert_eq!(exit_code(&failure.error), EXIT_DENIED);
                assert_eq!(failure.message, SHARED_PARENT_REBOUND);
            }
        }
    }

    #[test]
    fn a_handoff_refusal_survives_drop_after_the_parent_is_valid_again() {
        // Break caught: Drop retrying an already refused authority checkpoint.
        let (staging, scratch) = refused_handoff_with_valid_parent_again();
        drop(staging);
        assert!(
            scratch.root.is_dir(),
            "Drop must retain the preservation decision"
        );
    }

    #[test]
    fn a_handoff_refusal_cannot_be_replaced_by_a_later_valid_handoff() {
        // Break caught: handing out a path after the guard already refused it.
        let (mut staging, scratch) = refused_handoff_with_valid_parent_again();
        let failure = staging
            .checked_path()
            .expect_err("the first refusal is permanent even after valid predicates return");
        assert_eq!(exit_code(&failure.error), EXIT_DENIED);
        assert_eq!(failure.message, SHARED_PARENT_REBOUND);
        drop(staging);
        assert!(scratch.root.is_dir());
    }

    fn refused_handoff_with_valid_parent_again() -> (SandboxStaging, UnitScratch) {
        let mut staging = SandboxStaging::create().expect("create the staging child");
        let scratch = UnitScratch {
            root: staging.path.clone(),
        };
        let identity = staging.parent.identity;
        // Change only synthetic captured state; both filesystem namespaces
        // stay untouched throughout this refusal-then-valid sequence.
        staging.parent.identity = RetainedIdentity {
            volume: identity.volume,
            object: identity.object ^ 1,
        };
        let failure = staging
            .checked_path()
            .expect_err("refuse the synthetic mismatch");
        assert_eq!(exit_code(&failure.error), EXIT_DENIED);
        assert_eq!(failure.message, SHARED_PARENT_REBOUND);
        staging.parent.identity = identity;
        staging
            .parent
            .recheck(&staging.principal)
            .expect("all parent predicates are valid again");
        (staging, scratch)
    }

    #[test]
    fn captured_name_identity_is_required_even_when_retained_authority_is_valid() {
        // Break caught: omitting only the captured-name identity comparison.
        // Separate, owned private directories supply the synthetic path; no
        // filesystem name is replaced or moved, and both policies are valid.
        let retained = UnitScratch::new();
        let captured = UnitScratch::new();
        let principal = process_principal().expect("establish the process principal");
        let directory = open_shared_temporary_parent(&retained.root).expect("open retained parent");
        let identity = retained_identity(&directory).expect("read retained identity");
        let marker = admit_shared_temporary_parent(&directory, &principal).expect("admit parent");
        let other = open_shared_temporary_parent(&captured.root).expect("open captured path");
        recheck_shared_temporary_parent(&other, &principal, &marker)
            .expect("the captured path independently satisfies the same policy");
        assert_ne!(
            retained_identity(&other).expect("read captured identity"),
            identity
        );
        let parent = RetainedTemporaryParent {
            directory,
            path: captured.root.clone(),
            identity,
            marker,
        };
        parent
            .recheck_retained(&principal)
            .expect("retained authority is valid");
        let failure = parent
            .recheck(&principal)
            .expect_err("captured identity differs");
        assert_eq!(exit_code(&failure.error), EXIT_DENIED);
        assert_eq!(failure.message, SHARED_PARENT_REBOUND);
    }

    #[test]
    fn a_binding_refusal_never_deletes_the_unproven_name_it_refused() {
        // Break caught: the destructor removing `name` through the parent after
        // the very check that proved the name no longer denotes our child.
        let (mut guard, path) = provisional_fixture();
        guard.record_identity(RetainedIdentity {
            volume: u64::MAX,
            object: u64::MAX,
        });
        let failure = guard.settle(Failure::new(HeleosError::NotFound, "primary"));
        assert_eq!(
            exit_code(&failure.error),
            EXIT_DENIED,
            "cleanup uncertainty propagates above the primary error"
        );
        assert_ne!(failure.message, "primary");
        assert!(path.is_dir(), "the refused name is preserved");
        std::fs::remove_dir_all(&path).expect("retire the preserved fixture tree");
    }

    #[test]
    fn a_settled_provisional_cleanup_is_never_retried_by_the_destructor() {
        // Break caught: `Drop` making a second removal decision against a
        // namespace that may have changed since the first one refused.
        let (mut guard, path) = provisional_fixture();
        guard.record_identity(RetainedIdentity {
            volume: u64::MAX,
            object: u64::MAX,
        });
        assert!(guard.cleanup().is_err(), "the first decision refuses");
        assert_eq!(
            guard
                .cleanup()
                .expect_err("a settled guard never re-decides")
                .message,
            STAGING_ALREADY_SETTLED
        );
        drop(guard);
        assert!(path.is_dir(), "the destructor honours the refusal");
        std::fs::remove_dir_all(&path).expect("retire the preserved fixture tree");
    }

    #[test]
    fn a_construction_failure_after_hardening_removes_only_the_proven_child() {
        // Break caught: cleanup skipping the retained identity, privacy, or
        // emptiness evidence it had already established before the failure.
        let (mut guard, path) = provisional_fixture();
        let child = open_child_handle(
            guard.parent().expect("retained parent").directory(),
            guard.name(),
        )
        .expect("open the staging child");
        guard.record_child(child);
        let identity = retained_identity(guard.child().expect("retained child"))
            .expect("read the child identity");
        guard.record_identity(identity);
        harden_staging_child(guard.child().expect("retained child"), guard.principal())
            .expect("harden the staging child");
        guard.record_hardened();
        let failure = guard.settle(Failure::new(HeleosError::Integrity, "primary"));
        assert_eq!(failure.message, "primary");
        assert!(!path.exists(), "the proven child is removed");
    }

    #[test]
    fn staging_names_must_be_exactly_one_relative_component() {
        require_one_component("heleos-pdf-1").expect("a single component is accepted");
        for refused in ["", ".", "..", "a/b", "/a", "./a", "a/"] {
            assert!(
                require_one_component(refused).is_err(),
                "{refused:?} is not one component"
            );
        }
    }

    #[test]
    fn a_refused_checked_cleanup_fails_the_command_and_is_never_retried() {
        // Break caught: a command reporting success although its private child
        // could not be removed, or a destructor deleting a tree that checked
        // cleanup had already decided to preserve.
        let mut staging = SandboxStaging::create().expect("create the staging child");
        let retained = staging
            .checked_path()
            .expect("the fresh staging path is bound to its retained parent")
            .to_owned();
        // A rebound name is exactly the case the checked cleanup must refuse.
        staging.identity = RetainedIdentity {
            volume: u64::MAX,
            object: u64::MAX,
        };
        let failure = staging
            .finish(Ok(Value::Null))
            .expect_err("a refused cleanup must not report success");
        assert_eq!(exit_code(&failure.error), EXIT_DENIED);
        assert!(
            retained.is_dir(),
            "a refused cleanup preserves the tree rather than forcing a removal"
        );
        std::fs::remove_dir_all(&retained).expect("retire the preserved fixture tree");
    }

    #[cfg(unix)]
    #[test]
    fn a_fifo_is_refused_without_the_admission_ever_blocking() {
        // Break caught: a FIFO with no writer parking the process inside
        // `open`, before its type could be rejected at all. The work runs on a
        // worker thread with a bounded wait, so a regression is reported as a
        // timeout and no blocked process outlives this test.
        use std::sync::mpsc;
        use std::time::Duration;

        let scratch = UnitScratch::new();
        let path = scratch.root.join("pipe");
        // `rustix` excludes `mkfifoat` on Apple targets and the safe standard
        // library has no FIFO constructor, so the fixture is made by the
        // platform's own `mkfifo` utility. This runs only in the test harness;
        // the binary never spawns a process.
        let created = std::process::Command::new("mkfifo")
            .arg(&path)
            .status()
            .expect("run the platform mkfifo utility");
        assert!(created.success(), "the synthetic FIFO fixture must exist");

        let (sender, receiver) = mpsc::channel();
        std::thread::spawn(move || {
            let admitted = open_regular_nofollow(&path).is_ok();
            let _ = sender.send(admitted);
        });
        match receiver.recv_timeout(Duration::from_secs(10)) {
            Ok(admitted) => assert!(!admitted, "a FIFO is not a regular direct file"),
            Err(_) => panic!("admitting a FIFO must not block"),
        }
    }

    #[test]
    fn every_declared_command_has_a_stable_name() {
        for (arguments, expected) in [
            (
                vec!["heleos", "db", "migrate", "--database", "/d"],
                "db.migrate",
            ),
            (
                vec![
                    "heleos",
                    "project",
                    "create",
                    "--database",
                    "/d",
                    "--name",
                    "n",
                    "--actor",
                    "a",
                ],
                "project.create",
            ),
            (
                vec![
                    "heleos",
                    "ingest",
                    "/f.pdf",
                    "--project",
                    "11111111-1111-4111-8111-111111111111",
                    "--idempotency-key",
                    "k",
                    "--database",
                    "/d",
                    "--vault",
                    "/v",
                    "--actor",
                    "a",
                ],
                "ingest",
            ),
            (
                vec![
                    "heleos",
                    "inspect",
                    "foundation",
                    "--project",
                    "11111111-1111-4111-8111-111111111111",
                    "--database",
                    "/d",
                    "--vault",
                    "/v",
                ],
                "inspect.foundation",
            ),
            (
                vec!["heleos", "verify", "--database", "/d", "--vault", "/v"],
                "verify",
            ),
            (
                vec![
                    "heleos",
                    "backup",
                    "create",
                    "/o.age",
                    "--database",
                    "/d",
                    "--vault",
                    "/v",
                    "--recipient",
                    "age1ql3z7hjy54pw3hyww5ayyfg7zqgvc7w3j2elw8zmrj2kg5sfn9aqmcac8p",
                    "--signing-key",
                    "/s",
                ],
                "backup.create",
            ),
            (
                vec![
                    "heleos",
                    "backup",
                    "verify",
                    "/o.age",
                    "--identity",
                    "/i",
                    "--trusted-signer",
                    "/t",
                ],
                "backup.verify",
            ),
            (
                vec![
                    "heleos",
                    "restore",
                    "/o.age",
                    "--into",
                    "/into",
                    "--identity",
                    "/i",
                    "--trusted-signer",
                    "/t",
                    "--verify",
                ],
                "restore",
            ),
            (
                vec![
                    "heleos",
                    "jobs",
                    "resume",
                    "22222222-2222-4222-8222-222222222222",
                    "--database",
                    "/d",
                    "--vault",
                    "/v",
                    "--actor",
                    "a",
                ],
                "jobs.resume",
            ),
        ] {
            let parsed = Cli::try_parse_from(&arguments)
                .unwrap_or_else(|error| panic!("{arguments:?} must parse: {error}"));
            assert_eq!(command_name(&parsed.command), expected);
        }
    }
}
