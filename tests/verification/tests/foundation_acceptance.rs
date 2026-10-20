#![forbid(unsafe_code)]

//! Operator-level acceptance: every production action is a separate real CLI process.
//! SQLite's JSON functions decode public envelopes in memory; no database table is read.
//! Injected crash and retention-quota cases remain in the existing core/Task 8 suites:
//! the CLI deliberately exposes neither a fault switch nor a quota override.

use std::{
    ffi::OsStr,
    fs::{self, File},
    io::{Read, Seek, SeekFrom},
    path::{Component, Path, PathBuf},
    process::{Command, Output, Stdio},
    time::{Duration, Instant},
};

use age::secrecy::ExposeSecret;
use heleos_core::Sha256Digest;
use rusqlite::Connection;

const PROJECT: &str = "00000000-0000-4000-8000-000000000091";
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

    fn number(&self, path: &str) -> u64 {
        self.at(path).parse().expect("non-negative integer")
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
    // File-backed capture cannot deadlock when a pipe fills. Both output volume
    // and elapsed time are bounded, and every overrun is killed and reaped.
    let mut stdout = tempfile::tempfile().unwrap();
    let mut stderr = tempfile::tempfile().unwrap();
    let mut child = command
        .stdin(Stdio::null())
        .stdout(stdout.try_clone().unwrap())
        .stderr(stderr.try_clone().unwrap())
        .spawn()
        .expect("start the real CLI");
    let deadline = Instant::now() + Duration::from_secs(180);
    let status = loop {
        if let Some(status) = child.try_wait().unwrap() {
            break status;
        }
        if Instant::now() >= deadline
            || stdout.metadata().unwrap().len() > OUTPUT_LIMIT
            || stderr.metadata().unwrap().len() > OUTPUT_LIMIT
        {
            child.kill().expect("terminate overdue CLI");
            child.wait().expect("reap overdue CLI");
            panic!("CLI exceeded its 180-second or 16-MiB output budget");
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
    assert_eq!(output.stdout.iter().filter(|b| **b == b'\n').count(), 1);
    let json = Json::new(&output.stdout);
    assert_eq!(json.at("$.schema"), "heleos.cli-envelope/v1");
    assert_eq!(json.at("$.command"), command);
    json
}

struct Fixture {
    _temporary: tempfile::TempDir,
    root: PathBuf,
    binary: PathBuf,
    guest: PathBuf,
    database: PathBuf,
    vault: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let temporary = tempfile::tempdir().unwrap();
        heleos_core::apply_private_permissions(temporary.path()).unwrap();
        let root = fs::canonicalize(temporary.path()).unwrap();
        Self {
            database: root.join("foundation.sqlite3"),
            vault: root.join("vault"),
            _temporary: temporary,
            root,
            binary: required_file("HELEOS_BIN"),
            guest: required_file("HELEOS_PDF_GUEST"),
        }
    }

    fn run<S: AsRef<OsStr>>(&self, args: &[S]) -> Output {
        bounded(
            Command::new(&self.binary)
                .current_dir(&self.root)
                .args(args)
                .env("CARGO_NET_OFFLINE", "true"),
        )
    }

    fn initialize(&self) {
        let migration = success(
            self.run(&["db", "migrate", "--database", text(&self.database)]),
            "db.migrate",
        );
        assert_eq!(migration.number("$.result.from_version"), 0);
        assert_eq!(migration.number("$.result.to_version"), 1);
        let repeated = success(
            self.run(&["db", "migrate", "--database", text(&self.database)]),
            "db.migrate",
        );
        assert_eq!(repeated.number("$.result.from_version"), 1);
        assert_eq!(repeated.at("$.result.applied_versions"), "[]");
        let project = success(
            self.run(&[
                "project",
                "create",
                "--database",
                text(&self.database),
                "--id",
                PROJECT,
                "--name",
                "Synthetic Foundation acceptance",
                "--actor",
                "acceptance",
            ]),
            "project.create",
        );
        assert_eq!(project.at("$.result.project_id"), PROJECT);
    }

    fn file(&self, name: &str, bytes: &[u8]) -> PathBuf {
        let path = self.root.join(name);
        fs::write(&path, bytes).unwrap();
        heleos_core::apply_private_permissions(&path).unwrap();
        path
    }

    fn ingest(&self, source: &Path, key: &str) -> Json {
        success(
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
            ]),
            "ingest",
        )
    }

    fn inspect(&self, database: &Path, vault: &Path) -> Json {
        success(
            self.run(&[
                "inspect",
                "foundation",
                "--project",
                PROJECT,
                "--database",
                text(database),
                "--vault",
                text(vault),
            ]),
            "inspect.foundation",
        )
    }

    fn verify(&self, database: &Path, vault: &Path, selector: &[&str]) -> Json {
        let mut args = vec![
            "verify",
            "--database",
            text(database),
            "--vault",
            text(vault),
        ];
        args.extend_from_slice(selector);
        let result = success(self.run(&args), "verify");
        assert_eq!(result.at("$.result.database.clean"), "1");
        assert_eq!(result.at("$.result.audit_chain.valid"), "1");
        assert_eq!(result.at("$.result.audit_chain.findings"), "[]");
        assert_eq!(result.at("$.result.audit_chain.findings_truncated"), "0");
        for key in [
            "integrity_check_violations",
            "quick_check_violations",
            "foreign_key_violations",
        ] {
            assert_eq!(result.at(&format!("$.result.database.{key}")), "[]");
        }
        result
    }
}

