#![forbid(unsafe_code)]

//! Black-box grammar, custody, envelope, and exit-class tests for the `heleos` binary.
//!
//! Every test drives the declared binary as an operator would: through `argv`,
//! stdout, stderr, and the process exit code. No private CLI item is reachable
//! from here, so these tests pin the operator-visible contract only.

use std::ffi::OsStr;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
// The bounded runner and its fixtures exist only where a FIFO can be created,
// so its exclusive imports carry the same target gate and no non-Unix target
// compiles an unused item.
#[cfg(unix)]
use std::process::Stdio;
#[cfg(unix)]
use std::time::{Duration, Instant};

use serde_json::Value;

const SUCCESS: i32 = 0;
const USAGE: i32 = 2;
const INTEGRITY: i32 = 20;
const QUARANTINE: i32 = 21;
const DENIED: i32 = 22;
const NOT_FOUND: i32 = 23;
const INTERNAL: i32 = 70;

fn binary() -> &'static str {
    env!("CARGO_BIN_EXE_heleos")
}

fn run<I, S>(arguments: I) -> Output
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    Command::new(binary())
        .args(arguments)
        .output()
        .expect("run the declared heleos binary")
}

/// Runs the binary from `directory`, so relative operands reach it verbatim.
fn run_in<I, S>(directory: &Path, arguments: I) -> Output
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    Command::new(binary())
        .args(arguments)
        .current_dir(directory)
        .output()
        .expect("run the declared heleos binary")
}

/// Runs the binary under a bounded wait and terminates it if it overruns.
///
/// Admission of a non-regular file must never park inside `open`. A regression
/// therefore has to surface as a failed test rather than a hung suite, and the
/// overrunning process is killed and reaped here rather than left behind.
#[cfg(unix)]
fn run_bounded<I, S>(arguments: I, limit: Duration) -> Output
where
    I: IntoIterator<Item = S>,
    S: AsRef<OsStr>,
{
    let mut child = Command::new(binary())
        .args(arguments)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn the declared heleos binary");
    let deadline = Instant::now() + limit;
    loop {
        match child.try_wait().expect("poll the heleos binary") {
            Some(_) => break,
            None if Instant::now() >= deadline => {
                child.kill().expect("terminate the overrunning binary");
                child.wait().expect("reap the terminated binary");
                panic!("heleos must not block while admitting a non-regular file");
            }
            None => std::thread::sleep(Duration::from_millis(25)),
        }
    }
    child
        .wait_with_output()
        .expect("collect the heleos binary output")
}

/// Creates a FIFO with no writer, in the test harness only.
///
/// The safe standard library has no FIFO constructor and `rustix` excludes
/// `mkfifoat` on Apple targets, so the platform utility builds the fixture.
/// The binary under test never spawns a process.
#[cfg(unix)]
fn make_fifo(path: &Path) {
    let created = Command::new("mkfifo")
        .arg(path)
        .status()
        .expect("run the platform mkfifo utility");
    assert!(created.success(), "the FIFO fixture must exist");
}

fn exit_code(output: &Output) -> i32 {
    output
        .status
        .code()
        .expect("heleos must exit with a code, never a signal")
}

fn stdout_text(output: &Output) -> String {
    String::from_utf8(output.stdout.clone()).expect("stdout must be UTF-8")
}

fn stderr_text(output: &Output) -> String {
    String::from_utf8(output.stderr.clone()).expect("stderr must be UTF-8")
}

/// Asserts the single-envelope stdout contract and returns the decoded envelope.
fn success_envelope(output: &Output, command: &str) -> Value {
    assert_eq!(
        exit_code(output),
        SUCCESS,
        "expected success for {command}: stderr={}",
        stderr_text(output)
    );
    assert!(
        output.stderr.is_empty(),
        "successful runs must leave stderr empty, saw {:?}",
        stderr_text(output)
    );
    let text = stdout_text(output);
    assert_eq!(
        text.lines().count(),
        1,
        "stdout must carry exactly one JSON envelope line"
    );
    assert!(text.ends_with('\n'), "the envelope line must be terminated");
    let envelope: Value = serde_json::from_str(text.trim_end())
        .unwrap_or_else(|error| panic!("stdout must be one JSON object: {error}: {text}"));
    assert_eq!(envelope["schema"], "heleos.cli-envelope/v1");
    assert_eq!(envelope["command"], command);
    assert!(
        envelope.get("result").is_some(),
        "the envelope must carry a result"
    );
    assert_eq!(
        envelope.as_object().expect("object envelope").len(),
        3,
        "the envelope has exactly schema, command, and result"
    );
    envelope
}

/// Asserts the single-diagnostic stderr contract and returns the decoded object.
fn failure_diagnostic(output: &Output, expected_code: i32) -> Value {
    assert_eq!(
        exit_code(output),
        expected_code,
        "unexpected exit class: stdout={} stderr={}",
        stdout_text(output),
        stderr_text(output)
    );
    assert!(
        output.stdout.is_empty(),
        "failed runs must not emit a success envelope, saw {:?}",
        stdout_text(output)
    );
    let text = stderr_text(output);
    assert_eq!(
        text.lines().count(),
        1,
        "stderr must carry exactly one escaped JSON diagnostic line, saw {text:?}"
    );
    let diagnostic: Value = serde_json::from_str(text.trim_end())
        .unwrap_or_else(|error| panic!("stderr must be one JSON object: {error}: {text:?}"));
    assert_eq!(diagnostic["schema"], "heleos.cli-error/v1");
    assert_eq!(diagnostic["exit_code"], expected_code);
    assert!(
        diagnostic["class"].is_string(),
        "the diagnostic must name an exit class"
    );
    assert!(
        diagnostic["message"].is_string(),
        "the diagnostic must carry a message"
    );
    diagnostic
}

struct Scratch {
    root: PathBuf,
}

impl Scratch {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!("heleos-cli-t-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&root).expect("create the unpredictable scratch child");
        heleos_core::apply_private_permissions(&root).expect("harden the scratch child");
        let root = fs::canonicalize(&root).expect("canonicalize the scratch child");
        Self { root }
    }

    fn join(&self, name: &str) -> PathBuf {
        self.root.join(name)
    }

    fn directory(&self, name: &str) -> PathBuf {
        let path = self.join(name);
        fs::create_dir(&path).expect("create a scratch directory");
        heleos_core::apply_private_permissions(&path).expect("harden a scratch directory");
        path
    }

    fn private_file(&self, name: &str, bytes: &[u8]) -> PathBuf {
        let path = self.join(name);
        fs::write(&path, bytes).expect("write a scratch file");
        heleos_core::apply_private_permissions(&path).expect("harden a scratch file");
        path
    }

    #[cfg(unix)]
    fn world_readable_file(&self, name: &str, bytes: &[u8]) -> PathBuf {
        let path = self.join(name);
        fs::write(&path, bytes).expect("write a scratch file");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&path, fs::Permissions::from_mode(0o644))
                .expect("widen a scratch file");
        }
        path
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

/// A migrated database plus the flags that address it.
struct Foundation {
    database: PathBuf,
    vault: PathBuf,
}

impl Foundation {
    fn migrate(scratch: &Scratch) -> Self {
        let database = scratch.join("foundation.sqlite3");
        let output = run([
            OsStr::new("db"),
            OsStr::new("migrate"),
            OsStr::new("--database"),
            database.as_os_str(),
        ]);
        let envelope = success_envelope(&output, "db.migrate");
        assert_eq!(envelope["result"]["to_version"], 1);
        Self {
            database,
            vault: scratch.join("vault"),
        }
    }
}

fn create_project(foundation: &Foundation, id: &str, name: &str) -> Output {
    run([
        OsStr::new("project"),
        OsStr::new("create"),
        OsStr::new("--database"),
        foundation.database.as_os_str(),
        OsStr::new("--name"),
        OsStr::new(name),
        OsStr::new("--id"),
        OsStr::new(id),
        OsStr::new("--actor"),
        OsStr::new("operator-1"),
    ])
}

const PROJECT: &str = "11111111-1111-4111-8111-111111111111";
const ABSENT_DIGEST: &str = "0000000000000000000000000000000000000000000000000000000000000000";

