#![forbid(unsafe_code)]

//! Rejected input remains auditable and never becomes authoritative through replay.
//! Fixtures cross the actual CLI defaults; they do not configure production quotas.
//! Multi-GiB retention-quota and injected-crash coverage remains in core/Task 8,
//! because there is no public CLI override for either boundary.

use std::{
    fs::{self, File},
    io::{Read, Seek, SeekFrom},
    path::{Path, PathBuf},
    process::{Command, Output, Stdio},
    time::{Duration, Instant},
};

use rusqlite::Connection;

const PROJECT: &str = "00000000-0000-4000-8000-000000000092";
const OUTPUT_LIMIT: u64 = 16 * 1024 * 1024;

struct Json(String);

impl Json {
    fn new(bytes: &[u8]) -> Self {
        let text = String::from_utf8(bytes.to_vec()).expect("JSON is UTF-8");
        let db = Connection::open_in_memory().unwrap();
        assert_eq!(
            db.query_row("SELECT json_valid(?1)", [&text], |r| r.get::<_, i64>(0))
                .unwrap(),
            1
        );
        Self(text)
    }

    fn at(&self, path: &str) -> String {
        Connection::open_in_memory()
            .unwrap()
            .query_row(
                "SELECT CAST(json_extract(?1, ?2) AS TEXT)",
                [self.0.as_str(), path],
                |r| r.get::<_, String>(0),
            )
            .unwrap_or_else(|e| panic!("required JSON field {path}: {e}; {}", self.0))
    }

    fn array(&self, path: &str) -> Vec<Self> {
        let db = Connection::open_in_memory().unwrap();
        let mut statement = db.prepare("SELECT value FROM json_each(?1, ?2)").unwrap();
        statement
            .query_map([self.0.as_str(), path], |r| r.get::<_, String>(0))
            .unwrap()
            .map(|r| Self(r.unwrap()))
            .collect()
    }
}

fn text(path: &Path) -> &str {
    path.to_str().expect("fixture paths are UTF-8")
}

fn required_file(variable: &str) -> PathBuf {
    let path = PathBuf::from(
        std::env::var_os(variable).unwrap_or_else(|| panic!("{variable} is required")),
    );
    assert!(
        path.is_absolute() && path.is_file(),
        "{variable} must name the built absolute artifact"
    );
    path
}

fn bounded(command: &mut Command) -> Output {
    let mut stdout = tempfile::tempfile().unwrap();
    let mut stderr = tempfile::tempfile().unwrap();
    let mut child = command
        .stdin(Stdio::null())
        .stdout(stdout.try_clone().unwrap())
        .stderr(stderr.try_clone().unwrap())
        .spawn()
        .expect("run the acceptance command");
    let deadline = Instant::now() + Duration::from_secs(180);
    let status = loop {
        if let Some(status) = child.try_wait().unwrap() {
            break status;
        }
        if Instant::now() >= deadline
            || stdout.metadata().unwrap().len() > OUTPUT_LIMIT
            || stderr.metadata().unwrap().len() > OUTPUT_LIMIT
        {
            child.kill().expect("terminate overdue command");
            child.wait().expect("reap overdue command");
            panic!("acceptance command exceeded its 180-second or 16-MiB output budget");
        }
        std::thread::sleep(Duration::from_millis(20));
    };
    assert!(stdout.metadata().unwrap().len() <= OUTPUT_LIMIT);
    assert!(stderr.metadata().unwrap().len() <= OUTPUT_LIMIT);
    stdout.seek(SeekFrom::Start(0)).unwrap();
    stderr.seek(SeekFrom::Start(0)).unwrap();
    let mut output = Output {
        status,
        stdout: Vec::new(),
        stderr: Vec::new(),
    };
    stdout.read_to_end(&mut output.stdout).unwrap();
    stderr.read_to_end(&mut output.stderr).unwrap();
    output
}