fn text(path: &Path) -> &str {
    path.to_str().expect("fixture paths are UTF-8")
}
fn digest(bytes: &[u8]) -> String {
    Sha256Digest::hash_reader(bytes).unwrap().to_string()
}

fn synthetic_pdf(variant: &str) -> Vec<u8> {
    let objects = [
        "<< /Type /Catalog /Pages 2 0 R >>".to_owned(),
        "<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 /MediaBox [0 0 612 792] >>".to_owned(),
        "<< /Type /Page /Parent 2 0 R >>".to_owned(),
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 400] /Rotate 90 >>".to_owned(),
        format!("<< /Producer ({variant}) >>"),
    ];
    let mut bytes = b"%PDF-1.7\n".to_vec();
    let mut offsets = Vec::new();
    for (index, object) in objects.iter().enumerate() {
        offsets.push(bytes.len());
        bytes.extend_from_slice(format!("{} 0 obj\n{object}\nendobj\n", index + 1).as_bytes());
    }
    let xref = bytes.len();
    bytes.extend_from_slice(b"xref\n0 6\n0000000000 65535 f \n");
    for offset in offsets {
        bytes.extend_from_slice(format!("{offset:010} 00000 n \n").as_bytes());
    }
    bytes.extend_from_slice(
        format!("trailer\n<< /Size 6 /Root 1 0 R /Info 5 0 R >>\nstartxref\n{xref}\n%%EOF\n")
            .as_bytes(),
    );
    bytes
}

fn assert_counts(snapshot: &Json, objects: u64, revisions: u64, events: u64, jobs: u64) {
    for (name, count) in [
        ("content_objects", objects),
        ("documents", revisions),
        ("revisions", revisions),
        ("project_documents", revisions),
        ("evidence_objects", revisions),
        ("sheets", revisions * 2),
        ("ingest_events", events),
        ("jobs", jobs),
    ] {
        assert_eq!(
            snapshot.number(&format!("$.result.counts.{name}")),
            count,
            "{name}"
        );
    }
}