// ---------------------------------------------------------------------------
// Help, version, and grammar surface
// ---------------------------------------------------------------------------

#[test]
fn help_is_available_from_the_declared_heleos_binary() {
    let output = Command::new(env!("CARGO_BIN_EXE_heleos"))
        .arg("--help")
        .output()
        .expect("run the declared heleos binary");

    assert!(output.status.success(), "--help must succeed");
}

#[test]
fn root_help_lists_every_declared_subcommand() {
    let output = run(["--help"]);
    assert_eq!(exit_code(&output), SUCCESS);
    let text = stdout_text(&output);
    for subcommand in [
        "db", "project", "ingest", "inspect", "verify", "backup", "restore", "jobs",
    ] {
        assert!(
            text.contains(subcommand),
            "root help must list {subcommand}"
        );
    }
}

#[test]
fn nested_help_documents_the_required_flags_of_each_leaf() {
    for (arguments, expected) in [
        (vec!["db", "migrate", "--help"], vec!["--database"]),
        (
            vec!["project", "create", "--help"],
            vec!["--database", "--name", "--id", "--actor"],
        ),
        (
            vec!["ingest", "--help"],
            vec![
                "--project",
                "--idempotency-key",
                "--database",
                "--vault",
                "--actor",
            ],
        ),
        (
            vec!["inspect", "foundation", "--help"],
            vec!["--project", "--database", "--vault"],
        ),
        (
            vec!["verify", "--help"],
            vec!["--database", "--vault", "--object", "--revision"],
        ),
        (
            vec!["backup", "create", "--help"],
            vec!["--database", "--vault", "--recipient", "--signing-key"],
        ),
        (
            vec!["backup", "verify", "--help"],
            vec!["--identity", "--trusted-signer"],
        ),
        (
            vec!["restore", "--help"],
            vec!["--into", "--identity", "--trusted-signer", "--verify"],
        ),
        (
            vec!["jobs", "resume", "--help"],
            vec!["--database", "--vault", "--actor"],
        ),
    ] {
        let output = run(&arguments);
        assert_eq!(
            exit_code(&output),
            SUCCESS,
            "help for {arguments:?} must succeed"
        );
        let text = stdout_text(&output);
        for flag in expected {
            assert!(
                text.contains(flag),
                "help for {arguments:?} must list {flag}"
            );
        }
    }
}

#[test]
fn version_is_reported_without_a_diagnostic() {
    let output = run(["--version"]);
    assert_eq!(exit_code(&output), SUCCESS);
    assert!(output.stderr.is_empty());
    assert!(stdout_text(&output).contains("heleos"));
}

// ---------------------------------------------------------------------------
// Usage errors
// ---------------------------------------------------------------------------

#[test]
fn no_subcommand_is_a_usage_error() {
    failure_diagnostic(&run(Vec::<&str>::new()), USAGE);
}

#[test]
fn unknown_subcommand_and_unknown_flag_are_usage_errors() {
    let diagnostic = failure_diagnostic(&run(["nonexistent-verb"]), USAGE);
    assert_eq!(diagnostic["class"], "usage");
    failure_diagnostic(
        &run(["db", "migrate", "--database", "/x", "--unknown-flag"]),
        USAGE,
    );
}

#[test]
fn duplicate_long_flags_are_rejected_instead_of_silently_overriding() {
    let diagnostic = failure_diagnostic(
        &run([
            "db",
            "migrate",
            "--database",
            "/first.sqlite3",
            "--database",
            "/second.sqlite3",
        ]),
        USAGE,
    );
    assert_eq!(diagnostic["class"], "usage");
    failure_diagnostic(
        &run([
            "db",
            "migrate",
            "--database=/first.sqlite3",
            "--database=/second.sqlite3",
        ]),
        USAGE,
    );
}

#[test]
fn object_and_revision_are_mutually_exclusive() {
    failure_diagnostic(
        &run([
            "verify",
            "--database",
            "/db",
            "--vault",
            "/vault",
            "--object",
            ABSENT_DIGEST,
            "--revision",
            ABSENT_DIGEST,
        ]),
        USAGE,
    );
}

#[test]
fn missing_required_flags_are_usage_errors() {
    for arguments in [
        vec!["db", "migrate"],
        vec!["project", "create", "--database", "/db", "--name", "n"],
        vec!["backup", "verify", "/backup"],
        vec!["restore", "/backup", "--into", "/into"],
    ] {
        failure_diagnostic(&run(&arguments), USAGE);
    }
}

#[test]
fn restore_requires_the_destructive_verify_acknowledgement() {
    let scratch = Scratch::new();
    let identity = scratch.private_file("id.age", b"AGE-SECRET-KEY-1PLACEHOLDER\n");
    let signer = scratch.private_file("signer.key", &[0_u8; 32]);
    failure_diagnostic(
        &run([
            OsStr::new("restore"),
            OsStr::new("/absent.age"),
            OsStr::new("--into"),
            scratch.join("restored").as_os_str(),
            OsStr::new("--identity"),
            identity.as_os_str(),
            OsStr::new("--trusted-signer"),
            signer.as_os_str(),
        ]),
        USAGE,
    );
}

#[test]
fn malformed_ids_digests_and_recipients_are_usage_errors() {
    let scratch = Scratch::new();
    let foundation = Foundation::migrate(&scratch);
    // Malformed project UUID.
    failure_diagnostic(
        &run([
            OsStr::new("project"),
            OsStr::new("create"),
            OsStr::new("--database"),
            foundation.database.as_os_str(),
            OsStr::new("--name"),
            OsStr::new("Fixture"),
            OsStr::new("--id"),
            OsStr::new("not-a-uuid"),
            OsStr::new("--actor"),
            OsStr::new("operator-1"),
        ]),
        USAGE,
    );
    // Malformed digest: wrong length, then uppercase hex, then non-hex.
    for digest in [
        "abc",
        "AAAA000000000000000000000000000000000000000000000000000000000000",
        "zz00000000000000000000000000000000000000000000000000000000000000",
    ] {
        failure_diagnostic(
            &run([
                OsStr::new("verify"),
                OsStr::new("--database"),
                foundation.database.as_os_str(),
                OsStr::new("--vault"),
                foundation.vault.as_os_str(),
                OsStr::new("--object"),
                OsStr::new(digest),
            ]),
            USAGE,
        );
    }
    // Malformed age recipient.
    failure_diagnostic(
        &run([
            OsStr::new("backup"),
            OsStr::new("create"),
            scratch.join("backup.age").as_os_str(),
            OsStr::new("--database"),
            foundation.database.as_os_str(),
            OsStr::new("--vault"),
            foundation.vault.as_os_str(),
            OsStr::new("--recipient"),
            OsStr::new("ssh-ed25519 AAAA"),
            OsStr::new("--signing-key"),
            OsStr::new("/absent.key"),
        ]),
        USAGE,
    );
}

#[test]
fn oversize_and_control_bearing_typed_arguments_are_usage_errors() {
    let scratch = Scratch::new();
    let foundation = Foundation::migrate(&scratch);
    let oversize_actor = "a".repeat(129);
    let oversize_key = "k".repeat(129);
    for (actor, key) in [
        (oversize_actor.as_str(), "fine"),
        ("fine", oversize_key.as_str()),
        ("bad\u{7}actor", "fine"),
        ("fine", "bad\u{7}key"),
        ("", "fine"),
        ("fine", ""),
    ] {
        failure_diagnostic(
            &run([
                OsStr::new("ingest"),
                OsStr::new("/absent.pdf"),
                OsStr::new("--project"),
                OsStr::new(PROJECT),
                OsStr::new("--idempotency-key"),
                OsStr::new(key),
                OsStr::new("--database"),
                foundation.database.as_os_str(),
                OsStr::new("--vault"),
                foundation.vault.as_os_str(),
                OsStr::new("--actor"),
                OsStr::new(actor),
            ]),
            USAGE,
        );
    }
}