fn success(output: Output, command: &str) -> Json {
    assert_eq!(
        output.status.code(),
        Some(0),
        "{command}: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(output.stderr.is_empty());
    let json = Json::new(&output.stdout);
    assert_eq!(json.at("$.schema"), "heleos.cli-envelope/v1");
    assert_eq!(json.at("$.command"), command);
    json
}

fn quarantined(output: Output, command: &str) {
    assert_eq!(
        output.status.code(),
        Some(21),
        "expected quarantine: stdout={} stderr={}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(output.stdout.is_empty());
    let diagnostic = Json::new(&output.stderr);
    assert_eq!(diagnostic.at("$.schema"), "heleos.cli-error/v1");
    assert_eq!(diagnostic.at("$.command"), command);
    assert_eq!(diagnostic.at("$.class"), "quarantine");
    assert_eq!(diagnostic.at("$.exit_code"), "21");
}

struct Fixture {
    _temporary: tempfile::TempDir,
    root: PathBuf,
    binary: PathBuf,
    database: PathBuf,
    vault: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let temporary = tempfile::tempdir().unwrap();
        heleos_core::apply_private_permissions(temporary.path()).unwrap();
        let root = fs::canonicalize(temporary.path()).unwrap();
        required_file("HELEOS_PDF_GUEST");
        let fixture = Self {
            database: root.join("foundation.sqlite3"),
            vault: root.join("vault"),
            _temporary: temporary,
            root,
            binary: required_file("HELEOS_BIN"),
        };
        success(
            fixture.run(&["db", "migrate", "--database", text(&fixture.database)]),
            "db.migrate",
        );
        success(
            fixture.run(&[
                "project",
                "create",
                "--database",
                text(&fixture.database),
                "--id",
                PROJECT,
                "--name",
                "Hostile synthetic intake",
                "--actor",
                "acceptance",
            ]),
            "project.create",
        );
        fixture
    }

    fn run(&self, args: &[&str]) -> Output {
        bounded(
            Command::new(&self.binary)
                .current_dir(&self.root)
                .args(args)
                .env("CARGO_NET_OFFLINE", "true"),
        )
    }

    fn file(&self, bytes: &[u8]) -> PathBuf {
        let path = self.root.join("input.pdf");
        fs::write(&path, bytes).unwrap();
        heleos_core::apply_private_permissions(&path).unwrap();
        path
    }

    fn ingest(&self, source: &Path, key: &str) -> Output {
        self.run(&[
            "ingest",
            text(source),
            "--project",
            PROJECT,
            "--idempotency-key",
            key,
            "--database",
            text(&self.database),
            "--vault",
            text(&self.vault),
            "--actor",
            "acceptance",
        ])
    }

    fn inspect(&self) -> Json {
        success(
            self.run(&[
                "inspect",
                "foundation",
                "--project",
                PROJECT,
                "--database",
                text(&self.database),
                "--vault",
                text(&self.vault),
            ]),
            "inspect.foundation",
        )
    }

    fn assert_no_authoritative_state(&self, expected_events: usize, expected_jobs: usize) -> Json {
        let snapshot = self.inspect();
        for name in [
            "documents",
            "revisions",
            "project_documents",
            "sheets",
            "evidence_objects",
        ] {
            assert_eq!(
                snapshot.at(&format!("$.result.counts.{name}")),
                "0",
                "rejected input published {name}"
            );
        }
        assert_eq!(
            snapshot.array("$.result.intake_events").len(),
            expected_events
        );
        assert_eq!(snapshot.array("$.result.jobs").len(), expected_jobs);
        assert!(
            snapshot
                .array("$.result.jobs")
                .iter()
                .all(|j| j.at("$.state") == "succeeded" && j.at("$.terminal_reason") == "completed")
        );
        let verified = success(
            self.run(&[
                "verify",
                "--database",
                text(&self.database),
                "--vault",
                text(&self.vault),
            ]),
            "verify",
        );
        assert_eq!(verified.at("$.result.database.clean"), "1");
        assert_eq!(verified.at("$.result.audit_chain.valid"), "1");
        assert_eq!(verified.at("$.result.audit_chain.findings"), "[]");
        snapshot
    }

    fn rejection_lifecycle(
        &self,
        source: &Path,
        outcome: &str,
        reason: Option<(&str, Option<&str>)>,
    ) {
        let before_hash =
            heleos_core::Sha256Digest::hash_reader(File::open(source).unwrap()).unwrap();
        quarantined(self.ingest(source, "first-rejection"), "ingest");
        let first = self.assert_no_authoritative_state(1, 1);
        assert_eq!(first.at("$.result.intake_events[0].outcome"), outcome);
        if let Some((kind, detail)) = reason {
            let objects = first.array("$.result.content_objects");
            assert_eq!(
                objects.len(),
                1,
                "bounded rejected original remains retrievable"
            );
            assert_eq!(objects[0].at("$.admission_state"), "quarantined");
            assert_eq!(objects[0].at("$.quarantine.reason.kind"), "pdf");
            assert_eq!(objects[0].at("$.quarantine.reason.detail.kind"), kind);
            if let Some(detail) = detail {
                assert_eq!(objects[0].at("$.quarantine.reason.detail.detail"), detail);
            }
            assert_eq!(objects[0].at("$.sha256"), before_hash.to_string());
        }
        let repeated = self.ingest(source, "first-rejection");
        if reason.is_none() {
            // The input cap prevented hashing, so equal size/name is not proof
            // of equal content. The same key must record a conflict, not replay.
            assert_eq!(repeated.status.code(), Some(22));
            assert!(repeated.stdout.is_empty());
            let diagnostic = Json::new(&repeated.stderr);
            assert_eq!(diagnostic.at("$.schema"), "heleos.cli-error/v1");
            assert_eq!(diagnostic.at("$.class"), "idempotency_conflict");
            assert_eq!(diagnostic.at("$.command"), "ingest");
            assert_eq!(diagnostic.at("$.exit_code"), "22");
        } else {
            quarantined(repeated, "ingest");
        }
        quarantined(self.ingest(source, "second-rejection"), "ingest");
        let after = self.assert_no_authoritative_state(3, 2);
        let mut outcomes: Vec<_> = after
            .array("$.result.intake_events")
            .iter()
            .map(|event| event.at("$.outcome"))
            .collect();
        let mut expected = vec![
            outcome.to_owned(),
            outcome.to_owned(),
            if reason.is_none() {
                "denied_conflict"
            } else {
                "idempotent_replay"
            }
            .to_owned(),
        ];
        outcomes.sort();
        expected.sort();
        assert_eq!(outcomes, expected);
        assert_eq!(
            after.at("$.result.content_objects"),
            first.at("$.result.content_objects")
        );
        let old_event = first.at("$.result.intake_events[0]");
        assert_eq!(
            after
                .array("$.result.intake_events")
                .iter()
                .filter(|event| event.0 == old_event)
                .count(),
            1,
            "replay or repeat must not rewrite the original attempt"
        );
        for job in after.array("$.result.jobs") {
            quarantined(
                self.run(&[
                    "jobs",
                    "resume",
                    &job.at("$.job_id"),
                    "--database",
                    text(&self.database),
                    "--vault",
                    text(&self.vault),
                    "--actor",
                    "acceptance",
                ]),
                "jobs.resume",
            );
        }
        let resumed = self.assert_no_authoritative_state(3, 2);
        assert_eq!(
            resumed.at("$.result.intake_events"),
            after.at("$.result.intake_events")
        );
        assert_eq!(
            heleos_core::Sha256Digest::hash_reader(File::open(source).unwrap()).unwrap(),
            before_hash
        );
    }
}

/// Small deterministic classic PDF encoder; every xref points to its actual object.
fn pdf(objects: &[String], extra_trailer: &str) -> Vec<u8> {
    let mut bytes = b"%PDF-1.7\n".to_vec();
    let mut offsets = Vec::new();
    for (index, object) in objects.iter().enumerate() {
        offsets.push(bytes.len());
        bytes.extend_from_slice(format!("{} 0 obj\n{object}\nendobj\n", index + 1).as_bytes());
    }
    let xref = bytes.len();
    bytes.extend_from_slice(
        format!("xref\n0 {}\n0000000000 65535 f \n", objects.len() + 1).as_bytes(),
    );
    for offset in offsets {
        bytes.extend_from_slice(format!("{offset:010} 00000 n \n").as_bytes());
    }
    bytes.extend_from_slice(
        format!(
            "trailer\n<< /Size {} /Root 1 0 R {extra_trailer} >>\nstartxref\n{xref}\n%%EOF\n",
            objects.len() + 1
        )
        .as_bytes(),
    );
    bytes
}

fn one_page() -> Vec<String> {
    vec![
        "<< /Type /Catalog /Pages 2 0 R >>".to_owned(),
        "<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 612 792] >>".to_owned(),
        "<< /Type /Page /Parent 2 0 R >>".to_owned(),
    ]
}

#[test]
fn corrupt_input_is_durable_quarantine_after_replay_and_restart() {
    let fixture = Fixture::new();
    let source = fixture.file(b"%PDF-1.7\nnot a PDF object graph\n%%EOF\n");
    fixture.rejection_lifecycle(&source, "quarantined_corrupt", Some(("corrupt", None)));
}

#[test]
fn encrypted_input_never_publishes_a_revision() {
    let fixture = Fixture::new();
    let mut objects = one_page();
    // Empty-page standard-security PDF: the encrypted flag is authoritative
    // even without a content stream requiring decryption.
    objects.push(format!(
        "<< /Filter /Standard /V 1 /R 2 /Length 40 /O <{}> /U <{}> /P -4 >>",
        "00".repeat(32),
        "11".repeat(32)
    ));
    let source = fixture.file(&pdf(
        &objects,
        "/Encrypt 4 0 R /ID [<00112233445566778899aabbccddeeff><00112233445566778899aabbccddeeff>]",
    ));
    fixture.rejection_lifecycle(&source, "quarantined_encrypted", Some(("encrypted", None)));
}

#[test]
fn active_content_is_classified_without_execution() {
    let fixture = Fixture::new();
    let mut objects = one_page();
    objects[0] = "<< /Type /Catalog /Pages 2 0 R /OpenAction 4 0 R >>".to_owned();
    objects.push("<< /Type /Action /S /JavaScript /JS (inert synthetic action) >>".to_owned());
    let source = fixture.file(&pdf(&objects, ""));
    fixture.rejection_lifecycle(
        &source,
        "quarantined_suspicious",
        Some(("active_feature", Some("open_action"))),
    );
}

#[test]
fn input_byte_quota_rejects_before_retaining_the_original() {
    let fixture = Fixture::new();
    let source = fixture.file(b"%PDF-1.7\n");
    // A sparse input crosses the literal public 256-MiB cap with negligible disk use.
    File::options()
        .write(true)
        .open(&source)
        .unwrap()
        .set_len(268_435_457)
        .unwrap();
    fixture.rejection_lifecycle(&source, "quarantined_limit", None);
    assert_eq!(fixture.inspect().at("$.result.counts.content_objects"), "0");
}

#[test]
fn page_count_limit_rejects_a_well_formed_page_tree() {
    let fixture = Fixture::new();
    let count = 10_001;
    let kids = (3..count + 3)
        .map(|n| format!("{n} 0 R"))
        .collect::<Vec<_>>()
        .join(" ");
    let mut objects = vec![
        "<< /Type /Catalog /Pages 2 0 R >>".to_owned(),
        format!("<< /Type /Pages /Kids [{kids}] /Count {count} /MediaBox [0 0 612 792] >>"),
    ];
    objects.extend(std::iter::repeat_n(
        "<< /Type /Page /Parent 2 0 R >>".to_owned(),
        count,
    ));
    let source = fixture.file(&pdf(&objects, ""));
    fixture.rejection_lifecycle(
        &source,
        "quarantined_limit",
        Some(("limit_exceeded", Some("pages"))),
    );
}

#[test]
fn indirect_object_limit_rejects_before_the_full_object_graph_is_loaded() {
    let fixture = Fixture::new();
    let mut objects = one_page();
    objects.resize(250_001, "<< /Synthetic true >>".to_owned());
    let source = fixture.file(&pdf(&objects, ""));
    fixture.rejection_lifecycle(
        &source,
        "quarantined_limit",
        Some(("limit_exceeded", Some("indirect_objects"))),
    );
}

#[test]
fn z_task9_provenance_entry_point_runs_and_reports_the_governed_source_set() {
    // Missing or inert wrappers must fail acceptance even when the CLI itself works.
    // The normal provenance command must not recursively run this acceptance suite.
    let repository = Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .parent()
        .unwrap();
    for entry in [
        "scripts/verify-foundation",
        "scripts/verify-foundation.ps1",
        "scripts/verify-provenance",
    ] {
        assert!(
            repository.join(entry).is_file(),
            "Task 9 acceptance/provenance entry point is missing: {entry}"
        );
    }
    #[cfg(unix)]
    let program = repository.join("scripts/verify-provenance");
    #[cfg(windows)]
    let program = required_file("HELEOS_BIN").with_file_name("verify-provenance.exe");
    let output = bounded(
        Command::new(program)
            .current_dir(repository)
            .env("CARGO_NET_OFFLINE", "true"),
    );
    assert_eq!(
        output.status.code(),
        Some(0),
        "provenance command failed: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(output.stderr.is_empty());
    let report = Json::new(&output.stdout);
    assert_eq!(report.at("$.schema"), "heleos.provenance-report/v1");
    assert_eq!(report.at("$.status"), "pass");
}