fn assert_preserved_events(before: &Json, after: &Json) {
    let after = after.array("$.result.intake_events");
    for event in before.array("$.result.intake_events") {
        let id = event.at("$.ingest_event_id");
        let retained: Vec<_> = after
            .iter()
            .filter(|entry| entry.at("$.ingest_event_id") == id)
            .collect();
        assert_eq!(
            retained.len(),
            1,
            "immutable attempt must survive exactly once"
        );
        assert_eq!(
            retained[0].0, event.0,
            "a subsequent operation rewrote an attempt"
        );
    }
}

fn object_bytes(vault: &Path, key: &str) -> Vec<u8> {
    let key = Path::new(key);
    assert!(!key.is_absolute() && key.components().all(|c| matches!(c, Component::Normal(_))));
    let path = vault.join(key);
    assert!(fs::symlink_metadata(&path).unwrap().file_type().is_file());
    fs::read(path).unwrap()
}

#[test]
fn cli_preserves_canonical_evidence_and_restores_a_verified_backup() {
    // Catches duplicated canonical bytes/revisions, mutable attempt history,
    // wrong page geometry or lineage, unauthenticated backups, and incomplete restores.
    let fixture = Fixture::new();
    fixture.initialize();
    let original = synthetic_pdf("foundation-acceptance-v1");
    let original_hash = digest(&original);
    assert_eq!(original.len(), 527);
    // Independently computed from the hand-authored PDF bytes using Node SHA-256.
    assert_eq!(
        original_hash,
        "1a05143e7f7fc02b45c2d098a22a184f1cbd8fc585c5b3757301ef197d65e676"
    );
    let source = fixture.file("original.pdf", &original);
    let first = fixture.ingest(&source, "first");
    assert_eq!(first.at("$.result.outcome"), "accepted_new");
    assert_eq!(first.at("$.result.content_sha256"), original_hash);
    let first_snapshot = fixture.inspect(&fixture.database, &fixture.vault);
    assert_counts(&first_snapshot, 2, 1, 1, 1);

    let second = fixture.ingest(&source, "second");
    assert_eq!(second.at("$.result.outcome"), "accepted_duplicate");
    for key in ["document_id", "revision_id", "content_sha256"] {
        assert_eq!(
            first.at(&format!("$.result.{key}")),
            second.at(&format!("$.result.{key}"))
        );
    }
    let twice = fixture.inspect(&fixture.database, &fixture.vault);
    assert_counts(&twice, 2, 1, 2, 2);
    assert_preserved_events(&first_snapshot, &twice);
    assert_eq!(
        first_snapshot.at("$.result.sheets"),
        twice.at("$.result.sheets")
    );
    assert_eq!(
        first_snapshot.at("$.result.evidence"),
        twice.at("$.result.evidence")
    );
    let events = twice.array("$.result.intake_events");
    let mut outcomes: Vec<_> = events.iter().map(|event| event.at("$.outcome")).collect();
    outcomes.sort();
    assert_eq!(outcomes, ["accepted_duplicate", "accepted_new"]);

    let revision = first.at("$.result.revision_id");
    let sheets = twice.array("$.result.sheets");
    assert_eq!(sheets.len(), 2);
    let expected = [
        (612_000_000, 792_000_000, 0, [1, 0, 0, -1, 0, 792_000_000]),
        (400_000_000, 300_000_000, 90, [0, 1, 1, 0, 0, 0]),
    ];
    for (index, (sheet, (width, height, rotation, transform))) in
        sheets.iter().zip(expected).enumerate()
    {
        assert_eq!(sheet.number("$.index"), index as u64);
        assert_eq!(sheet.number("$.width_micropoints"), width);
        assert_eq!(sheet.number("$.height_micropoints"), height);
        assert_eq!(sheet.number("$.rotation_degrees"), rotation);
        assert_eq!(sheet.at("$.unit"), "pt");
        assert_eq!(sheet.at("$.revision_id"), revision);
        assert_eq!(sheet.at("$.parent_content_sha256"), original_hash);
        for (field, value) in [
            "m11",
            "m12",
            "m21",
            "m22",
            "tx_micropoints",
            "ty_micropoints",
        ]
        .iter()
        .zip(transform)
        {
            assert_eq!(sheet.at(&format!("$.transform.{field}")), value.to_string());
        }
    }
    assert_ne!(sheets[0].at("$.sheet_id"), sheets[1].at("$.sheet_id"));
    let evidence = &twice.array("$.result.evidence")[0];
    assert_eq!(
        evidence.at("$.originating_job_id"),
        first.at("$.result.authoritative_job_id")
    );
    assert_eq!(
        evidence.at("$.document_id"),
        first.at("$.result.document_id")
    );
    assert_eq!(evidence.at("$.revision_id"), revision);
    assert_eq!(evidence.at("$.review_state"), "accepted");
    assert_eq!(evidence.at("$.extraction_method"), "heleos.pdf-probe/v1");
    assert_eq!(
        evidence.at("$.probe_provenance.guest_wasm_sha256"),
        digest(&fs::read(&fixture.guest).unwrap())
    );
    assert_eq!(
        object_bytes(&fixture.vault, &evidence.at("$.original.vault_key")),
        original
    );
    let manifest_bytes = object_bytes(&fixture.vault, &evidence.at("$.manifest.vault_key"));
    assert_eq!(digest(&manifest_bytes), evidence.at("$.manifest.sha256"));
    assert_eq!(
        manifest_bytes.len() as u64,
        evidence.number("$.manifest.byte_length")
    );
    let manifest = Json::new(&manifest_bytes);
    assert_eq!(manifest.at("$.schema"), "heleos.evidence-manifest/v1");
    assert_eq!(manifest.at("$.revision_id"), revision);
    assert_eq!(manifest.at("$.original.sha256"), original_hash);
    assert_eq!(
        manifest.number("$.original.byte_length"),
        original.len() as u64
    );
    for (sheet, page) in sheets.iter().zip(manifest.array("$.pages")) {
        assert_eq!(sheet.at("$.sheet_id"), page.at("$.page_id"));
        assert_eq!(sheet.at("$.transform"), page.at("$.transform"));
    }

    let renamed = fixture.file("renamed.pdf", &original);
    let renamed_receipt = fixture.ingest(&renamed, "renamed");
    assert_eq!(renamed_receipt.at("$.result.outcome"), "accepted_duplicate");
    assert_eq!(renamed_receipt.at("$.result.revision_id"), revision);
    let after_rename = fixture.inspect(&fixture.database, &fixture.vault);
    assert_counts(&after_rename, 2, 1, 3, 3);
    assert_preserved_events(&twice, &after_rename);

    let near_bytes = synthetic_pdf("foundation-acceptance-v2");
    assert_ne!(digest(&near_bytes), original_hash);
    let near = fixture.file("near-duplicate.pdf", &near_bytes);
    let near_receipt = fixture.ingest(&near, "near");
    assert_eq!(near_receipt.at("$.result.outcome"), "accepted_new");
    assert_ne!(near_receipt.at("$.result.revision_id"), revision);
    let before_replay = fixture.inspect(&fixture.database, &fixture.vault);
    assert_counts(&before_replay, 4, 2, 4, 4);
    let replay = fixture.ingest(&source, "first");
    assert_eq!(replay.at("$.result.outcome"), "idempotent_replay");
    assert_eq!(
        replay.at("$.result.authoritative_job_id"),
        first.at("$.result.authoritative_job_id")
    );
    let job = first.at("$.result.authoritative_job_id");
    let resumed = success(
        fixture.run(&[
            "jobs",
            "resume",
            &job,
            "--database",
            text(&fixture.database),
            "--vault",
            text(&fixture.vault),
            "--actor",
            "acceptance",
        ]),
        "jobs.resume",
    );
    assert_eq!(resumed.at("$.result.receipt.revision_id"), revision);
    let final_snapshot = fixture.inspect(&fixture.database, &fixture.vault);
    assert_counts(&final_snapshot, 4, 2, 5, 4);
    assert_preserved_events(&before_replay, &final_snapshot);
    assert_eq!(
        before_replay.at("$.result.sheets"),
        final_snapshot.at("$.result.sheets")
    );
    assert!(
        final_snapshot
            .array("$.result.jobs")
            .iter()
            .all(|job| job.at("$.state") == "succeeded")
    );
    assert_eq!(fs::read(&source).unwrap(), original);
    assert_eq!(digest(&fs::read(&renamed).unwrap()), original_hash);

    let source_verification = fixture.verify(&fixture.database, &fixture.vault, &[]);
    let verified = fixture.verify(
        &fixture.database,
        &fixture.vault,
        &["--revision", &revision],
    );
    assert_eq!(verified.at("$.result.revision.original.kind"), "verified");
    assert_eq!(verified.at("$.result.revision.manifest.kind"), "verified");
    for object in final_snapshot.array("$.result.content_objects") {
        let hash = object.at("$.sha256");
        let result = fixture.verify(&fixture.database, &fixture.vault, &["--object", &hash]);
        assert_eq!(result.at("$.result.object.kind"), "verified");
        assert_eq!(result.at("$.result.object.digest"), hash);
        assert_eq!(
            digest(&object_bytes(&fixture.vault, &object.at("$.vault_key"))),
            hash
        );
    }

    let identity = age::x25519::Identity::generate();
    let identity_path = fixture.file(
        "identity.key",
        identity.to_string().expose_secret().as_bytes(),
    );
    let mut seed = [0_u8; 32];
    seed[..16].copy_from_slice(uuid::Uuid::new_v4().as_bytes());
    seed[16..].copy_from_slice(uuid::Uuid::new_v4().as_bytes());
    let signer = ed25519_dalek::SigningKey::from_bytes(&seed);
    let signing_path = fixture.file("signing.key", &signer.to_bytes());
    let trusted_path = fixture.file("trusted.pub", &signer.verifying_key().to_bytes());
    let recipient = identity.to_public().to_string();
    let backup = fixture.root.join("foundation.age");
    let created = success(
        fixture.run(&[
            "backup",
            "create",
            text(&backup),
            "--database",
            text(&fixture.database),
            "--vault",
            text(&fixture.vault),
            "--recipient",
            &recipient,
            "--signing-key",
            text(&signing_path),
        ]),
        "backup.create",
    );
    assert_eq!(created.number("$.result.container.entry_count"), 5);
    assert_eq!(
        created.at("$.result.container.signer_key_sha256"),
        digest(&signer.verifying_key().to_bytes())
    );
    independently_check_backup(&backup, &identity, &signer.verifying_key());
    let checked = success(
        fixture.run(&[
            "backup",
            "verify",
            text(&backup),
            "--identity",
            text(&identity_path),
            "--trusted-signer",
            text(&trusted_path),
        ]),
        "backup.verify",
    );
    assert_eq!(
        checked.at("$.result.container"),
        created.at("$.result.container")
    );
    assert_eq!(
        checked.at("$.result.audit_head_hash"),
        source_verification.at("$.result.audit_chain.head_hash")
    );
    assert_eq!(
        checked.number("$.result.checked_audit_event_count"),
        source_verification.number("$.result.audit_chain.checked_event_count")
    );

    let restored = fixture.root.join("restored");
    assert!(!restored.exists());
    let receipt = success(
        fixture.run(&[
            "restore",
            text(&backup),
            "--into",
            text(&restored),
            "--identity",
            text(&identity_path),
            "--trusted-signer",
            text(&trusted_path),
            "--verify",
        ]),
        "restore",
    );
    assert_eq!(receipt.at("$.result.verification"), checked.at("$.result"));
    let restored_db = restored.join("foundation.sqlite3");
    let restored_vault = restored.join("vault");
    let restored_state = fixture.inspect(&restored_db, &restored_vault);
    assert_eq!(restored_state.at("$.result"), final_snapshot.at("$.result"));
    for restored_revision in [revision, near_receipt.at("$.result.revision_id")] {
        let result = fixture.verify(
            &restored_db,
            &restored_vault,
            &["--revision", &restored_revision],
        );
        assert_eq!(result.at("$.result.revision.original.kind"), "verified");
        assert_eq!(result.at("$.result.revision.manifest.kind"), "verified");
    }
    for object in restored_state.array("$.result.content_objects") {
        assert_eq!(
            digest(&object_bytes(&restored_vault, &object.at("$.vault_key"))),
            object.at("$.sha256")
        );
    }
}