#[test]
fn rejected_operands_are_never_echoed_back_into_the_usage_diagnostic() {
    // Break caught: a parser rejection rendering the offending operand, which
    // is how an identity mistyped into a public field, or any other private
    // value, reaches stderr and the operator's logs.
    let hostile = [
        "--flag with spaces",
        "--flag\nwith\nnewlines",
        "--flag\u{1}\u{7}\u{1b}with-controls",
        "--flag-\u{4e2d}\u{6587}-\u{1f600}",
        "--flag; rm -rf / | cat & echo `id` $(id)",
        "--../../../../etc/passwd",
        "--'; DROP TABLE projects; --",
        "--\"quoted\\backslash\"",
        "--AGE-SECRET-KEY-1EXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXAMPLEEXA",
    ];
    for flag in hostile {
        let output = run(["db", "migrate", "--database", "/db", flag]);
        let diagnostic = failure_diagnostic(&output, USAGE);
        assert_eq!(diagnostic["class"], "usage");
        let message = diagnostic["message"]
            .as_str()
            .expect("string diagnostic message");
        assert!(
            !message.contains('\n') && !message.contains('\r'),
            "raw newlines must not survive into the diagnostic"
        );
        assert!(
            !message.chars().any(|character| character.is_control()),
            "raw control characters must not survive into the diagnostic"
        );
        assert!(
            !message.contains(flag.trim_start_matches('-')),
            "the rejected operand must not be echoed, saw {message:?}"
        );
    }
}

#[test]
fn secret_shaped_values_rejected_by_a_typed_parser_are_not_disclosed() {
    // The value below is a locally generated age identity, never an
    // operational key. Supplying it where a public recipient belongs is the
    // ordinary operator slip this contract has to survive.
    use age::secrecy::ExposeSecret;

    let scratch = Scratch::new();
    let foundation = Foundation::migrate(&scratch);
    let identity = age::x25519::Identity::generate();
    let secret = identity.to_string();
    let secret = secret.expose_secret();

    for arguments in [
        vec![
            OsStr::new("backup"),
            OsStr::new("create"),
            OsStr::new("/out.age"),
            OsStr::new("--database"),
            foundation.database.as_os_str(),
            OsStr::new("--vault"),
            foundation.vault.as_os_str(),
            OsStr::new("--recipient"),
            OsStr::new(secret),
            OsStr::new("--signing-key"),
            OsStr::new("/absent.key"),
        ],
        vec![
            OsStr::new("verify"),
            OsStr::new("--database"),
            foundation.database.as_os_str(),
            OsStr::new("--vault"),
            foundation.vault.as_os_str(),
            OsStr::new("--object"),
            OsStr::new(secret),
        ],
        vec![
            OsStr::new("project"),
            OsStr::new("create"),
            OsStr::new("--database"),
            foundation.database.as_os_str(),
            OsStr::new("--name"),
            OsStr::new("Fixture"),
            OsStr::new("--id"),
            OsStr::new(secret),
            OsStr::new("--actor"),
            OsStr::new("operator-1"),
        ],
    ] {
        let output = run(&arguments);
        failure_diagnostic(&output, USAGE);
        let rendered = stderr_text(&output);
        assert!(
            !rendered.contains(secret),
            "a rejected secret-shaped operand must not reach stderr"
        );
        assert!(!rendered.contains("AGE-SECRET-KEY-"));
    }
}

#[test]
fn oversize_argument_vectors_are_rejected_without_panicking() {
    let scratch = Scratch::new();
    let foundation = Foundation::migrate(&scratch);
    let oversize = "n".repeat(200_000);
    let output = create_project(&foundation, PROJECT, &oversize);
    assert_ne!(exit_code(&output), SUCCESS);
    assert!(output.stdout.is_empty());
    let text = stderr_text(&output);
    assert_eq!(text.lines().count(), 1);
    let diagnostic: Value = serde_json::from_str(text.trim_end()).expect("one JSON diagnostic");
    assert_eq!(diagnostic["schema"], "heleos.cli-error/v1");
}

#[test]
fn real_leading_hyphen_relative_operands_reach_the_service_boundary() {
    // The operands below genuinely begin with `-`, so they exercise the two
    // spellings an operator actually needs: `--flag=value` for an option and
    // `--` for a positional. An absolute path would begin with a separator and
    // would prove nothing about either.
    let scratch = Scratch::new();
    let output = run_in(
        &scratch.root,
        ["db", "migrate", "--database=-leading-hyphen.sqlite3"],
    );
    success_envelope(&output, "db.migrate");
    assert!(
        scratch.join("-leading-hyphen.sqlite3").is_file(),
        "the hyphen-led relative database must have been created"
    );

    // A hyphen-led relative positional reaches the container opener, which
    // admits the real file and hands it to core.
    let keys = Keys::new(&scratch);
    scratch.private_file("-leading-hyphen.age", b"not a container");
    let output = run_in(
        &scratch.root,
        [
            OsStr::new("backup"),
            OsStr::new("verify"),
            OsStr::new("--identity"),
            keys.identity_path.as_os_str(),
            OsStr::new("--trusted-signer"),
            keys.trusted_signer_path.as_os_str(),
            OsStr::new("--"),
            OsStr::new("-leading-hyphen.age"),
        ],
    );
    // The operand parsed and the file was opened: core rejected its contents.
    let diagnostic = failure_diagnostic(&output, INTEGRITY);
    assert_eq!(diagnostic["command"], "backup.verify");
}

#[test]
fn unusual_but_legitimate_names_and_text_transport_to_the_service_boundary() {
    // Break caught: quoting, Unicode, or shell-shaped bytes being mangled or
    // refused on the way to core, rather than carried through verbatim.
    let scratch = Scratch::new();
    let foundation = backup_ready(&scratch);
    let hostile_name = "Bridge \u{4e2d}\u{6587} \u{1f600} 'quoted' \"double\" \
                        ; rm -rf / | cat & $(id) `id` ../.. %s %n \u{2028}end";
    let output = run([
        OsStr::new("project"),
        OsStr::new("create"),
        OsStr::new("--database"),
        foundation.database.as_os_str(),
        OsStr::new("--name"),
        OsStr::new(hostile_name),
        OsStr::new("--id"),
        OsStr::new("44444444-4444-4444-8444-444444444444"),
        OsStr::new("--actor"),
        OsStr::new("operator-1"),
    ]);
    let created = success_envelope(&output, "project.create");
    assert_eq!(
        created["result"]["project_id"],
        "44444444-4444-4444-8444-444444444444"
    );

    // A PDF whose file name carries the same shapes still ingests, and the
    // receipt still carries no path. The name is the widest set of unusual
    // characters that is a *valid filename* on the target: Windows rejects a
    // double quote in a filename, so the double-quoted shape is carried by the
    // project name and actor text fields above and below instead. The success
    // assertion itself is identical on every platform.
    #[cfg(unix)]
    let source_name = "a doc; rm -rf \u{4e2d}\u{6587} 'q' \"d\" $(id) %s.pdf";
    #[cfg(not(unix))]
    let source_name = "a doc; rm -rf \u{4e2d}\u{6587} 'q' $(id) %s.pdf";
    let source = scratch.private_file(source_name, &single_page_pdf());
    let output = run([
        OsStr::new("ingest"),
        source.as_os_str(),
        OsStr::new("--project"),
        OsStr::new("44444444-4444-4444-8444-444444444444"),
        OsStr::new("--idempotency-key"),
        OsStr::new("odd \"quoted\" \u{4e2d}\u{6587} key"),
        OsStr::new("--database"),
        foundation.database.as_os_str(),
        OsStr::new("--vault"),
        foundation.vault.as_os_str(),
        OsStr::new("--actor"),
        OsStr::new("operator \"double\" \u{4e2d}\u{6587}"),
    ]);
    let ingested = success_envelope(&output, "ingest");
    assert_eq!(ingested["result"]["outcome"], "accepted_new");
    let rendered = serde_json::to_string(&ingested).expect("re-render the envelope");
    assert!(
        !rendered.contains(scratch.root.to_str().expect("UTF-8 scratch root")),
        "receipts must stay path free whatever the source is called"
    );
}

#[cfg(unix)]
#[test]
fn a_fifo_is_refused_promptly_wherever_a_file_is_admitted() {
    // Break caught: a FIFO with no writer parking `heleos` inside `open`, so a
    // hostile or mistaken operand stalls a recovery command indefinitely.
    let scratch = Scratch::new();
    let keys = Keys::new(&scratch);
    let pipe = scratch.join("pipe.age");
    make_fifo(&pipe);
    let output = run_bounded(
        [
            OsStr::new("backup"),
            OsStr::new("verify"),
            pipe.as_os_str(),
            OsStr::new("--identity"),
            keys.identity_path.as_os_str(),
            OsStr::new("--trusted-signer"),
            keys.trusted_signer_path.as_os_str(),
        ],
        Duration::from_secs(20),
    );
    let diagnostic = failure_diagnostic(&output, DENIED);
    assert_eq!(diagnostic["class"], "policy_denied");

    // The same admission guards key material.
    let key_pipe = scratch.join("pipe.key");
    make_fifo(&key_pipe);
    let foundation = backup_ready(&scratch);
    let output = run_bounded(
        [
            OsStr::new("backup"),
            OsStr::new("create"),
            scratch.join("from-a-pipe.age").as_os_str(),
            OsStr::new("--database"),
            foundation.database.as_os_str(),
            OsStr::new("--vault"),
            foundation.vault.as_os_str(),
            OsStr::new("--recipient"),
            OsStr::new(&keys.recipient),
            OsStr::new("--signing-key"),
            key_pipe.as_os_str(),
        ],
        Duration::from_secs(20),
    );
    assert_ne!(exit_code(&output), SUCCESS);
    assert!(output.stdout.is_empty());
}

// ---------------------------------------------------------------------------
// Foundation commands
// ---------------------------------------------------------------------------

#[test]
fn db_migrate_reports_the_applied_schema_and_is_idempotent() {
    let scratch = Scratch::new();
    let foundation = Foundation::migrate(&scratch);
    let output = run([
        OsStr::new("db"),
        OsStr::new("migrate"),
        OsStr::new("--database"),
        foundation.database.as_os_str(),
    ]);
    let envelope = success_envelope(&output, "db.migrate");
    assert_eq!(envelope["result"]["from_version"], 1);
    assert_eq!(envelope["result"]["to_version"], 1);
    assert_eq!(
        envelope["result"]["applied_versions"]
            .as_array()
            .expect("array")
            .len(),
        0
    );
}

#[test]
fn project_create_emits_one_path_free_receipt() {
    let scratch = Scratch::new();
    let foundation = Foundation::migrate(&scratch);
    let output = create_project(&foundation, PROJECT, "Bridge Retrofit");
    let envelope = success_envelope(&output, "project.create");
    assert_eq!(envelope["result"]["project_id"], PROJECT);
    let rendered = serde_json::to_string(&envelope).expect("re-render the envelope");
    assert!(
        !rendered.contains(scratch.root.to_str().expect("UTF-8 scratch root")),
        "receipts must stay path free"
    );
}

#[test]
fn inspect_foundation_reports_counts_for_a_created_project() {
    let scratch = Scratch::new();
    let foundation = Foundation::migrate(&scratch);
    success_envelope(
        &create_project(&foundation, PROJECT, "Bridge Retrofit"),
        "project.create",
    );
    let vault = scratch.directory("vault");
    let output = run([
        OsStr::new("inspect"),
        OsStr::new("foundation"),
        OsStr::new("--project"),
        OsStr::new(PROJECT),
        OsStr::new("--database"),
        foundation.database.as_os_str(),
        OsStr::new("--vault"),
        vault.as_os_str(),
    ]);
    let envelope = success_envelope(&output, "inspect.foundation");
    assert_eq!(envelope["result"]["project_id"], PROJECT);
    assert_eq!(envelope["result"]["counts"]["documents"], 0);
    let rendered = serde_json::to_string(&envelope).expect("re-render the envelope");
    assert!(!rendered.contains(scratch.root.to_str().expect("UTF-8 scratch root")));
}

#[test]
fn verify_reports_a_clean_database_and_audit_chain() {
    let scratch = Scratch::new();
    let foundation = Foundation::migrate(&scratch);
    success_envelope(
        &create_project(&foundation, PROJECT, "Bridge Retrofit"),
        "project.create",
    );
    let vault = scratch.directory("vault");
    let output = run([
        OsStr::new("verify"),
        OsStr::new("--database"),
        foundation.database.as_os_str(),
        OsStr::new("--vault"),
        vault.as_os_str(),
    ]);
    let envelope = success_envelope(&output, "verify");
    assert_eq!(envelope["result"]["database"]["clean"], true);
    assert_eq!(envelope["result"]["audit_chain"]["valid"], true);
    assert!(envelope["result"]["object"].is_null());
    assert!(envelope["result"]["revision"].is_null());
}

#[test]
fn verify_object_and_revision_report_absent_content_as_not_found() {
    let scratch = Scratch::new();
    let foundation = Foundation::migrate(&scratch);
    let vault = scratch.directory("vault");
    for flag in ["--object", "--revision"] {
        let diagnostic = failure_diagnostic(
            &run([
                OsStr::new("verify"),
                OsStr::new("--database"),
                foundation.database.as_os_str(),
                OsStr::new("--vault"),
                vault.as_os_str(),
                OsStr::new(flag),
                OsStr::new(ABSENT_DIGEST),
            ]),
            NOT_FOUND,
        );
        assert_eq!(diagnostic["class"], "not_found");
    }
}

#[test]
fn absent_database_and_vault_paths_fail_without_creating_a_vault() {
    let scratch = Scratch::new();
    let foundation = Foundation::migrate(&scratch);
    let vault = scratch.join("absent-vault");
    let output = run([
        OsStr::new("verify"),
        OsStr::new("--database"),
        foundation.database.as_os_str(),
        OsStr::new("--vault"),
        vault.as_os_str(),
    ]);
    assert_ne!(exit_code(&output), SUCCESS);
    assert!(output.stdout.is_empty());
    assert!(
        !vault.exists(),
        "read-only commands must never create the vault"
    );
}

#[test]
fn traversal_shaped_and_directory_valued_database_paths_are_rejected() {
    let scratch = Scratch::new();
    let directory = scratch.directory("a-directory");
    let output = run([
        OsStr::new("db"),
        OsStr::new("migrate"),
        OsStr::new("--database"),
        directory.as_os_str(),
    ]);
    assert_ne!(exit_code(&output), SUCCESS);
    assert!(output.stdout.is_empty());

    let traversal = scratch.join("../../../../etc/heleos-should-not-exist.sqlite3");
    let output = run([
        OsStr::new("db"),
        OsStr::new("migrate"),
        OsStr::new("--database"),
        traversal.as_os_str(),
    ]);
    assert_ne!(exit_code(&output), SUCCESS);
    assert!(output.stdout.is_empty());
}

#[test]
fn jobs_resume_for_an_unknown_job_reports_a_mapped_class() {
    let scratch = Scratch::new();
    let foundation = backup_ready(&scratch);
    let output = run([
        OsStr::new("jobs"),
        OsStr::new("resume"),
        OsStr::new("22222222-2222-4222-8222-222222222222"),
        OsStr::new("--database"),
        foundation.database.as_os_str(),
        OsStr::new("--vault"),
        foundation.vault.as_os_str(),
        OsStr::new("--actor"),
        OsStr::new("operator-1"),
    ]);
    // Core refuses to recover a job it has no authoritative record of.
    let diagnostic = failure_diagnostic(&output, INTEGRITY);
    assert_eq!(diagnostic["class"], "integrity");
    assert_eq!(diagnostic["command"], "jobs.resume");
}

// ---------------------------------------------------------------------------
// Key custody
// ---------------------------------------------------------------------------

struct Keys {
    identity_path: PathBuf,
    recipient: String,
    signing_key_path: PathBuf,
    trusted_signer_path: PathBuf,
}