fn independently_check_backup(
    path: &Path,
    identity: &age::x25519::Identity,
    trusted: &ed25519_dalek::VerifyingKey,
) {
    // Independent wire parsing and Ed25519 verification: no BackupService parser
    // or DTO supplies the expected signer, manifest, table, or payload hashes.
    let ciphertext = File::open(path).unwrap();
    let decryptor = age::Decryptor::new(ciphertext).unwrap();
    let mut plaintext = Vec::new();
    decryptor
        .decrypt(std::iter::once(identity as &dyn age::Identity))
        .unwrap()
        .take(OUTPUT_LIMIT + 1)
        .read_to_end(&mut plaintext)
        .unwrap();
    assert!(plaintext.len() as u64 <= OUTPUT_LIMIT);
    let mut cursor = plaintext.as_slice();
    assert_eq!(take(&mut cursor, 8), b"HELEOSB1");
    let manifest_len =
        usize::try_from(u64::from_be_bytes(take(&mut cursor, 8).try_into().unwrap())).unwrap();
    assert!(manifest_len <= 1024 * 1024);
    let manifest_bytes = take(&mut cursor, manifest_len);
    assert_eq!(take(&mut cursor, 32), trusted.to_bytes());
    let signature = ed25519_dalek::Signature::from_slice(take(&mut cursor, 64)).unwrap();
    let mut signed = b"heleos-backup-v1\0".to_vec();
    signed.extend_from_slice(manifest_bytes);
    trusted.verify_strict(&signed, &signature).unwrap();
    let manifest = Json::new(manifest_bytes);
    assert_eq!(
        manifest.at("$.signer_key_sha256"),
        digest(&trusted.to_bytes())
    );
    let table_len =
        usize::try_from(u64::from_be_bytes(take(&mut cursor, 8).try_into().unwrap())).unwrap();
    let table = take(&mut cursor, table_len);
    assert_eq!(digest(table), manifest.at("$.entry_table_sha256"));
    assert_eq!(
        table_len as u64,
        manifest.number("$.entry_table_byte_length")
    );
    assert_eq!(table_len, 5 * 41);
    assert_eq!(
        cursor.len() as u64,
        manifest.number("$.decoded_payload_byte_length")
    );
    for (index, row) in table.chunks_exact(41).enumerate() {
        assert_eq!(row[0], u8::from(index != 0));
        let count = usize::try_from(u64::from_be_bytes(row[33..41].try_into().unwrap())).unwrap();
        let bytes = take(&mut cursor, count);
        assert_eq!(
            Sha256Digest::hash_reader(bytes).unwrap().as_bytes(),
            &row[1..33]
        );
        if index == 0 {
            assert_eq!(digest(bytes), manifest.at("$.database.sha256"));
            assert_eq!(count as u64, manifest.number("$.database.byte_length"));
        }
    }
    assert!(cursor.is_empty(), "backup has unaccounted payload bytes");
}

fn take<'a>(input: &mut &'a [u8], count: usize) -> &'a [u8] {
    assert!(count <= input.len(), "truncated backup grammar");
    let (prefix, suffix) = input.split_at(count);
    *input = suffix;
    prefix
}