impl Keys {
    fn new(scratch: &Scratch) -> Self {
        use age::secrecy::ExposeSecret;

        let identity = age::x25519::Identity::generate();
        let identity_path = scratch.private_file(
            "identity.age",
            identity.to_string().expose_secret().as_bytes(),
        );
        let recipient = identity.to_public().to_string();
        let signing_key = ed25519_dalek::SigningKey::from_bytes(&[7_u8; 32]);
        let signing_key_path = scratch.private_file("signing.key", &signing_key.to_bytes());
        let trusted_signer_path =
            scratch.private_file("signer.pub", &signing_key.verifying_key().to_bytes());
        Self {
            identity_path,
            recipient,
            signing_key_path,
            trusted_signer_path,
        }
    }
}

fn backup_create(
    scratch: &Scratch,
    foundation: &Foundation,
    keys: &Keys,
    destination: &Path,
    signing_key: &Path,
) -> Output {
    let _ = scratch;
    run([
        OsStr::new("backup"),
        OsStr::new("create"),
        destination.as_os_str(),
        OsStr::new("--database"),
        foundation.database.as_os_str(),
        OsStr::new("--vault"),
        foundation.vault.as_os_str(),
        OsStr::new("--recipient"),
        OsStr::new(&keys.recipient),
        OsStr::new("--signing-key"),
        signing_key.as_os_str(),
    ])
}

/// Migrates a foundation and creates its vault so backups have a full source.
fn backup_ready(scratch: &Scratch) -> Foundation {
    let foundation = Foundation::migrate(scratch);
    success_envelope(
        &create_project(&foundation, PROJECT, "Bridge Retrofit"),
        "project.create",
    );
    let vault = heleos_core::Vault::open(heleos_core::VaultConfig {
        root: foundation.vault.clone(),
        open_mode: heleos_core::VaultOpenMode::CreateNew,
    })
    .expect("create the source vault");
    drop(vault);
    foundation
}

#[test]
fn signing_keys_must_be_exactly_thirty_two_bytes_with_a_clean_eof() {
    let scratch = Scratch::new();
    let foundation = backup_ready(&scratch);
    let keys = Keys::new(&scratch);
    for (name, bytes) in [
        ("short.key", vec![0_u8; 31]),
        ("long.key", vec![0_u8; 33]),
        ("empty.key", Vec::new()),
        ("huge.key", vec![0_u8; 4096]),
    ] {
        let path = scratch.private_file(name, &bytes);
        let output = backup_create(
            &scratch,
            &foundation,
            &keys,
            &scratch.join(&format!("backup-{name}.age")),
            &path,
        );
        assert_ne!(exit_code(&output), SUCCESS, "{name} must be rejected");
        assert!(output.stdout.is_empty());
        let diagnostic: Value =
            serde_json::from_str(stderr_text(&output).trim_end()).expect("one JSON diagnostic");
        assert_eq!(diagnostic["class"], "policy_denied");
    }
}

#[test]
fn trusted_signer_keys_must_be_exactly_thirty_two_bytes() {
    let scratch = Scratch::new();
    let keys = Keys::new(&scratch);
    for (name, bytes) in [("short.pub", vec![0_u8; 31]), ("long.pub", vec![0_u8; 33])] {
        let path = scratch.private_file(name, &bytes);
        let output = run([
            OsStr::new("backup"),
            OsStr::new("verify"),
            OsStr::new("/absent.age"),
            OsStr::new("--identity"),
            keys.identity_path.as_os_str(),
            OsStr::new("--trusted-signer"),
            path.as_os_str(),
        ]);
        let diagnostic = failure_diagnostic(&output, DENIED);
        assert_eq!(diagnostic["class"], "policy_denied");
    }
}

#[test]
fn key_material_must_be_private_regular_and_direct() {
    let scratch = Scratch::new();
    let foundation = backup_ready(&scratch);
    let keys = Keys::new(&scratch);

    // World-readable key material is refused.
    #[cfg(unix)]
    {
        let widened = scratch.world_readable_file("widened.key", &[3_u8; 32]);
        let output = backup_create(
            &scratch,
            &foundation,
            &keys,
            &scratch.join("backup-widened.age"),
            &widened,
        );
        let diagnostic = failure_diagnostic(&output, DENIED);
        assert_eq!(diagnostic["class"], "policy_denied");
    }

    // A directory is not key material.
    let directory = scratch.directory("key-directory");
    let output = backup_create(
        &scratch,
        &foundation,
        &keys,
        &scratch.join("backup-directory.age"),
        &directory,
    );
    assert_ne!(exit_code(&output), SUCCESS);
    assert!(output.stdout.is_empty());

    // A symlink to good key material is refused: the open is no-follow.
    #[cfg(unix)]
    {
        let link = scratch.join("signing-link.key");
        std::os::unix::fs::symlink(&keys.signing_key_path, &link).expect("create a symlink");
        let output = backup_create(
            &scratch,
            &foundation,
            &keys,
            &scratch.join("backup-symlink.age"),
            &link,
        );
        assert_ne!(exit_code(&output), SUCCESS);
        assert!(output.stdout.is_empty());
    }

    // A character device is not key material.
    #[cfg(unix)]
    {
        let output = backup_create(
            &scratch,
            &foundation,
            &keys,
            &scratch.join("backup-device.age"),
            Path::new("/dev/null"),
        );
        assert_ne!(exit_code(&output), SUCCESS);
        assert!(output.stdout.is_empty());
    }
}

#[test]
fn identity_files_must_hold_exactly_one_age_x25519_identity() {
    let scratch = Scratch::new();
    let keys = Keys::new(&scratch);
    for (name, bytes) in [
        ("garbage.age", b"not-an-identity\n".to_vec()),
        ("empty.age", Vec::new()),
        ("ssh.age", b"-----BEGIN OPENSSH PRIVATE KEY-----\n".to_vec()),
    ] {
        let path = scratch.private_file(name, &bytes);
        let output = run([
            OsStr::new("backup"),
            OsStr::new("verify"),
            OsStr::new("/absent.age"),
            OsStr::new("--identity"),
            path.as_os_str(),
            OsStr::new("--trusted-signer"),
            keys.trusted_signer_path.as_os_str(),
        ]);
        let diagnostic = failure_diagnostic(&output, DENIED);
        assert_eq!(diagnostic["class"], "policy_denied");
    }
}

#[test]
fn diagnostics_never_echo_key_bytes_or_identity_text() {
    let scratch = Scratch::new();
    let foundation = backup_ready(&scratch);
    let keys = Keys::new(&scratch);
    let identity_text = fs::read_to_string(&keys.identity_path).expect("read the identity file");
    let secret = identity_text.trim();
    assert!(secret.starts_with("AGE-SECRET-KEY-"));

    let short = scratch.private_file("short.key", &[9_u8; 31]);
    let output = backup_create(
        &scratch,
        &foundation,
        &keys,
        &scratch.join("backup-short.age"),
        &short,
    );
    let rendered = stderr_text(&output);
    assert!(!rendered.contains(secret));
    assert!(!rendered.contains("AGE-SECRET-KEY-"));

    let output = run([
        OsStr::new("backup"),
        OsStr::new("verify"),
        OsStr::new("/absent.age"),
        OsStr::new("--identity"),
        keys.identity_path.as_os_str(),
        OsStr::new("--trusted-signer"),
        keys.trusted_signer_path.as_os_str(),
    ]);
    let rendered = stderr_text(&output);
    assert!(!rendered.contains(secret));
    assert!(!rendered.contains("AGE-SECRET-KEY-"));
}

// ---------------------------------------------------------------------------
// Backup, verification, and restore
// ---------------------------------------------------------------------------

#[test]
fn backup_create_verify_and_restore_round_trip_with_path_free_receipts() {
    let scratch = Scratch::new();
    let foundation = backup_ready(&scratch);
    let keys = Keys::new(&scratch);
    let destination = scratch.join("backup.age");

    let output = backup_create(
        &scratch,
        &foundation,
        &keys,
        &destination,
        &keys.signing_key_path,
    );
    let created = success_envelope(&output, "backup.create");
    assert_eq!(
        created["result"]["schema"], "heleos.backup-receipt/v1",
        "the receipt must be the core receipt"
    );
    assert!(destination.is_file(), "the ciphertext must be published");

    let output = run([
        OsStr::new("backup"),
        OsStr::new("verify"),
        destination.as_os_str(),
        OsStr::new("--identity"),
        keys.identity_path.as_os_str(),
        OsStr::new("--trusted-signer"),
        keys.trusted_signer_path.as_os_str(),
    ]);
    let verified = success_envelope(&output, "backup.verify");
    assert_eq!(
        verified["result"]["schema"],
        "heleos.backup-verification-report/v1"
    );
    assert_eq!(
        verified["result"]["container"],
        created["result"]["container"]
    );

    let into = scratch.join("restored");
    let output = run([
        OsStr::new("restore"),
        destination.as_os_str(),
        OsStr::new("--into"),
        into.as_os_str(),
        OsStr::new("--identity"),
        keys.identity_path.as_os_str(),
        OsStr::new("--trusted-signer"),
        keys.trusted_signer_path.as_os_str(),
        OsStr::new("--verify"),
    ]);
    let restored = success_envelope(&output, "restore");
    assert_eq!(restored["result"]["schema"], "heleos.restore-receipt/v1");
    assert_eq!(restored["result"]["verification"], verified["result"]);

    let mut entries = fs::read_dir(&into)
        .expect("read the restored destination")
        .map(|entry| {
            entry
                .expect("entry")
                .file_name()
                .into_string()
                .expect("UTF-8")
        })
        .collect::<Vec<_>>();
    entries.sort();
    assert_eq!(
        entries,
        [
            "foundation.sqlite3",
            "foundation.sqlite3.writer.lock",
            "vault"
        ]
    );

    for envelope in [&created, &verified, &restored] {
        let rendered = serde_json::to_string(envelope).expect("re-render the envelope");
        assert!(
            !rendered.contains(scratch.root.to_str().expect("UTF-8 scratch root")),
            "backup receipts must stay path free"
        );
    }
}

#[test]
fn the_restore_drill_reproduces_ingested_evidence_in_the_restored_tree() {
    let scratch = Scratch::new();
    let foundation = backup_ready(&scratch);
    let keys = Keys::new(&scratch);
    let source = scratch.private_file("page.pdf", &single_page_pdf());
    let ingested = success_envelope(
        &run([
            OsStr::new("ingest"),
            source.as_os_str(),
            OsStr::new("--project"),
            OsStr::new(PROJECT),
            OsStr::new("--idempotency-key"),
            OsStr::new("drill-1"),
            OsStr::new("--database"),
            foundation.database.as_os_str(),
            OsStr::new("--vault"),
            foundation.vault.as_os_str(),
            OsStr::new("--actor"),
            OsStr::new("operator-1"),
        ]),
        "ingest",
    );
    let revision = ingested["result"]["revision_id"]
        .as_str()
        .expect("an accepted receipt names its revision")
        .to_owned();

    let destination = scratch.join("drill.age");
    let created = success_envelope(
        &backup_create(
            &scratch,
            &foundation,
            &keys,
            &destination,
            &keys.signing_key_path,
        ),
        "backup.create",
    );
    assert!(
        created["result"]["container"]["entry_count"]
            .as_u64()
            .expect("an entry count")
            > 1,
        "the container must carry the database plus the vault objects"
    );

    let into = scratch.join("drill-restored");
    success_envelope(
        &run([
            OsStr::new("restore"),
            destination.as_os_str(),
            OsStr::new("--into"),
            into.as_os_str(),
            OsStr::new("--identity"),
            keys.identity_path.as_os_str(),
            OsStr::new("--trusted-signer"),
            keys.trusted_signer_path.as_os_str(),
            OsStr::new("--verify"),
        ]),
        "restore",
    );

    // The restored tree answers the same evidence question as the source.
    let verified = success_envelope(
        &run([
            OsStr::new("verify"),
            OsStr::new("--database"),
            into.join("foundation.sqlite3").as_os_str(),
            OsStr::new("--vault"),
            into.join("vault").as_os_str(),
            OsStr::new("--revision"),
            OsStr::new(&revision),
        ]),
        "verify",
    );
    assert_eq!(
        verified["result"]["revision"]["evidence"]["manifest"]["revision_id"],
        revision.as_str()
    );
    assert_eq!(verified["result"]["database"]["clean"], true);
    assert_eq!(verified["result"]["audit_chain"]["valid"], true);
}

#[test]
fn restoring_into_an_existing_destination_is_refused() {
    let scratch = Scratch::new();
    let foundation = backup_ready(&scratch);
    let keys = Keys::new(&scratch);
    let destination = scratch.join("backup.age");
    success_envelope(
        &backup_create(
            &scratch,
            &foundation,
            &keys,
            &destination,
            &keys.signing_key_path,
        ),
        "backup.create",
    );

    let into = scratch.directory("already-here");
    let output = run([
        OsStr::new("restore"),
        destination.as_os_str(),
        OsStr::new("--into"),
        into.as_os_str(),
        OsStr::new("--identity"),
        keys.identity_path.as_os_str(),
        OsStr::new("--trusted-signer"),
        keys.trusted_signer_path.as_os_str(),
        OsStr::new("--verify"),
    ]);
    assert_ne!(exit_code(&output), SUCCESS);
    assert!(output.stdout.is_empty());
    let diagnostic: Value =
        serde_json::from_str(stderr_text(&output).trim_end()).expect("one JSON diagnostic");
    assert_eq!(diagnostic["schema"], "heleos.cli-error/v1");
    assert!(
        fs::read_dir(&into)
            .expect("read the pre-existing destination")
            .next()
            .is_none(),
        "a refused restore must not populate the destination"
    );
}

#[test]
fn wrong_identity_wrong_signer_and_corruption_all_map_to_the_integrity_class() {
    let scratch = Scratch::new();
    let foundation = backup_ready(&scratch);
    let keys = Keys::new(&scratch);
    let destination = scratch.join("backup.age");
    success_envelope(
        &backup_create(
            &scratch,
            &foundation,
            &keys,
            &destination,
            &keys.signing_key_path,
        ),
        "backup.create",
    );

    // Wrong age identity.
    use age::secrecy::ExposeSecret;
    let other = age::x25519::Identity::generate();
    let other_identity = scratch.private_file(
        "other-identity.age",
        other.to_string().expose_secret().as_bytes(),
    );
    failure_diagnostic(
        &run([
            OsStr::new("backup"),
            OsStr::new("verify"),
            destination.as_os_str(),
            OsStr::new("--identity"),
            other_identity.as_os_str(),
            OsStr::new("--trusted-signer"),
            keys.trusted_signer_path.as_os_str(),
        ]),
        INTEGRITY,
    );

    // Wrong trusted signer.
    let other_signer = scratch.private_file(
        "other-signer.pub",
        &ed25519_dalek::SigningKey::from_bytes(&[8_u8; 32])
            .verifying_key()
            .to_bytes(),
    );
    failure_diagnostic(
        &run([
            OsStr::new("backup"),
            OsStr::new("verify"),
            destination.as_os_str(),
            OsStr::new("--identity"),
            keys.identity_path.as_os_str(),
            OsStr::new("--trusted-signer"),
            other_signer.as_os_str(),
        ]),
        INTEGRITY,
    );

    // Corrupted ciphertext.
    let corrupted = scratch.join("corrupted.age");
    let mut bytes = fs::read(&destination).expect("read the ciphertext");
    let last = bytes.len() - 1;
    bytes[last] ^= 0xff;
    fs::write(&corrupted, &bytes).expect("write the corrupted ciphertext");
    let output = run([
        OsStr::new("backup"),
        OsStr::new("verify"),
        corrupted.as_os_str(),
        OsStr::new("--identity"),
        keys.identity_path.as_os_str(),
        OsStr::new("--trusted-signer"),
        keys.trusted_signer_path.as_os_str(),
    ]);
    assert_eq!(exit_code(&output), INTEGRITY);
}

#[test]
fn backup_containers_must_be_regular_direct_files() {
    let scratch = Scratch::new();
    let keys = Keys::new(&scratch);

    // A directory is not a container.
    let directory = scratch.directory("container-directory");
    let output = run([
        OsStr::new("backup"),
        OsStr::new("verify"),
        directory.as_os_str(),
        OsStr::new("--identity"),
        keys.identity_path.as_os_str(),
        OsStr::new("--trusted-signer"),
        keys.trusted_signer_path.as_os_str(),
    ]);
    assert_ne!(exit_code(&output), SUCCESS);
    assert!(output.stdout.is_empty());

    // A symlink is not a container: the open is no-follow.
    #[cfg(unix)]
    {
        let target = scratch.private_file("target.age", b"not a container");
        let link = scratch.join("container-link.age");
        std::os::unix::fs::symlink(&target, &link).expect("create a symlink");
        let output = run([
            OsStr::new("backup"),
            OsStr::new("verify"),
            link.as_os_str(),
            OsStr::new("--identity"),
            keys.identity_path.as_os_str(),
            OsStr::new("--trusted-signer"),
            keys.trusted_signer_path.as_os_str(),
        ]);
        assert_ne!(exit_code(&output), SUCCESS);
        assert!(output.stdout.is_empty());
    }

    // A character device is not a container.
    #[cfg(unix)]
    {
        let output = run([
            OsStr::new("backup"),
            OsStr::new("verify"),
            OsStr::new("/dev/null"),
            OsStr::new("--identity"),
            keys.identity_path.as_os_str(),
            OsStr::new("--trusted-signer"),
            keys.trusted_signer_path.as_os_str(),
        ]);
        assert_ne!(exit_code(&output), SUCCESS);
        assert!(output.stdout.is_empty());
    }
}

#[test]
fn absent_backup_containers_report_a_mapped_class() {
    let scratch = Scratch::new();
    let keys = Keys::new(&scratch);
    let output = run([
        OsStr::new("backup"),
        OsStr::new("verify"),
        scratch.join("absent.age").as_os_str(),
        OsStr::new("--identity"),
        keys.identity_path.as_os_str(),
        OsStr::new("--trusted-signer"),
        keys.trusted_signer_path.as_os_str(),
    ]);
    assert_eq!(exit_code(&output), INTERNAL);
    let diagnostic: Value =
        serde_json::from_str(stderr_text(&output).trim_end()).expect("one JSON diagnostic");
    assert_eq!(diagnostic["class"], "io");
}

// ---------------------------------------------------------------------------
// Ingest
// ---------------------------------------------------------------------------

/// Writes one classic single-page PDF with a valid cross-reference table.
///
/// The repository fixture crate is not a dependency of this test target, so
/// the minimal accepted shape is authored here.
fn single_page_pdf() -> Vec<u8> {
    let objects: [&[u8]; 3] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 612 792] >>",
        b"<< /Type /Page /Parent 2 0 R >>",
    ];
    let mut bytes = b"%PDF-1.7\n%\x80\x81\x82\x83\n".to_vec();
    let mut offsets = Vec::with_capacity(objects.len());
    for (index, object) in objects.iter().enumerate() {
        offsets.push(bytes.len());
        bytes.extend_from_slice(format!("{} 0 obj\n", index + 1).as_bytes());
        bytes.extend_from_slice(object);
        bytes.extend_from_slice(b"\nendobj\n");
    }
    let xref_offset = bytes.len();
    bytes.extend_from_slice(format!("xref\n0 {}\n", objects.len() + 1).as_bytes());
    bytes.extend_from_slice(b"0000000000 65535 f \n");
    for offset in offsets {
        bytes.extend_from_slice(format!("{offset:010} 00000 n \n").as_bytes());
    }
    bytes.extend_from_slice(
        format!("trailer\n<< /Size {} /Root 1 0 R", objects.len() + 1).as_bytes(),
    );
    bytes.extend_from_slice(
        b" /ID [<00112233445566778899aabbccddeeff><00112233445566778899aabbccddeeff>] >>\n",
    );
    bytes.extend_from_slice(format!("startxref\n{xref_offset}\n%%EOF\n").as_bytes());
    bytes
}

#[test]
fn an_accepted_ingest_is_verifiable_by_revision_and_visible_to_inspection() {
    let scratch = Scratch::new();
    let foundation = backup_ready(&scratch);
    let source = scratch.private_file("page.pdf", &single_page_pdf());
    let output = run([
        OsStr::new("ingest"),
        source.as_os_str(),
        OsStr::new("--project"),
        OsStr::new(PROJECT),
        OsStr::new("--idempotency-key"),
        OsStr::new("accepted-1"),
        OsStr::new("--database"),
        foundation.database.as_os_str(),
        OsStr::new("--vault"),
        foundation.vault.as_os_str(),
        OsStr::new("--actor"),
        OsStr::new("operator-1"),
    ]);
    let envelope = success_envelope(&output, "ingest");
    assert_eq!(envelope["result"]["outcome"], "accepted_new");
    let revision = envelope["result"]["revision_id"]
        .as_str()
        .expect("an accepted receipt names its revision")
        .to_owned();
    let content = envelope["result"]["content_sha256"]
        .as_str()
        .expect("an accepted receipt names its content digest")
        .to_owned();

    // `verify --revision` re-reads the manifest and both vault objects.
    let output = run([
        OsStr::new("verify"),
        OsStr::new("--database"),
        foundation.database.as_os_str(),
        OsStr::new("--vault"),
        foundation.vault.as_os_str(),
        OsStr::new("--revision"),
        OsStr::new(&revision),
    ]);
    let verified = success_envelope(&output, "verify");
    assert_eq!(
        verified["result"]["revision"]["evidence"]["manifest"]["revision_id"],
        revision.as_str()
    );
    assert_eq!(
        verified["result"]["revision"]["original"]["kind"],
        "verified"
    );
    assert_eq!(
        verified["result"]["revision"]["manifest"]["kind"],
        "verified"
    );
    assert!(verified["result"]["object"].is_null());

    // `verify --object` re-reads exactly the original content object.
    let output = run([
        OsStr::new("verify"),
        OsStr::new("--database"),
        foundation.database.as_os_str(),
        OsStr::new("--vault"),
        foundation.vault.as_os_str(),
        OsStr::new("--object"),
        OsStr::new(&content),
    ]);
    let verified = success_envelope(&output, "verify");
    assert_eq!(verified["result"]["object"]["kind"], "verified");
    assert_eq!(verified["result"]["object"]["digest"], content.as_str());
    assert!(verified["result"]["revision"].is_null());

    let output = run([
        OsStr::new("inspect"),
        OsStr::new("foundation"),
        OsStr::new("--project"),
        OsStr::new(PROJECT),
        OsStr::new("--database"),
        foundation.database.as_os_str(),
        OsStr::new("--vault"),
        foundation.vault.as_os_str(),
    ]);
    let inspected = success_envelope(&output, "inspect.foundation");
    assert_eq!(inspected["result"]["counts"]["documents"], 1);
    assert_eq!(inspected["result"]["counts"]["revisions"], 1);
    assert_eq!(inspected["result"]["counts"]["sheets"], 1);
    let rendered = serde_json::to_string(&inspected).expect("re-render the envelope");
    assert!(!rendered.contains(scratch.root.to_str().expect("UTF-8 scratch root")));
}

fn ingest_once(foundation: &Foundation, source: &Path, key: &str) -> Output {
    run([
        OsStr::new("ingest"),
        source.as_os_str(),
        OsStr::new("--project"),
        OsStr::new(PROJECT),
        OsStr::new("--idempotency-key"),
        OsStr::new(key),
        OsStr::new("--database"),
        foundation.database.as_os_str(),
        OsStr::new("--vault"),
        foundation.vault.as_os_str(),
        OsStr::new("--actor"),
        OsStr::new("operator-1"),
    ])
}

#[test]
fn a_quarantined_intake_is_reported_as_exit_twenty_one_on_first_run_and_on_replay() {
    let scratch = Scratch::new();
    let foundation = backup_ready(&scratch);
    let source = scratch.private_file("not-really.pdf", b"%PDF-1.7\nnot a real body\n%%EOF\n");

    let diagnostic = failure_diagnostic(&ingest_once(&foundation, &source, "intake-1"), QUARANTINE);
    assert_eq!(diagnostic["class"], "quarantine");
    assert_eq!(diagnostic["command"], "ingest");
    let message = diagnostic["message"]
        .as_str()
        .expect("string diagnostic message");
    assert!(
        message.contains("quarantined"),
        "the diagnostic must name the quarantine, saw {message}"
    );
    let rendered = stderr_text(&ingest_once(&foundation, &source, "intake-2"));
    assert!(
        !rendered.contains(scratch.root.to_str().expect("UTF-8 scratch root")),
        "the quarantine diagnostic must stay path free"
    );

    // Replaying the same key returns core's retained quarantine record, which
    // is still a quarantine for the operator rather than a success.
    let replayed = failure_diagnostic(&ingest_once(&foundation, &source, "intake-1"), QUARANTINE);
    assert_eq!(replayed["class"], "quarantine");

    // The quarantine is nevertheless durable: core committed its receipt and
    // the audit chain that records it still verifies.
    let verified = success_envelope(
        &run([
            OsStr::new("verify"),
            OsStr::new("--database"),
            foundation.database.as_os_str(),
            OsStr::new("--vault"),
            foundation.vault.as_os_str(),
        ]),
        "verify",
    );
    assert_eq!(verified["result"]["audit_chain"]["valid"], true);
    let inspected = success_envelope(
        &run([
            OsStr::new("inspect"),
            OsStr::new("foundation"),
            OsStr::new("--project"),
            OsStr::new(PROJECT),
            OsStr::new("--database"),
            foundation.database.as_os_str(),
            OsStr::new("--vault"),
            foundation.vault.as_os_str(),
        ]),
        "inspect.foundation",
    );
    assert_eq!(
        inspected["result"]["counts"]["documents"], 0,
        "a quarantined intake creates no document"
    );
}

/// Reads `result.jobs[].job_id` for a project through the public CLI surface.
///
/// The quarantine diagnostic deliberately carries no job identifier, so this
/// inspection envelope is the only operator route from a quarantined intake to
/// the job it produced. No core API or diagnostic field was added for it.
fn inspected_job_ids(foundation: &Foundation, project: &str) -> Vec<String> {
    let inspected = success_envelope(
        &run([
            OsStr::new("inspect"),
            OsStr::new("foundation"),
            OsStr::new("--project"),
            OsStr::new(project),
            OsStr::new("--database"),
            foundation.database.as_os_str(),
            OsStr::new("--vault"),
            foundation.vault.as_os_str(),
        ]),
        "inspect.foundation",
    );
    inspected["result"]["jobs"]
        .as_array()
        .expect("inspection exposes its job list")
        .iter()
        .map(|job| {
            job["job_id"]
                .as_str()
                .expect("every inspected job names its id")
                .to_owned()
        })
        .collect()
}

#[test]
fn resuming_a_job_that_resolved_to_quarantine_is_reported_as_exit_twenty_one() {
    // Break caught: `jobs resume` serialising a retained quarantine receipt
    // into a success envelope, so the operator sees exit 0 for content Heleos
    // refused. The job id is obtained the way an operator would obtain it.
    let scratch = Scratch::new();
    let foundation = backup_ready(&scratch);
    let source = scratch.private_file("quarantined.pdf", b"%PDF-1.7\nnot a real body\n%%EOF\n");
    let diagnostic = failure_diagnostic(
        &ingest_once(&foundation, &source, "quarantine-resume-1"),
        QUARANTINE,
    );
    assert_eq!(diagnostic["class"], "quarantine");

    let jobs = inspected_job_ids(&foundation, PROJECT);
    assert!(
        !jobs.is_empty(),
        "a quarantined intake still records its job for inspection"
    );
    for job in jobs {
        let output = run([
            OsStr::new("jobs"),
            OsStr::new("resume"),
            OsStr::new(&job),
            OsStr::new("--database"),
            foundation.database.as_os_str(),
            OsStr::new("--vault"),
            foundation.vault.as_os_str(),
            OsStr::new("--actor"),
            OsStr::new("operator-1"),
        ]);
        let diagnostic = failure_diagnostic(&output, QUARANTINE);
        assert_eq!(diagnostic["class"], "quarantine");
        assert_eq!(diagnostic["command"], "jobs.resume");
        let message = diagnostic["message"]
            .as_str()
            .expect("string diagnostic message");
        assert!(
            message.contains("quarantined"),
            "the diagnostic must name the quarantine, saw {message}"
        );
        assert!(
            !message.contains(&job),
            "the quarantine diagnostic carries no job identifier"
        );
        let rendered = stderr_text(&output);
        assert!(
            !rendered.contains(scratch.root.to_str().expect("UTF-8 scratch root")),
            "the resumed quarantine diagnostic must stay path free"
        );
    }
}

#[test]
fn jobs_resume_replays_a_completed_job_into_one_path_free_receipt() {
    let scratch = Scratch::new();
    let foundation = backup_ready(&scratch);
    let source = scratch.private_file("page.pdf", &single_page_pdf());
    let ingested = success_envelope(&ingest_once(&foundation, &source, "resume-1"), "ingest");
    let job = ingested["result"]["authoritative_job_id"]
        .as_str()
        .expect("an accepted receipt names its authoritative job")
        .to_owned();

    let output = run([
        OsStr::new("jobs"),
        OsStr::new("resume"),
        OsStr::new(&job),
        OsStr::new("--database"),
        foundation.database.as_os_str(),
        OsStr::new("--vault"),
        foundation.vault.as_os_str(),
        OsStr::new("--actor"),
        OsStr::new("operator-1"),
    ]);
    let resumed = success_envelope(&output, "jobs.resume");
    assert_eq!(resumed["result"]["job_id"], job.as_str());
    // A terminal job replays its own recorded receipt, so the outcome the
    // operator sees is the one the original attempt committed.
    assert_eq!(resumed["result"]["receipt"]["outcome"], "accepted_new");
    assert_eq!(
        resumed["result"]["receipt"]["revision_id"],
        ingested["result"]["revision_id"]
    );
    assert!(resumed["result"]["resumed_attempt"].is_null());
    assert_eq!(
        resumed["result"]["receipt"]["authoritative_job_id"],
        job.as_str()
    );
    assert!(resumed["result"]["receipt"]["quarantine"].is_null());
    let rendered = serde_json::to_string(&resumed).expect("re-render the envelope");
    assert!(
        !rendered.contains(scratch.root.to_str().expect("UTF-8 scratch root")),
        "resume receipts must stay path free"
    );
}

#[test]
fn ingest_into_an_unknown_project_is_not_found() {
    let scratch = Scratch::new();
    let foundation = backup_ready(&scratch);
    let source = scratch.private_file("doc.pdf", b"%PDF-1.7\n%%EOF\n");
    let diagnostic = failure_diagnostic(
        &run([
            OsStr::new("ingest"),
            source.as_os_str(),
            OsStr::new("--project"),
            OsStr::new("33333333-3333-4333-8333-333333333333"),
            OsStr::new("--idempotency-key"),
            OsStr::new("intake-1"),
            OsStr::new("--database"),
            foundation.database.as_os_str(),
            OsStr::new("--vault"),
            foundation.vault.as_os_str(),
            OsStr::new("--actor"),
            OsStr::new("operator-1"),
        ]),
        NOT_FOUND,
    );
    assert_eq!(diagnostic["class"], "not_found");
    assert_eq!(diagnostic["command"], "ingest");
}

#[test]
fn ingest_rejects_absent_and_non_regular_sources() {
    let scratch = Scratch::new();
    let foundation = backup_ready(&scratch);
    for source in [
        scratch.join("absent.pdf"),
        scratch.directory("source-directory"),
    ] {
        let output = run([
            OsStr::new("ingest"),
            source.as_os_str(),
            OsStr::new("--project"),
            OsStr::new(PROJECT),
            OsStr::new("--idempotency-key"),
            OsStr::new("key-1"),
            OsStr::new("--database"),
            foundation.database.as_os_str(),
            OsStr::new("--vault"),
            foundation.vault.as_os_str(),
            OsStr::new("--actor"),
            OsStr::new("operator-1"),
        ]);
        assert_ne!(exit_code(&output), SUCCESS);
        assert!(output.stdout.is_empty());
        let diagnostic: Value =
            serde_json::from_str(stderr_text(&output).trim_end()).expect("one JSON diagnostic");
        assert_eq!(diagnostic["schema"], "heleos.cli-error/v1");
    }
}
