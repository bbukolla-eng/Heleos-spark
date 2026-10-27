#![forbid(unsafe_code)]

use std::env;
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use fs2::FileExt;
use heleos_core::{
    HeleosError, INTEGRITY_VIOLATION_LIMIT, Store, apply_private_permissions,
    verify_private_permissions,
};
#[cfg(unix)]
use rusqlite::OpenFlags;
use rusqlite::functions::FunctionFlags;
use rusqlite::{Connection, Transaction, TransactionBehavior, params};
use uuid::Uuid;

const TABLES: [&str; 14] = [
    "audit_events",
    "content_objects",
    "corrections",
    "document_revisions",
    "documents",
    "evidence_objects",
    "ingest_events",
    "job_runs",
    "project_documents",
    "projects",
    "scales",
    "schema_migrations",
    "sheets",
    "source_records",
];

struct TestDatabase {
    root: PathBuf,
    path: PathBuf,
}

impl TestDatabase {
    fn new(label: &str) -> Self {
        let root = env::temp_dir().join(format!("heleos-{label}-{}", Uuid::new_v4()));
        fs::create_dir(&root).expect("create isolated test directory");
        let root = fs::canonicalize(root).expect("resolve physical test directory path");
        let path = root.join("foundation.sqlite3");
        Self { root, path }
    }
}

impl Drop for TestDatabase {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

fn database_sidecar(database: &Path, suffix: &str) -> PathBuf {
    let mut path = database.as_os_str().to_owned();
    path.push(suffix);
    PathBuf::from(path)
}

#[cfg(unix)]
#[derive(Debug, Eq, PartialEq)]
struct UnixMetadataSnapshot {
    device: u64,
    inode: u64,
    mode: u32,
    links: u64,
    owner: u32,
    group: u32,
    length: u64,
    modified_seconds: i64,
    modified_nanoseconds: i64,
    changed_seconds: i64,
    changed_nanoseconds: i64,
}

#[cfg(unix)]
impl UnixMetadataSnapshot {
    fn read(path: &Path) -> Self {
        use std::os::unix::fs::MetadataExt;

        let metadata = fs::symlink_metadata(path).expect("read metadata snapshot");
        Self {
            device: metadata.dev(),
            inode: metadata.ino(),
            mode: metadata.mode(),
            links: metadata.nlink(),
            owner: metadata.uid(),
            group: metadata.gid(),
            length: metadata.size(),
            modified_seconds: metadata.mtime(),
            modified_nanoseconds: metadata.mtime_nsec(),
            changed_seconds: metadata.ctime(),
            changed_nanoseconds: metadata.ctime_nsec(),
        }
    }
}

fn migrated_store(database: &TestDatabase) -> Store {
    let mut store = Store::open_writer(&database.path).expect("open writer");
    store.migrate().expect("migrate database");
    store
}

fn raw_verifier_connection(database: &TestDatabase) -> Connection {
    raw_verifier_connection_path(&database.path)
}

fn raw_verifier_connection_path(path: &Path) -> Connection {
    let connection = Connection::open(path).expect("open verifier-only connection");
    let flags = FunctionFlags::SQLITE_UTF8
        | FunctionFlags::SQLITE_DETERMINISTIC
        | FunctionFlags::SQLITE_INNOCUOUS;
    connection
        .create_scalar_function("heleos_is_jcs", 1, flags, |context| {
            let text = context.get::<String>(0)?;
            let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) else {
                return Ok(false);
            };
            let Ok(canonical) = serde_jcs::to_string(&value) else {
                return Ok(false);
            };
            Ok(canonical == text)
        })
        .expect("register verifier JCS validator");
    connection
        .create_scalar_function("heleos_is_uuid", 1, flags, |context| {
            let text = context.get::<String>(0)?;
            Ok(Uuid::parse_str(&text).is_ok_and(|value| value.hyphenated().to_string() == text))
        })
        .expect("register verifier UUID validator");
    connection
        .create_scalar_function("heleos_valid_text", 3, flags, |context| {
            let text = context.get::<String>(0)?;
            let max_bytes = context.get::<i64>(1)?;
            let allow_ordinary_whitespace = context.get::<i64>(2)? != 0;
            let valid_control = |character: char| {
                allow_ordinary_whitespace && matches!(character, '\n' | '\r' | '\t')
            };
            let valid = u64::try_from(max_bytes).is_ok_and(|maximum| {
                !text.is_empty()
                    && u64::try_from(text.len()).is_ok_and(|length| length <= maximum)
                    && !text
                        .chars()
                        .any(|character| character.is_control() && !valid_control(character))
            });
            Ok(valid)
        })
        .expect("register verifier text validator");
    connection
        .pragma_update(None, "foreign_keys", "ON")
        .expect("enable verifier foreign keys");
    connection
        .pragma_update(None, "trusted_schema", "OFF")
        .expect("disable verifier trusted schema");
    connection
}

fn with_raw_verifier_transaction<T>(
    database: &TestDatabase,
    operation: impl FnOnce(&Transaction<'_>) -> Result<T, HeleosError>,
) -> Result<T, HeleosError> {
    let mut connection = raw_verifier_connection(database);
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .expect("begin verifier-only transaction");
    let value = operation(&transaction)?;
    transaction
        .commit()
        .expect("commit verifier-only transaction");
    Ok(value)
}

fn insert_job_test_project(transaction: &Transaction<'_>, project_id: &str) {
    transaction
        .execute(
            "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
             VALUES (?1, ?1, 0, 'actor', 'INTERNAL')",
            [project_id],
        )
        .expect("insert job-test project");
}

fn insert_queued_job(transaction: &Transaction<'_>, job_id: &str, project_id: &str, key: &str) {
    transaction
        .execute(
            "INSERT INTO job_runs
                (id, project_id, kind, idempotency_key, state, attempt, deadline_at_ms,
                 budget_json, input_json, checkpoint_json, created_at_ms, updated_at_ms)
             VALUES (?1, ?2, 'pdf_ingest', ?3, 'queued', 0, 300000,
                     '{}', '{}', '{}', 0, 0)",
            params![job_id, project_id, key],
        )
        .expect("insert queued job fixture");
}

fn start_job(transaction: &Transaction<'_>, job_id: &str, updated_at_ms: i64) {
    transaction
        .execute(
            "UPDATE job_runs
             SET state = 'running', attempt = 1,
                 lease_owner = '00000000-0000-4000-8000-000000000001',
                 lease_expires_at_ms = 30000, updated_at_ms = ?2
             WHERE id = ?1",
            params![job_id, updated_at_ms],
        )
        .expect("start job fixture");
}

fn assert_sql_error_contains(result: rusqlite::Result<usize>, expected: &str) {
    let error = result.expect_err("hostile SQL unexpectedly succeeded");
    assert!(
        error.to_string().contains(expected),
        "unexpected SQLite error {error:?}; expected {expected:?}"
    );
}

fn canonical_json_string_with_exact_bytes(byte_length: usize) -> String {
    assert!(byte_length >= 2);
    format!("\"{}\"", "x".repeat(byte_length - 2))
}

fn spawn_crash_left_writer(database: &TestDatabase) {
    let status = Command::new(env::current_exe().expect("locate test executable"))
        .arg("--exact")
        .arg("crash_left_wal_process_helper")
        .arg("--nocapture")
        .env("HELEOS_TEST_CRASH_DATABASE", &database.path)
        .status()
        .expect("spawn crash-left WAL fixture writer");
    assert!(status.success(), "crash-left WAL fixture failed");
}

fn insert_complete_fixture(database: &TestDatabase, payload: &str) {
    let json = serde_json::to_string(&payload).expect("encode payload as JSON string");
    with_raw_verifier_transaction(database, |tx| {
        tx.execute(
            "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
            params!["project-1", payload, 1_i64, payload, "PROJECT_CONFIDENTIAL"],
        )
        .expect("insert project");
        tx.execute(
            "INSERT INTO content_objects
                    (sha256, byte_length, admission_state, vault_key, created_at_ms, created_by,
                     quarantine_reason)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, NULL)",
            params!["a".repeat(64), 4_i64, "accepted", payload, 2_i64, payload],
        )
        .expect("insert content object");
        tx.execute(
            "INSERT INTO documents (id, created_at_ms, created_by)
                 VALUES (?1, ?2, ?3)",
            params!["document-1", 3_i64, payload],
        )
        .expect("insert document");
        tx.execute(
            "INSERT INTO document_revisions
                    (id, document_id, content_sha256, created_at_ms, created_by)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
            params!["revision-1", "document-1", "a".repeat(64), 4_i64, payload],
        )
        .expect("insert revision");
        tx.execute(
            "INSERT INTO project_documents
                    (project_id, document_id, linked_at_ms, linked_by)
                 VALUES (?1, ?2, ?3, ?4)",
            params!["project-1", "document-1", 5_i64, payload],
        )
        .expect("link project document");
        tx.execute(
            "INSERT INTO job_runs
                    (id, project_id, kind, idempotency_key, state, attempt, lease_owner,
                     lease_expires_at_ms, deadline_at_ms, budget_json, input_json,
                     checkpoint_json, terminal_reason, created_at_ms, updated_at_ms)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, NULL, NULL, ?7, ?8, ?9, ?10,
                         'completed', ?11, ?12)",
            params![
                "job-1",
                "project-1",
                "pdf_ingest",
                payload,
                "succeeded",
                1_i64,
                100_i64,
                &json,
                &json,
                &json,
                6_i64,
                7_i64
            ],
        )
        .expect("insert job");
        tx.execute(
            "INSERT INTO ingest_events
                    (id, project_id, job_id, content_sha256, outcome, attempt, source_name,
                     source_path, idempotency_key, actor, terminal_at_ms, details_json)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, '<redacted>', ?8, ?9, ?10, ?11)",
            params![
                "ingest-1",
                "project-1",
                "job-1",
                "a".repeat(64),
                "accepted_new",
                1_i64,
                payload,
                payload,
                payload,
                8_i64,
                &json
            ],
        )
        .expect("insert ingest event");
        tx.execute(
            "INSERT INTO sheets
                    (id, revision_id, zero_based_page_index, width_micropoints,
                     height_micropoints, rotation_degrees, unit, parent_content_sha256,
                     transform_json)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                "sheet-1",
                "revision-1",
                0_i64,
                612_000_i64,
                792_000_i64,
                0_i64,
                "pt",
                "a".repeat(64),
                &json
            ],
        )
        .expect("insert sheet");
        tx.execute(
            "INSERT INTO scales
                    (id, sheet_id, numerator, denominator, source, created_at_ms, created_by)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params!["scale-1", "sheet-1", 1_i64, 48_i64, payload, 9_i64, payload],
        )
        .expect("insert scale");
        tx.execute(
            "INSERT INTO source_records
                    (id, project_id, job_id, source_name, source_path, content_sha256,
                     metadata_json, created_at_ms)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                "source-1",
                "project-1",
                "job-1",
                payload,
                "<redacted>",
                "a".repeat(64),
                &json,
                10_i64
            ],
        )
        .expect("insert source record");
        tx.execute(
            "INSERT INTO evidence_objects
                    (id, project_id, job_id, document_revision_id, content_sha256,
                     parent_content_sha256, extraction_method, parameters_json, review_state,
                     created_at_ms)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            params![
                "evidence-1",
                "project-1",
                "job-1",
                "revision-1",
                "a".repeat(64),
                "a".repeat(64),
                payload,
                &json,
                "unreviewed",
                11_i64
            ],
        )
        .expect("insert evidence object");
        tx.execute(
            "INSERT INTO corrections
                    (id, project_id, evidence_id, actor, reason, before_json, after_json,
                     created_at_ms)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                "correction-1",
                "project-1",
                "evidence-1",
                payload,
                payload,
                &json,
                &json,
                12_i64
            ],
        )
        .expect("insert correction");
        tx.execute(
            "INSERT INTO audit_events
                    (id, sequence, project_id, actor, action, subject_type, subject_id,
                     before_json, after_json, reason, occurred_at_ms, previous_hash, event_hash)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
            params![
                "audit-1",
                1_i64,
                "project-1",
                payload,
                "project_created",
                "project",
                "project-1",
                &json,
                &json,
                payload,
                13_i64,
                "0".repeat(64),
                "b".repeat(64)
            ],
        )
        .expect("insert audit event");
        Ok(())
    })
    .expect("commit fixture");
}

#[test]
fn empty_database_migrates_to_version_one_and_reopen_is_idempotent() {
    let database = TestDatabase::new("fresh-migration");
    let mut store = Store::open_writer(&database.path).expect("open empty database");
    assert_eq!(store.schema_version().expect("read empty version"), 0);

    let first = store.migrate().expect("apply foundation migration");
    assert_eq!(first.from_version, 0);
    assert_eq!(first.to_version, 1);
    assert_eq!(first.applied_versions, vec![1]);
    drop(store);

    let mut reopened = Store::open_writer(&database.path).expect("reopen database");
    let second = reopened.migrate().expect("repeat migration");
    assert_eq!(second.from_version, 1);
    assert_eq!(second.to_version, 1);
    assert!(second.applied_versions.is_empty());
}

#[test]
fn foundation_migration_creates_exact_required_tables() {
    let database = TestDatabase::new("table-set");
    drop(migrated_store(&database));
    let connection = raw_verifier_connection(&database);
    let mut statement = connection
        .prepare(
            "SELECT name FROM sqlite_schema
             WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
             ORDER BY name",
        )
        .expect("prepare table query");
    let tables = statement
        .query_map([], |row| row.get::<_, String>(0))
        .expect("query table names")
        .collect::<rusqlite::Result<Vec<_>>>()
        .expect("collect table names");

    assert_eq!(tables, TABLES);
}

#[test]
fn task_six_schema_exposes_media_authority_and_attempt_identity() {
    // Break caught: accepted manifests/PDFs sharing an untyped content row or duplicate job attempts.
    let database = TestDatabase::new("task-six-columns");
    let store = migrated_store(&database);
    drop(store);
    let connection = Connection::open_with_flags(
        &database.path,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY
            | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX
            | rusqlite::OpenFlags::SQLITE_OPEN_NOFOLLOW,
    )
    .expect("open verifier-only schema connection");

    let columns = |table: &str| {
        let mut statement = connection
            .prepare(&format!("PRAGMA table_info({table})"))
            .expect("prepare table column query");
        statement
            .query_map([], |row| row.get::<_, String>(1))
            .expect("query table columns")
            .collect::<std::result::Result<Vec<_>, _>>()
            .expect("collect table columns")
    };
    assert!(
        columns("content_objects")
            .iter()
            .any(|column| column == "media_type")
    );
    assert!(
        columns("ingest_events")
            .iter()
            .any(|column| column == "attempt")
    );
}

#[test]
fn task_six_latest_job_audit_index_has_the_exact_subject_sequence_shape() {
    // Break caught: every live-job lookup window-sorting the complete historical audit chain.
    let database = TestDatabase::new("task-six-job-audit-index");
    drop(migrated_store(&database));
    let connection = raw_verifier_connection(&database);
    let sql = connection
        .query_row(
            "SELECT sql FROM sqlite_schema
             WHERE type = 'index' AND name = 'audit_events_subject_sequence'",
            [],
            |row| row.get::<_, String>(0),
        )
        .expect("read exact latest-job-audit index");
    assert_eq!(
        sql,
        "CREATE INDEX audit_events_subject_sequence ON audit_events(subject_type, subject_id, sequence DESC)"
    );
    let mut statement = connection
        .prepare("PRAGMA index_xinfo(audit_events_subject_sequence)")
        .expect("prepare index shape query");
    let columns = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, i64>(0)?,
                row.get::<_, Option<String>>(2)?,
                row.get::<_, i64>(3)?,
                row.get::<_, i64>(5)?,
            ))
        })
        .expect("query index columns")
        .collect::<rusqlite::Result<Vec<_>>>()
        .expect("collect index columns");
    assert_eq!(
        columns,
        vec![
            (0, Some("subject_type".to_owned()), 0, 1),
            (1, Some("subject_id".to_owned()), 0, 1),
            (2, Some("sequence".to_owned()), 1, 1),
            (3, None, 0, 0),
        ]
    );
}

#[test]
fn bound_sql_payloads_remain_inert_data_in_every_sensitive_text_class() {
    let database = TestDatabase::new("bound-values");
    drop(migrated_store(&database));
    let payload = "Robert'); DROP TABLE projects; -- ../vault/$HOME/[31m";
    insert_complete_fixture(&database, payload);

    let recovered = with_raw_verifier_transaction(&database, |tx| {
        let project_name = tx
            .query_row(
                "SELECT name FROM projects WHERE id = ?1",
                ["project-1"],
                |row| row.get::<_, String>(0),
            )
            .expect("query project name");
        let actor = tx
            .query_row(
                "SELECT actor FROM ingest_events WHERE id = ?1",
                ["ingest-1"],
                |row| row.get::<_, String>(0),
            )
            .expect("query actor");
        let idempotency_key = tx
            .query_row(
                "SELECT idempotency_key FROM job_runs WHERE id = ?1",
                ["job-1"],
                |row| row.get::<_, String>(0),
            )
            .expect("query idempotency key");
        let path = tx
            .query_row(
                "SELECT source_path FROM source_records WHERE id = ?1",
                ["source-1"],
                |row| row.get::<_, String>(0),
            )
            .expect("query path");
        let json = tx
            .query_row(
                "SELECT metadata_json FROM source_records WHERE id = ?1",
                ["source-1"],
                |row| row.get::<_, String>(0),
            )
            .expect("query JSON");
        Ok((project_name, actor, idempotency_key, path, json))
    })
    .expect("read payloads");

    assert_eq!(recovered.0, payload);
    assert_eq!(recovered.1, payload);
    assert_eq!(recovered.2, payload);
    assert_eq!(recovered.3, "<redacted>");
    assert_eq!(
        serde_json::from_str::<String>(&recovered.4).expect("decode stored JSON"),
        payload
    );
}

#[test]
fn foreign_keys_json_and_exact_persisted_enums_fail_closed() {
    let database = TestDatabase::new("constraints");
    drop(migrated_store(&database));

    with_raw_verifier_transaction(&database, |tx| {
        tx.execute(
            "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
            params!["project-valid", "name", 1_i64, "actor", "INTERNAL"],
        )
        .expect("insert valid project prerequisite");
        assert!(
            tx.execute(
                "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
                     VALUES (?1, ?2, ?3, ?4, ?5)",
                params!["project-invalid", "name", 1_i64, "actor", "PRIVATE"],
            )
            .is_err()
        );
        assert!(
            tx.execute(
                "INSERT INTO project_documents
                        (project_id, document_id, linked_at_ms, linked_by)
                     VALUES (?1, ?2, ?3, ?4)",
                params!["missing-project", "missing-document", 1_i64, "actor"],
            )
            .is_err()
        );
        assert!(
            tx.execute(
                "INSERT INTO content_objects
                        (sha256, byte_length, admission_state, vault_key, created_at_ms, created_by)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params!["c".repeat(64), 1_i64, "pending", "key", 1_i64, "actor"],
            )
            .is_err()
        );
        assert!(
            tx.execute(
                "INSERT INTO job_runs
                        (id, project_id, kind, idempotency_key, state, attempt, deadline_at_ms,
                         budget_json, input_json, checkpoint_json, created_at_ms, updated_at_ms)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
                params![
                    "job-invalid",
                    "project-valid",
                    "pdf_ingest",
                    "key",
                    "paused",
                    0_i64,
                    100_i64,
                    "{}",
                    "{}",
                    "{}",
                    1_i64,
                    1_i64
                ],
            )
            .is_err()
        );
        tx.execute(
            "INSERT INTO job_runs
                    (id, project_id, kind, idempotency_key, state, attempt, deadline_at_ms,
                     budget_json, input_json, checkpoint_json, created_at_ms, updated_at_ms)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                "job-valid",
                "project-valid",
                "pdf_ingest",
                "valid-key",
                "queued",
                0_i64,
                100_i64,
                "{}",
                "{}",
                "{}",
                1_i64,
                1_i64
            ],
        )
        .expect("insert valid job prerequisite");
        assert!(
            tx.execute(
                "INSERT INTO ingest_events
                        (id, project_id, job_id, outcome, source_name, source_path,
                         idempotency_key, actor, terminal_at_ms, details_json)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
                params![
                    "ingest-invalid",
                    "project-valid",
                    "job-valid",
                    "accepted",
                    "name",
                    "path",
                    "valid-key",
                    "actor",
                    1_i64,
                    "{}"
                ],
            )
            .is_err()
        );
        assert!(
            tx.execute(
                "INSERT INTO source_records
                        (id, project_id, source_name, source_path, metadata_json, created_at_ms)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
                params![
                    "source-invalid",
                    "project-valid",
                    "name",
                    "path",
                    "not-json",
                    1_i64
                ],
            )
            .is_err()
        );
        Ok(())
    })
    .expect("constraint checks complete");
}

#[test]
fn running_job_same_state_update_requires_a_new_checkpoint_and_nondecreasing_timestamp() {
    // Break caught: an updated-at-only heartbeat bypassed the finite checkpoint transition.
    let database = TestDatabase::new("running-checkpoint-transition");
    drop(migrated_store(&database));

    with_raw_verifier_transaction(&database, |tx| {
        tx.execute(
            "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params!["project", "name", 1_i64, "actor", "INTERNAL"],
        )
        .expect("insert project prerequisite");
        tx.execute(
            "INSERT INTO job_runs
                (id, project_id, kind, idempotency_key, state, attempt, deadline_at_ms,
                 budget_json, input_json, checkpoint_json, created_at_ms, updated_at_ms)
             VALUES (?1, ?2, 'pdf_ingest', ?3, 'queued', 0, ?4, '{}', '{}', '{}', ?5, ?5)",
            params!["job", "project", "key", 100_i64, 1_i64],
        )
        .expect("insert queued job");
        tx.execute(
            "UPDATE job_runs
             SET state = 'running', attempt = 1, lease_owner = ?1,
                 lease_expires_at_ms = 50, updated_at_ms = 2
             WHERE id = 'job'",
            ["00000000-0000-4000-8000-000000000001"],
        )
        .expect("start job");

        assert!(
            tx.execute("UPDATE job_runs SET updated_at_ms = 3 WHERE id = 'job'", [],)
                .is_err(),
            "updated-at-only running heartbeat bypassed checkpoint transition"
        );
        assert!(
            tx.execute(
                "UPDATE job_runs SET checkpoint_json = checkpoint_json,
                    updated_at_ms = updated_at_ms WHERE id = 'job'",
                [],
            )
            .is_err(),
            "running no-op update bypassed checkpoint transition"
        );
        assert!(
            tx.execute(
                "UPDATE job_runs SET checkpoint_json = '{\"phase\":\"backward\"}',
                    updated_at_ms = 1 WHERE id = 'job'",
                [],
            )
            .is_err(),
            "running checkpoint update moved time backward"
        );
        tx.execute(
            "UPDATE job_runs
             SET checkpoint_json = '{\"phase\":\"processing_complete\"}', updated_at_ms = 2
             WHERE id = 'job'",
            [],
        )
        .expect("commit exact same-millisecond running checkpoint update");
        Ok(())
    })
    .expect("verify running checkpoint transition");
}

#[test]
fn task_six_job_checks_and_transition_triggers_fail_closed_at_raw_sql_boundary() {
    let database = TestDatabase::new("task-six-job-matrix");
    drop(migrated_store(&database));

    with_raw_verifier_transaction(&database, |tx| {
        insert_job_test_project(tx, "project");
        insert_job_test_project(tx, "project-other");

        let insert_state = |id: &str,
                            key: &str,
                            state: &str,
                            attempt: i64,
                            lease_owner: Option<&str>,
                            lease_expires_at_ms: Option<i64>,
                            deadline_at_ms: Option<i64>,
                            terminal_reason: Option<&str>,
                            created_at_ms: i64,
                            updated_at_ms: i64| {
            tx.execute(
                "INSERT INTO job_runs
                    (id, project_id, kind, idempotency_key, state, attempt, lease_owner,
                     lease_expires_at_ms, deadline_at_ms, budget_json, input_json,
                     checkpoint_json, terminal_reason, created_at_ms, updated_at_ms)
                 VALUES (?1, 'project', 'pdf_ingest', ?2, ?3, ?4, ?5, ?6, ?7,
                         '{}', '{}', '{}', ?8, ?9, ?10)",
                params![
                    id,
                    key,
                    state,
                    attempt,
                    lease_owner,
                    lease_expires_at_ms,
                    deadline_at_ms,
                    terminal_reason,
                    created_at_ms,
                    updated_at_ms
                ],
            )
        };

        assert!(
            insert_state(
                "max-time",
                "max-time",
                "queued",
                0,
                None,
                None,
                Some(9_007_199_254_740_991),
                None,
                9_007_199_254_740_991,
                9_007_199_254_740_991,
            )
            .is_ok(),
            "JCS-safe timestamp boundary was rejected"
        );
        for (label, result) in [
            (
                "kind literal",
                tx.execute(
                    "INSERT INTO job_runs
                        (id, project_id, kind, idempotency_key, state, attempt, deadline_at_ms,
                         budget_json, input_json, checkpoint_json, created_at_ms, updated_at_ms)
                     VALUES ('bad-kind', 'project', 'other', 'bad-kind', 'queued', 0, 1,
                             '{}', '{}', '{}', 0, 0)",
                    [],
                ),
            ),
            (
                "attempt upper bound",
                insert_state(
                    "attempt-17",
                    "attempt-17",
                    "running",
                    17,
                    Some("00000000-0000-4000-8000-000000000001"),
                    Some(1),
                    Some(2),
                    None,
                    0,
                    0,
                ),
            ),
            (
                "running lease required",
                insert_state(
                    "running-no-lease",
                    "running-no-lease",
                    "running",
                    1,
                    None,
                    None,
                    Some(2),
                    None,
                    0,
                    0,
                ),
            ),
            (
                "lease UUID shape",
                insert_state(
                    "running-bad-owner",
                    "running-bad-owner",
                    "running",
                    1,
                    Some("not-a-uuid"),
                    Some(1),
                    Some(2),
                    None,
                    0,
                    0,
                ),
            ),
            (
                "queued lease forbidden",
                insert_state(
                    "queued-with-lease",
                    "queued-with-lease",
                    "queued",
                    0,
                    Some("00000000-0000-4000-8000-000000000001"),
                    Some(1),
                    Some(2),
                    None,
                    0,
                    0,
                ),
            ),
            (
                "interrupted attempt maximum",
                insert_state(
                    "interrupted-16",
                    "interrupted-16",
                    "interrupted",
                    16,
                    None,
                    None,
                    Some(2),
                    None,
                    0,
                    0,
                ),
            ),
            (
                "terminal reason required",
                insert_state(
                    "succeeded-no-reason",
                    "succeeded-no-reason",
                    "succeeded",
                    1,
                    None,
                    None,
                    Some(2),
                    None,
                    0,
                    0,
                ),
            ),
            (
                "terminal lease forbidden",
                insert_state(
                    "succeeded-with-lease",
                    "succeeded-with-lease",
                    "succeeded",
                    1,
                    Some("00000000-0000-4000-8000-000000000001"),
                    Some(1),
                    Some(2),
                    Some("completed"),
                    0,
                    0,
                ),
            ),
            (
                "terminal reason literal",
                insert_state(
                    "failed-bad-reason",
                    "failed-bad-reason",
                    "failed",
                    1,
                    None,
                    None,
                    Some(2),
                    Some("other"),
                    0,
                    0,
                ),
            ),
            (
                "deadline required",
                insert_state(
                    "no-deadline",
                    "no-deadline",
                    "queued",
                    0,
                    None,
                    None,
                    None,
                    None,
                    0,
                    0,
                ),
            ),
            (
                "deadline precedes creation",
                insert_state(
                    "early-deadline",
                    "early-deadline",
                    "queued",
                    0,
                    None,
                    None,
                    Some(1),
                    None,
                    2,
                    2,
                ),
            ),
            (
                "updated precedes creation",
                insert_state(
                    "early-update",
                    "early-update",
                    "queued",
                    0,
                    None,
                    None,
                    Some(3),
                    None,
                    2,
                    1,
                ),
            ),
            (
                "timestamp JCS upper bound",
                insert_state(
                    "time-over",
                    "time-over",
                    "queued",
                    0,
                    None,
                    None,
                    Some(9_007_199_254_740_992),
                    None,
                    0,
                    0,
                ),
            ),
            (
                "idempotency key byte cap",
                insert_state(
                    "key-over",
                    &"k".repeat(129),
                    "queued",
                    0,
                    None,
                    None,
                    Some(2),
                    None,
                    0,
                    0,
                ),
            ),
            (
                "idempotency key controls",
                insert_state(
                    "key-control",
                    "key\ncontrol",
                    "queued",
                    0,
                    None,
                    None,
                    Some(2),
                    None,
                    0,
                    0,
                ),
            ),
        ] {
            assert!(result.is_err(), "{label} check unexpectedly passed");
        }

        for (id, reason) in [
            ("failed-deadline", "deadline_expired"),
            ("failed-attempt", "attempt_limit"),
            ("failed-internal", "internal_failure"),
        ] {
            insert_state(
                id,
                id,
                "failed",
                16,
                None,
                None,
                Some(2),
                Some(reason),
                0,
                0,
            )
            .expect("insert exact failed terminal reason");
        }
        insert_state(
            "interrupted-max",
            "interrupted-max",
            "interrupted",
            15,
            None,
            None,
            Some(2),
            None,
            0,
            0,
        )
        .expect("insert max resumable interrupted attempt");
        insert_state(
            "succeeded-max",
            "succeeded-max",
            "succeeded",
            16,
            None,
            None,
            Some(2),
            Some("completed"),
            0,
            0,
        )
        .expect("insert max succeeded attempt");
        for attempt in [0_i64, 16_i64] {
            let id = format!("cancelled-{attempt}");
            insert_state(
                &id,
                &id,
                "cancelled",
                attempt,
                None,
                None,
                Some(2),
                Some("cancelled"),
                0,
                0,
            )
            .expect("insert cancelled attempt boundary");
        }

        insert_queued_job(tx, "frozen", "project", "frozen");
        for sql in [
            "UPDATE job_runs SET state = 'running', attempt = 1,
                lease_owner = '00000000-0000-4000-8000-000000000001',
                lease_expires_at_ms = 1, updated_at_ms = 1,
                id = 'frozen-other' WHERE id = 'frozen'",
            "UPDATE job_runs SET state = 'running', attempt = 1,
                lease_owner = '00000000-0000-4000-8000-000000000001',
                lease_expires_at_ms = 1, updated_at_ms = 1,
                project_id = 'project-other' WHERE id = 'frozen'",
            "UPDATE job_runs SET state = 'running', attempt = 1,
                lease_owner = '00000000-0000-4000-8000-000000000001',
                lease_expires_at_ms = 1, updated_at_ms = 1,
                kind = 'other' WHERE id = 'frozen'",
            "UPDATE job_runs SET state = 'running', attempt = 1,
                lease_owner = '00000000-0000-4000-8000-000000000001',
                lease_expires_at_ms = 1, updated_at_ms = 1,
                idempotency_key = 'other' WHERE id = 'frozen'",
            "UPDATE job_runs SET state = 'running', attempt = 1,
                lease_owner = '00000000-0000-4000-8000-000000000001',
                lease_expires_at_ms = 1, updated_at_ms = 1,
                deadline_at_ms = 300001 WHERE id = 'frozen'",
            "UPDATE job_runs SET state = 'running', attempt = 1,
                lease_owner = '00000000-0000-4000-8000-000000000001',
                lease_expires_at_ms = 1, updated_at_ms = 1,
                budget_json = '{\"changed\":true}' WHERE id = 'frozen'",
            "UPDATE job_runs SET state = 'running', attempt = 1,
                lease_owner = '00000000-0000-4000-8000-000000000001',
                lease_expires_at_ms = 1, updated_at_ms = 1,
                input_json = '{\"changed\":true}' WHERE id = 'frozen'",
            "UPDATE job_runs SET state = 'running', attempt = 1,
                lease_owner = '00000000-0000-4000-8000-000000000001',
                lease_expires_at_ms = 1, updated_at_ms = 1,
                created_at_ms = 1 WHERE id = 'frozen'",
        ] {
            assert_sql_error_contains(tx.execute(sql, []), "frozen identity cannot change");
        }
        assert_sql_error_contains(
            tx.execute("DELETE FROM job_runs WHERE id = 'frozen'", []),
            "job_runs rows cannot be deleted",
        );

        insert_queued_job(tx, "queued-cancel", "project", "queued-cancel");
        tx.execute(
            "UPDATE job_runs SET state = 'cancelled', terminal_reason = 'cancelled',
                    updated_at_ms = 1 WHERE id = 'queued-cancel'",
            [],
        )
        .expect("queued to cancelled is legal");

        insert_queued_job(tx, "running-checkpoint", "project", "running-checkpoint");
        start_job(tx, "running-checkpoint", 1);
        tx.execute(
            "UPDATE job_runs SET checkpoint_json = '{\"step\":1}', updated_at_ms = 2
             WHERE id = 'running-checkpoint'",
            [],
        )
        .expect("running checkpoint update is legal");

        insert_queued_job(tx, "recover", "project", "recover");
        start_job(tx, "recover", 1);
        tx.execute(
            "UPDATE job_runs SET state = 'interrupted', lease_owner = NULL,
                    lease_expires_at_ms = NULL, updated_at_ms = 2 WHERE id = 'recover'",
            [],
        )
        .expect("running to interrupted is legal");
        tx.execute(
            "UPDATE job_runs SET state = 'running', attempt = 2,
                    lease_owner = '00000000-0000-4000-8000-000000000002',
                    lease_expires_at_ms = 60000, updated_at_ms = 3 WHERE id = 'recover'",
            [],
        )
        .expect("interrupted to next running attempt is legal");

        insert_queued_job(tx, "interrupt-cancel", "project", "interrupt-cancel");
        start_job(tx, "interrupt-cancel", 1);
        tx.execute(
            "UPDATE job_runs SET state = 'interrupted', lease_owner = NULL,
                    lease_expires_at_ms = NULL, updated_at_ms = 2
             WHERE id = 'interrupt-cancel'",
            [],
        )
        .expect("prepare interrupted cancellation");
        tx.execute(
            "UPDATE job_runs SET state = 'cancelled', terminal_reason = 'cancelled',
                    updated_at_ms = 3 WHERE id = 'interrupt-cancel'",
            [],
        )
        .expect("interrupted to cancelled is legal");

        for (suffix, state, reason) in [
            ("success", "succeeded", "completed"),
            ("failure", "failed", "internal_failure"),
            ("cancel", "cancelled", "cancelled"),
        ] {
            let id = format!("running-{suffix}");
            insert_queued_job(tx, &id, "project", &id);
            start_job(tx, &id, 1);
            tx.execute(
                "UPDATE job_runs SET state = ?2, lease_owner = NULL,
                        lease_expires_at_ms = NULL, terminal_reason = ?3, updated_at_ms = 2
                 WHERE id = ?1",
                params![id, state, reason],
            )
            .expect("running terminal transition is legal");
        }

        insert_queued_job(
            tx,
            "illegal-queued-terminal",
            "project",
            "illegal-queued-terminal",
        );
        assert_sql_error_contains(
            tx.execute(
                "UPDATE job_runs SET state = 'succeeded', attempt = 1,
                        terminal_reason = 'completed', updated_at_ms = 1
                 WHERE id = 'illegal-queued-terminal'",
                [],
            ),
            "illegal job_runs state transition",
        );
        insert_queued_job(
            tx,
            "illegal-attempt-jump",
            "project",
            "illegal-attempt-jump",
        );
        assert_sql_error_contains(
            tx.execute(
                "UPDATE job_runs SET state = 'running', attempt = 2,
                        lease_owner = '00000000-0000-4000-8000-000000000001',
                        lease_expires_at_ms = 1, updated_at_ms = 1
                 WHERE id = 'illegal-attempt-jump'",
                [],
            ),
            "illegal job_runs state transition",
        );
        insert_queued_job(
            tx,
            "illegal-interrupt-jump",
            "project",
            "illegal-interrupt-jump",
        );
        start_job(tx, "illegal-interrupt-jump", 1);
        assert_sql_error_contains(
            tx.execute(
                "UPDATE job_runs SET state = 'interrupted', attempt = 2, lease_owner = NULL,
                        lease_expires_at_ms = NULL, updated_at_ms = 2
                 WHERE id = 'illegal-interrupt-jump'",
                [],
            ),
            "illegal job_runs state transition",
        );
        insert_queued_job(tx, "illegal-resume-jump", "project", "illegal-resume-jump");
        start_job(tx, "illegal-resume-jump", 1);
        tx.execute(
            "UPDATE job_runs SET state = 'interrupted', lease_owner = NULL,
                    lease_expires_at_ms = NULL, updated_at_ms = 2
             WHERE id = 'illegal-resume-jump'",
            [],
        )
        .expect("prepare illegal resume jump");
        assert_sql_error_contains(
            tx.execute(
                "UPDATE job_runs SET state = 'running', attempt = 3,
                        lease_owner = '00000000-0000-4000-8000-000000000003',
                        lease_expires_at_ms = 60000, updated_at_ms = 3
                 WHERE id = 'illegal-resume-jump'",
                [],
            ),
            "illegal job_runs state transition",
        );
        assert_sql_error_contains(
            tx.execute(
                "UPDATE job_runs SET updated_at_ms = 4 WHERE id = 'queued-cancel'",
                [],
            ),
            "illegal job_runs state transition",
        );
        Ok(())
    })
    .expect("verify Task 6 job check/transition matrix");
}

#[test]
fn task_six_authority_literals_attempt_identity_redaction_and_text_caps_fail_closed() {
    let database = TestDatabase::new("task-six-authority-checks");
    drop(migrated_store(&database));

    with_raw_verifier_transaction(&database, |tx| {
        insert_job_test_project(tx, "project");
        insert_queued_job(tx, "job", "project", "job-key");

        let insert_event = |id: &str,
                            job_id: Option<&str>,
                            outcome: &str,
                            attempt: Option<i64>,
                            source_name: &str,
                            source_path: &str,
                            key: &str,
                            actor: &str,
                            terminal_at_ms: i64| {
            tx.execute(
                "INSERT INTO ingest_events
                    (id, project_id, job_id, outcome, attempt, source_name, source_path,
                     idempotency_key, actor, terminal_at_ms, details_json)
                 VALUES (?1, 'project', ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, '{}')",
                params![
                    id,
                    job_id,
                    outcome,
                    attempt,
                    source_name,
                    source_path,
                    key,
                    actor,
                    terminal_at_ms
                ],
            )
        };

        insert_event(
            "authoritative",
            Some("job"),
            "interrupted",
            Some(1),
            "utf8:source.pdf",
            "<redacted>",
            "key",
            "actor",
            1,
        )
        .expect("insert authoritative attempt event");
        assert!(
            insert_event(
                "duplicate-attempt",
                Some("job"),
                "accepted_new",
                Some(1),
                "utf8:source.pdf",
                "<redacted>",
                "key",
                "actor",
                1,
            )
            .is_err(),
            "duplicate (job, attempt) authority was accepted"
        );
        for id in ["replay-one", "replay-two"] {
            insert_event(
                id,
                Some("job"),
                "idempotent_replay",
                None,
                "utf8:source.pdf",
                "<redacted>",
                "key",
                "actor",
                1,
            )
            .expect("nullable replay attempt remains non-authoritative");
        }
        insert_event(
            "conflict",
            Some("job"),
            "denied_conflict",
            None,
            "utf8:source.pdf",
            "<redacted>",
            "key",
            "actor",
            1,
        )
        .expect("insert conflict observation with null attempt");

        for (label, result) in [
            (
                "replay positive attempt",
                insert_event(
                    "replay-positive",
                    Some("job"),
                    "idempotent_replay",
                    Some(2),
                    "name",
                    "<redacted>",
                    "key",
                    "actor",
                    1,
                ),
            ),
            (
                "authoritative null attempt",
                insert_event(
                    "authority-null",
                    Some("job"),
                    "interrupted",
                    None,
                    "name",
                    "<redacted>",
                    "key",
                    "actor",
                    1,
                ),
            ),
            (
                "authoritative missing job",
                insert_event(
                    "authority-no-job",
                    None,
                    "interrupted",
                    Some(2),
                    "name",
                    "<redacted>",
                    "key",
                    "actor",
                    1,
                ),
            ),
            (
                "attempt upper bound",
                insert_event(
                    "attempt-over",
                    Some("job"),
                    "interrupted",
                    Some(17),
                    "name",
                    "<redacted>",
                    "key",
                    "actor",
                    1,
                ),
            ),
            (
                "outcome literal",
                insert_event(
                    "outcome-other",
                    Some("job"),
                    "accepted",
                    Some(2),
                    "name",
                    "<redacted>",
                    "key",
                    "actor",
                    1,
                ),
            ),
            (
                "raw source path",
                insert_event(
                    "raw-path",
                    Some("job"),
                    "idempotent_replay",
                    None,
                    "name",
                    "/secret/path.pdf",
                    "key",
                    "actor",
                    1,
                ),
            ),
            (
                "source-name byte cap",
                insert_event(
                    "source-over",
                    Some("job"),
                    "idempotent_replay",
                    None,
                    &"s".repeat(4097),
                    "<redacted>",
                    "key",
                    "actor",
                    1,
                ),
            ),
            (
                "idempotency byte cap",
                insert_event(
                    "event-key-over",
                    Some("job"),
                    "idempotent_replay",
                    None,
                    "name",
                    "<redacted>",
                    &"k".repeat(129),
                    "actor",
                    1,
                ),
            ),
            (
                "actor byte cap",
                insert_event(
                    "actor-over",
                    Some("job"),
                    "idempotent_replay",
                    None,
                    "name",
                    "<redacted>",
                    "key",
                    &"a".repeat(129),
                    1,
                ),
            ),
            (
                "actor control",
                insert_event(
                    "actor-control",
                    Some("job"),
                    "idempotent_replay",
                    None,
                    "name",
                    "<redacted>",
                    "key",
                    "actor\n",
                    1,
                ),
            ),
            (
                "timestamp upper bound",
                insert_event(
                    "event-time-over",
                    Some("job"),
                    "idempotent_replay",
                    None,
                    "name",
                    "<redacted>",
                    "key",
                    "actor",
                    9_007_199_254_740_992,
                ),
            ),
        ] {
            assert!(result.is_err(), "{label} unexpectedly passed");
        }
        insert_event(
            "event-caps",
            Some("job"),
            "idempotent_replay",
            None,
            &"s".repeat(4096),
            "<redacted>",
            &"k".repeat(128),
            &"a".repeat(128),
            9_007_199_254_740_991,
        )
        .expect("insert event at every scalar cap");

        tx.execute(
            "INSERT INTO source_records
                (id, project_id, source_name, source_path, metadata_json, created_at_ms)
             VALUES ('redacted-source', 'project', 'name', '<redacted>', '{}', 0)",
            [],
        )
        .expect("insert redacted source record");
        assert!(
            tx.execute(
                "INSERT INTO source_records
                    (id, project_id, source_name, source_path, metadata_json, created_at_ms)
                 VALUES ('raw-source', 'project', 'name', '/raw/path', '{}', 0)",
                [],
            )
            .is_err(),
            "source record persisted a raw path"
        );
        tx.execute(
            "INSERT INTO source_records
                (id, project_id, source_name, source_path, metadata_json, created_at_ms)
             VALUES ('source-caps', 'project', ?1, '<redacted>', '{}', ?2)",
            params!["s".repeat(4096), 9_007_199_254_740_991_i64],
        )
        .expect("insert source record at safe-name/timestamp caps");
        for (id, name, timestamp) in [
            ("source-name-over", "s".repeat(4097), 0_i64),
            ("source-name-control", "source\n".to_owned(), 0_i64),
            (
                "source-time-over",
                "source".to_owned(),
                9_007_199_254_740_992_i64,
            ),
        ] {
            assert!(
                tx.execute(
                    "INSERT INTO source_records
                        (id, project_id, source_name, source_path, metadata_json, created_at_ms)
                     VALUES (?1, 'project', ?2, '<redacted>', '{}', ?3)",
                    params![id, name, timestamp],
                )
                .is_err(),
                "source-record scalar cap unexpectedly passed for {id}"
            );
        }

        tx.execute(
            "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
             VALUES ('text-caps', ?1, 9007199254740991, ?2, 'PUBLIC')",
            params!["é".repeat(128), "a".repeat(128)],
        )
        .expect("insert project at UTF-8/timestamp caps");
        for (id, name, actor, timestamp) in [
            (
                "name-over",
                format!("{}x", "é".repeat(128)),
                "actor".to_owned(),
                0_i64,
            ),
            (
                "name-control",
                "name\n".to_owned(),
                "actor".to_owned(),
                0_i64,
            ),
            ("creator-over", "name".to_owned(), "a".repeat(129), 0_i64),
            (
                "project-time-over",
                "name".to_owned(),
                "actor".to_owned(),
                9_007_199_254_740_992_i64,
            ),
        ] {
            assert!(
                tx.execute(
                    "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
                     VALUES (?1, ?2, ?3, ?4, 'PUBLIC')",
                    params![id, name, timestamp, actor],
                )
                .is_err(),
                "project scalar check unexpectedly accepted {id}"
            );
        }

        for (digest, media_type) in [
            ("1".repeat(64), "application/pdf"),
            (
                "2".repeat(64),
                "application/vnd.heleos.evidence-manifest+json;version=1",
            ),
        ] {
            tx.execute(
                "INSERT INTO content_objects
                    (sha256, byte_length, media_type, admission_state, vault_key,
                     created_at_ms, created_by, quarantine_reason)
                 VALUES (?1, 1, ?2, 'accepted', ?1, 0, 'actor', NULL)",
                params![digest, media_type],
            )
            .expect("insert exact content media literal");
        }
        for (digest, media_type, state, reason) in [
            ("3".repeat(64), "text/plain", "accepted", None),
            ("4".repeat(64), "application/pdf", "accepted", Some("{}")),
            ("5".repeat(64), "application/pdf", "quarantined", None),
            (
                "6".repeat(64),
                "application/pdf",
                "quarantined",
                Some("{ }"),
            ),
        ] {
            assert!(
                tx.execute(
                    "INSERT INTO content_objects
                        (sha256, byte_length, media_type, admission_state, vault_key,
                         created_at_ms, created_by, quarantine_reason)
                     VALUES (?1, 1, ?2, ?3, ?1, 0, 'actor', ?4)",
                    params![digest, media_type, state, reason],
                )
                .is_err(),
                "content authority check unexpectedly passed for {digest}"
            );
        }
        Ok(())
    })
    .expect("verify Task 6 authority and scalar check matrix");
}

#[test]
fn task_six_json_and_audit_scalar_caps_accept_n_and_reject_n_plus_one() {
    let database = TestDatabase::new("task-six-byte-caps");
    drop(migrated_store(&database));

    let one_mib = canonical_json_string_with_exact_bytes(1024 * 1024);
    let one_mib_plus_one = canonical_json_string_with_exact_bytes(1024 * 1024 + 1);
    let eight_mib = canonical_json_string_with_exact_bytes(8 * 1024 * 1024);
    let eight_mib_plus_one = canonical_json_string_with_exact_bytes(8 * 1024 * 1024 + 1);

    with_raw_verifier_transaction(&database, |tx| {
        insert_job_test_project(tx, "project");
        insert_job_test_project(tx, "project-other");
        tx.execute(
            "INSERT INTO job_runs
                (id, project_id, kind, idempotency_key, state, attempt, deadline_at_ms,
                 budget_json, input_json, checkpoint_json, created_at_ms, updated_at_ms)
             VALUES ('cap-job', 'project', 'pdf_ingest', 'cap-job', 'queued', 0, 1,
                     ?1, ?1, ?2, 0, 0)",
            params![&one_mib, &eight_mib],
        )
        .expect("insert job JSON at independent byte caps");
        for (id, budget, input, checkpoint) in [
            ("budget-over", one_mib_plus_one.as_str(), "{}", "{}"),
            ("input-over", "{}", one_mib_plus_one.as_str(), "{}"),
            ("checkpoint-over", "{}", "{}", eight_mib_plus_one.as_str()),
        ] {
            assert!(
                tx.execute(
                    "INSERT INTO job_runs
                        (id, project_id, kind, idempotency_key, state, attempt, deadline_at_ms,
                         budget_json, input_json, checkpoint_json, created_at_ms, updated_at_ms)
                     VALUES (?1, 'project', 'pdf_ingest', ?1, 'queued', 0, 1,
                             ?2, ?3, ?4, 0, 0)",
                    params![id, budget, input, checkpoint],
                )
                .is_err(),
                "job JSON N+1 cap unexpectedly passed for {id}"
            );
        }

        tx.execute(
            "INSERT INTO content_objects
                (sha256, byte_length, media_type, admission_state, vault_key,
                 created_at_ms, created_by, quarantine_reason)
             VALUES (?1, 1, 'application/pdf', 'quarantined', ?1, 0, 'actor', ?2)",
            params!["1".repeat(64), &one_mib],
        )
        .expect("insert quarantine JSON at byte cap");
        assert!(
            tx.execute(
                "INSERT INTO content_objects
                    (sha256, byte_length, media_type, admission_state, vault_key,
                     created_at_ms, created_by, quarantine_reason)
                 VALUES (?1, 1, 'application/pdf', 'quarantined', ?1, 0, 'actor', ?2)",
                params!["2".repeat(64), &one_mib_plus_one],
            )
            .is_err(),
            "quarantine JSON N+1 cap unexpectedly passed"
        );

        tx.execute(
            "INSERT INTO ingest_events
                (id, project_id, job_id, outcome, attempt, source_name, source_path,
                 idempotency_key, actor, terminal_at_ms, details_json)
             VALUES ('details-cap', 'project', 'cap-job', 'idempotent_replay', NULL,
                     'name', '<redacted>', 'key', 'actor', 0, ?1)",
            [&one_mib],
        )
        .expect("insert event details at byte cap");
        assert!(
            tx.execute(
                "INSERT INTO ingest_events
                    (id, project_id, job_id, outcome, attempt, source_name, source_path,
                     idempotency_key, actor, terminal_at_ms, details_json)
                 VALUES ('details-over', 'project', 'cap-job', 'idempotent_replay', NULL,
                         'name', '<redacted>', 'key', 'actor', 0, ?1)",
                [&one_mib_plus_one],
            )
            .is_err(),
            "event details N+1 cap unexpectedly passed"
        );
        tx.execute(
            "INSERT INTO source_records
                (id, project_id, job_id, source_name, source_path, metadata_json, created_at_ms)
             VALUES ('metadata-cap', 'project', 'cap-job', 'name', '<redacted>', ?1, 0)",
            [&one_mib],
        )
        .expect("insert source metadata at byte cap");
        assert!(
            tx.execute(
                "INSERT INTO source_records
                    (id, project_id, job_id, source_name, source_path, metadata_json, created_at_ms)
                 VALUES ('metadata-over', 'project', 'cap-job', 'name', '<redacted>', ?1, 0)",
                [&one_mib_plus_one],
            )
            .is_err(),
            "source metadata N+1 cap unexpectedly passed"
        );

        let original = "3".repeat(64);
        let manifest = "4".repeat(64);
        for (digest, media_type) in [
            (&original, "application/pdf"),
            (
                &manifest,
                "application/vnd.heleos.evidence-manifest+json;version=1",
            ),
        ] {
            tx.execute(
                "INSERT INTO content_objects
                    (sha256, byte_length, media_type, admission_state, vault_key,
                     created_at_ms, created_by)
                 VALUES (?1, 1, ?2, 'accepted', ?1, 0, 'actor')",
                params![digest, media_type],
            )
            .expect("insert accepted lineage content");
        }
        tx.execute(
            "INSERT INTO documents (id, created_at_ms, created_by)
             VALUES ('document', 0, 'actor')",
            [],
        )
        .expect("insert cap-test document");
        tx.execute(
            "INSERT INTO document_revisions
                (id, document_id, content_sha256, created_at_ms, created_by)
             VALUES ('revision', 'document', ?1, 0, 'actor')",
            [&original],
        )
        .expect("insert cap-test revision");
        tx.execute(
            "INSERT INTO sheets
                (id, revision_id, zero_based_page_index, width_micropoints,
                 height_micropoints, rotation_degrees, unit, parent_content_sha256,
                 transform_json)
             VALUES ('sheet-cap', 'revision', 0, 1, 1, 0, 'pt', ?1, ?2)",
            params![&original, &one_mib],
        )
        .expect("insert sheet transform at byte cap");
        assert!(
            tx.execute(
                "INSERT INTO sheets
                    (id, revision_id, zero_based_page_index, width_micropoints,
                     height_micropoints, rotation_degrees, unit, parent_content_sha256,
                     transform_json)
                 VALUES ('sheet-over', 'revision', 1, 1, 1, 0, 'pt', ?1, ?2)",
                params![&original, &one_mib_plus_one],
            )
            .is_err(),
            "sheet transform N+1 cap unexpectedly passed"
        );
        tx.execute(
            "INSERT INTO evidence_objects
                (id, project_id, job_id, document_revision_id, content_sha256,
                 parent_content_sha256, extraction_method, parameters_json, review_state,
                 created_at_ms)
             VALUES ('evidence-cap', 'project', 'cap-job', 'revision', ?1, ?2,
                     'heleos.pdf-probe/v1', ?3, 'accepted', 0)",
            params![&manifest, &original, &one_mib],
        )
        .expect("insert evidence parameters at byte cap");
        assert!(
            tx.execute(
                "INSERT INTO evidence_objects
                    (id, project_id, job_id, document_revision_id, content_sha256,
                     parent_content_sha256, extraction_method, parameters_json, review_state,
                     created_at_ms)
                 VALUES ('evidence-over', 'project-other', 'cap-job', 'revision', ?1, ?2,
                         'heleos.pdf-probe/v1', ?3, 'accepted', 0)",
                params![&manifest, &original, &one_mib_plus_one],
            )
            .is_err(),
            "evidence parameters N+1 cap unexpectedly passed"
        );
        tx.execute(
            "INSERT INTO corrections
                (id, project_id, evidence_id, actor, reason, before_json, after_json,
                 created_at_ms)
             VALUES ('correction-cap', 'project', 'evidence-cap', 'actor', 'reason',
                     ?1, ?1, 0)",
            [&one_mib],
        )
        .expect("insert correction JSON at byte caps");
        for (id, before, after) in [
            ("correction-before-over", one_mib_plus_one.as_str(), "{}"),
            ("correction-after-over", "{}", one_mib_plus_one.as_str()),
        ] {
            assert!(
                tx.execute(
                    "INSERT INTO corrections
                        (id, project_id, evidence_id, actor, reason, before_json, after_json,
                         created_at_ms)
                     VALUES (?1, 'project', 'evidence-cap', 'actor', 'reason', ?2, ?3, 0)",
                    params![id, before, after],
                )
                .is_err(),
                "correction JSON N+1 cap unexpectedly passed for {id}"
            );
        }

        let insert_audit = |id: &str,
                            sequence: i64,
                            action: &str,
                            subject_type: &str,
                            subject_id: &str,
                            before: &str,
                            after: &str,
                            reason: &str,
                            occurred_at_ms: i64,
                            hash_byte: char| {
            let digest = hash_byte.to_string().repeat(64);
            tx.execute(
                "INSERT INTO audit_events
                    (id, sequence, project_id, actor, action, subject_type, subject_id,
                     before_json, after_json, reason, occurred_at_ms, previous_hash, event_hash)
                 VALUES (?1, ?2, 'project', 'actor', ?3, ?4, ?5, ?6, ?7, ?8, ?9,
                         ?10, ?11)",
                params![
                    id,
                    sequence,
                    action,
                    subject_type,
                    subject_id,
                    before,
                    after,
                    reason,
                    occurred_at_ms,
                    "0".repeat(64),
                    digest
                ],
            )
        };
        insert_audit(
            "audit-cap",
            1,
            "project_created",
            "project",
            &"s".repeat(256),
            &one_mib,
            &one_mib,
            &"r".repeat(1024),
            9_007_199_254_740_991,
            'a',
        )
        .expect("insert audit row at scalar/document caps");
        for (label, result) in [
            (
                "before N+1",
                insert_audit(
                    "audit-before-over",
                    2,
                    "project_created",
                    "project",
                    "subject",
                    &one_mib_plus_one,
                    "{}",
                    "reason",
                    0,
                    'b',
                ),
            ),
            (
                "after N+1",
                insert_audit(
                    "audit-after-over",
                    3,
                    "project_created",
                    "project",
                    "subject",
                    "{}",
                    &one_mib_plus_one,
                    "reason",
                    0,
                    'c',
                ),
            ),
            (
                "subject N+1",
                insert_audit(
                    "audit-subject-over",
                    4,
                    "project_created",
                    "project",
                    &"s".repeat(257),
                    "{}",
                    "{}",
                    "reason",
                    0,
                    'd',
                ),
            ),
            (
                "subject control",
                insert_audit(
                    "audit-subject-control",
                    5,
                    "project_created",
                    "project",
                    "subject\n",
                    "{}",
                    "{}",
                    "reason",
                    0,
                    'e',
                ),
            ),
            (
                "reason N+1",
                insert_audit(
                    "audit-reason-over",
                    6,
                    "project_created",
                    "project",
                    "subject",
                    "{}",
                    "{}",
                    &"r".repeat(1025),
                    0,
                    'f',
                ),
            ),
            (
                "reason hostile control",
                insert_audit(
                    "audit-reason-control",
                    7,
                    "project_created",
                    "project",
                    "subject",
                    "{}",
                    "{}",
                    "reason\u{001b}",
                    0,
                    '1',
                ),
            ),
            (
                "action literal",
                insert_audit(
                    "audit-action",
                    8,
                    "other",
                    "project",
                    "subject",
                    "{}",
                    "{}",
                    "reason",
                    0,
                    '2',
                ),
            ),
            (
                "subject-type literal",
                insert_audit(
                    "audit-subject-type",
                    9,
                    "project_created",
                    "other",
                    "subject",
                    "{}",
                    "{}",
                    "reason",
                    0,
                    '3',
                ),
            ),
            (
                "sequence zero",
                insert_audit(
                    "audit-sequence-zero",
                    0,
                    "project_created",
                    "project",
                    "subject",
                    "{}",
                    "{}",
                    "reason",
                    0,
                    '4',
                ),
            ),
            (
                "sequence N+1",
                insert_audit(
                    "audit-sequence-over",
                    9_007_199_254_740_992,
                    "project_created",
                    "project",
                    "subject",
                    "{}",
                    "{}",
                    "reason",
                    0,
                    '6',
                ),
            ),
            (
                "timestamp N+1",
                insert_audit(
                    "audit-time-over",
                    10,
                    "project_created",
                    "project",
                    "subject",
                    "{}",
                    "{}",
                    "reason",
                    9_007_199_254_740_992,
                    '5',
                ),
            ),
        ] {
            assert!(result.is_err(), "audit {label} check unexpectedly passed");
        }
        tx.execute(
            "INSERT INTO audit_events
                (id, sequence, project_id, actor, action, subject_type, subject_id,
                 before_json, after_json, reason, occurred_at_ms, previous_hash, event_hash)
             VALUES ('audit-actor-cap', 9007199254740991, 'project', ?1,
                     'project_created', 'project', 'subject', '{}', '{}', 'reason\nline', 0,
                     ?2, ?3)",
            params!["a".repeat(128), "0".repeat(64), "6".repeat(64)],
        )
        .expect("insert audit actor/sequence and ordinary-whitespace caps");
        for (id, actor, hash) in [
            ("audit-actor-over", "a".repeat(129), "7".repeat(64)),
            ("audit-actor-control", "actor\n".to_owned(), "8".repeat(64)),
        ] {
            assert!(
                tx.execute(
                    "INSERT INTO audit_events
                        (id, sequence, project_id, actor, action, subject_type, subject_id,
                         before_json, after_json, reason, occurred_at_ms, previous_hash,
                         event_hash)
                     VALUES (?1, 11, 'project', ?2, 'project_created', 'project', 'subject',
                             '{}', '{}', 'reason', 0, ?3, ?4)",
                    params![id, actor, "0".repeat(64), hash],
                )
                .is_err(),
                "audit actor cap unexpectedly passed for {id}"
            );
        }
        Ok(())
    })
    .expect("verify Task 6 JSON/audit N/N+1 caps");
}

#[test]
fn declared_uniqueness_foreign_keys_and_delete_actions_are_independently_enforced() {
    let database = TestDatabase::new("constraint-matrix");
    drop(migrated_store(&database));
    insert_complete_fixture(&database, "collision");

    with_raw_verifier_transaction(&database, |tx| {
        let uniqueness_collisions = [
            (
                "content object sha256",
                "INSERT INTO content_objects
                        (sha256, byte_length, admission_state, vault_key, created_at_ms,
                         created_by, quarantine_reason)
                     VALUES (
                        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                        99, 'accepted', 'different-vault-key', 1, 'actor', NULL)",
            ),
            (
                "document revision content",
                "INSERT INTO document_revisions
                        (id, document_id, content_sha256, created_at_ms, created_by)
                     VALUES ('revision-2', 'document-1',
                        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                        1, 'actor')",
            ),
            (
                "project document pair",
                "INSERT INTO project_documents
                        (project_id, document_id, linked_at_ms, linked_by)
                     VALUES ('project-1', 'document-1', 1, 'actor')",
            ),
            (
                "sheet revision page",
                "INSERT INTO sheets
                        (id, revision_id, zero_based_page_index, width_micropoints,
                         height_micropoints, rotation_degrees, unit, parent_content_sha256,
                         transform_json)
                     VALUES ('sheet-2', 'revision-1', 0, 1, 1, 0, 'pt',
                        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                        '{}')",
            ),
            (
                "job idempotency tuple",
                "INSERT INTO job_runs
                        (id, project_id, kind, idempotency_key, state, attempt, deadline_at_ms,
                         budget_json, input_json, checkpoint_json, created_at_ms, updated_at_ms)
                     VALUES ('job-2', 'project-1', 'pdf_ingest', 'collision', 'queued', 0,
                        100, '{}', '{}', '{}', 1, 1)",
            ),
            (
                "evidence identity tuple",
                "INSERT INTO evidence_objects
                        (id, project_id, job_id, document_revision_id, content_sha256,
                         parent_content_sha256, extraction_method, parameters_json, review_state,
                         created_at_ms)
                     VALUES ('evidence-2', 'project-1', 'job-1', 'revision-1',
                        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                        'fixture', '{}', 'unreviewed', 1)",
            ),
            (
                "audit sequence",
                "INSERT INTO audit_events
                        (id, sequence, project_id, actor, action, subject_type, subject_id,
                         before_json, after_json, reason, occurred_at_ms, previous_hash,
                         event_hash)
                     VALUES ('audit-2', 1, 'project-1', 'actor', 'project_created', 'project',
                        'project-1', '{}', '{}', 'reason', 1,
                        '0000000000000000000000000000000000000000000000000000000000000000',
                        'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc')",
            ),
            (
                "audit event hash",
                "INSERT INTO audit_events
                        (id, sequence, project_id, actor, action, subject_type, subject_id,
                         before_json, after_json, reason, occurred_at_ms, previous_hash,
                         event_hash)
                     VALUES ('audit-3', 2, 'project-1', 'actor', 'project_created', 'project',
                        'project-1', '{}', '{}', 'reason', 1,
                        '0000000000000000000000000000000000000000000000000000000000000000',
                        'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb')",
            ),
        ];
        for (label, sql) in uniqueness_collisions {
            assert!(tx.execute(sql, []).is_err(), "accepted {label} collision");
        }

        let mut actual_foreign_keys = Vec::new();
        for child in TABLES {
            let mut statement = tx
                .prepare(
                    "SELECT \"from\", \"table\", \"to\", on_delete
                         FROM pragma_foreign_key_list(?1)",
                )
                .expect("prepare foreign-key introspection");
            let rows = statement
                .query_map([child], |row| {
                    Ok((
                        child.to_owned(),
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, String>(3)?,
                    ))
                })
                .expect("query foreign-key declarations");
            actual_foreign_keys.extend(
                rows.collect::<rusqlite::Result<Vec<_>>>()
                    .expect("collect foreign-key declarations"),
            );
        }
        actual_foreign_keys.sort();
        let mut expected_foreign_keys = vec![
            ("audit_events", "project_id", "projects", "id", "RESTRICT"),
            (
                "corrections",
                "evidence_id",
                "evidence_objects",
                "id",
                "RESTRICT",
            ),
            ("corrections", "project_id", "projects", "id", "RESTRICT"),
            (
                "document_revisions",
                "content_sha256",
                "content_objects",
                "sha256",
                "RESTRICT",
            ),
            (
                "document_revisions",
                "document_id",
                "documents",
                "id",
                "RESTRICT",
            ),
            (
                "evidence_objects",
                "content_sha256",
                "content_objects",
                "sha256",
                "RESTRICT",
            ),
            (
                "evidence_objects",
                "document_revision_id",
                "document_revisions",
                "id",
                "RESTRICT",
            ),
            ("evidence_objects", "job_id", "job_runs", "id", "RESTRICT"),
            (
                "evidence_objects",
                "parent_content_sha256",
                "content_objects",
                "sha256",
                "RESTRICT",
            ),
            (
                "evidence_objects",
                "project_id",
                "projects",
                "id",
                "RESTRICT",
            ),
            (
                "ingest_events",
                "content_sha256",
                "content_objects",
                "sha256",
                "RESTRICT",
            ),
            ("ingest_events", "job_id", "job_runs", "id", "RESTRICT"),
            ("ingest_events", "project_id", "projects", "id", "RESTRICT"),
            ("job_runs", "project_id", "projects", "id", "RESTRICT"),
            (
                "project_documents",
                "document_id",
                "documents",
                "id",
                "RESTRICT",
            ),
            (
                "project_documents",
                "project_id",
                "projects",
                "id",
                "CASCADE",
            ),
            ("scales", "sheet_id", "sheets", "id", "RESTRICT"),
            (
                "sheets",
                "parent_content_sha256",
                "content_objects",
                "sha256",
                "RESTRICT",
            ),
            (
                "sheets",
                "revision_id",
                "document_revisions",
                "id",
                "RESTRICT",
            ),
            (
                "source_records",
                "content_sha256",
                "content_objects",
                "sha256",
                "RESTRICT",
            ),
            ("source_records", "job_id", "job_runs", "id", "RESTRICT"),
            ("source_records", "project_id", "projects", "id", "RESTRICT"),
        ];
        expected_foreign_keys.sort();
        assert_eq!(
            actual_foreign_keys,
            expected_foreign_keys
                .into_iter()
                .map(|(child, from, parent, to, on_delete)| (
                    child.to_owned(),
                    from.to_owned(),
                    parent.to_owned(),
                    to.to_owned(),
                    on_delete.to_owned(),
                ))
                .collect::<Vec<_>>()
        );

        tx.execute(
            "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
                 VALUES ('cascade-project', 'name', 1, 'actor', 'INTERNAL')",
            [],
        )
        .expect("insert cascade project");
        tx.execute(
            "INSERT INTO documents (id, created_at_ms, created_by)
                 VALUES ('cascade-document', 1, 'actor')",
            [],
        )
        .expect("insert cascade document");
        tx.execute(
            "INSERT INTO project_documents
                    (project_id, document_id, linked_at_ms, linked_by)
                 VALUES ('cascade-project', 'cascade-document', 1, 'actor')",
            [],
        )
        .expect("insert cascade link");
        assert!(
            tx.execute("DELETE FROM documents WHERE id = 'cascade-document'", [],)
                .is_err(),
            "document-side RESTRICT was not enforced"
        );
        tx.execute("DELETE FROM projects WHERE id = 'cascade-project'", [])
            .expect("project-side CASCADE delete");
        assert_eq!(
            tx.query_row(
                "SELECT count(*) FROM project_documents
                     WHERE document_id = 'cascade-document'",
                [],
                |row| row.get::<_, i64>(0),
            )
            .expect("count cascaded links"),
            0
        );
        assert!(
            tx.execute("DELETE FROM projects WHERE id = 'project-1'", [])
                .is_err(),
            "dependent project RESTRICT actions were not enforced"
        );
        Ok(())
    })
    .expect("verify uniqueness, foreign-key, and delete matrix");
}

#[test]
fn every_json_column_rejects_valid_but_non_jcs_text_at_raw_transaction_boundary() {
    let database = TestDatabase::new("jcs-boundary");
    drop(migrated_store(&database));

    with_raw_verifier_transaction(&database, |tx| {
        tx.execute(
            "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
            params!["jcs-project", "name", 1_i64, "actor", "INTERNAL"],
        )
        .expect("insert project prerequisite");
        tx.execute(
            "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
            params!["jcs-project-2", "name", 1_i64, "actor", "INTERNAL"],
        )
        .expect("insert independent project prerequisite");
        tx.execute(
            "INSERT INTO content_objects
                    (sha256, byte_length, admission_state, vault_key, created_at_ms, created_by)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params!["1".repeat(64), 1_i64, "accepted", "key", 1_i64, "actor"],
        )
        .expect("insert content prerequisite");
        tx.execute(
            "INSERT INTO documents (id, created_at_ms, created_by) VALUES (?1, ?2, ?3)",
            params!["jcs-document", 1_i64, "actor"],
        )
        .expect("insert document prerequisite");
        tx.execute(
            "INSERT INTO document_revisions
                    (id, document_id, content_sha256, created_at_ms, created_by)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
            params![
                "jcs-revision",
                "jcs-document",
                "1".repeat(64),
                1_i64,
                "actor"
            ],
        )
        .expect("insert revision prerequisite");
        tx.execute(
            "INSERT INTO job_runs
                    (id, project_id, kind, idempotency_key, state, attempt, deadline_at_ms,
                     budget_json, input_json, checkpoint_json, created_at_ms, updated_at_ms)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
            params![
                "jcs-job",
                "jcs-project",
                "pdf_ingest",
                "canonical",
                "queued",
                0_i64,
                100_i64,
                "{}",
                "{}",
                "{}",
                1_i64,
                1_i64
            ],
        )
        .expect("insert job prerequisite");
        tx.execute(
            "INSERT INTO evidence_objects
                    (id, project_id, job_id, document_revision_id, content_sha256,
                     parent_content_sha256, extraction_method, parameters_json, review_state,
                     created_at_ms)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            params![
                "jcs-evidence",
                "jcs-project",
                "jcs-job",
                "jcs-revision",
                "1".repeat(64),
                "1".repeat(64),
                "fixture",
                "{}",
                "unreviewed",
                1_i64
            ],
        )
        .expect("insert evidence prerequisite");

        for (id, key, budget, input, checkpoint) in [
            ("bad-budget", "bad-budget", "{ }", "{}", "{}"),
            ("bad-input", "bad-input", "{}", "{\"b\":2,\"a\":1}", "{}"),
            (
                "bad-checkpoint",
                "bad-checkpoint",
                "{}",
                "{}",
                "{\"n\":1.0}",
            ),
        ] {
            assert!(
                tx.execute(
                    "INSERT INTO job_runs
                            (id, project_id, kind, idempotency_key, state, attempt, deadline_at_ms,
                             budget_json, input_json, checkpoint_json, created_at_ms, updated_at_ms)
                         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12)",
                    params![
                        id,
                        "jcs-project",
                        "pdf_ingest",
                        key,
                        "queued",
                        0_i64,
                        100_i64,
                        budget,
                        input,
                        checkpoint,
                        1_i64,
                        1_i64
                    ],
                )
                .is_err(),
                "non-JCS job JSON was accepted for {id}"
            );
        }

        assert!(
            tx.execute(
                "INSERT INTO ingest_events
                        (id, project_id, job_id, outcome, attempt, source_name, source_path,
                         idempotency_key, actor, terminal_at_ms, details_json)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, '<redacted>', ?7, ?8, ?9, ?10)",
                params![
                    "bad-details",
                    "jcs-project",
                    "jcs-job",
                    "interrupted",
                    1_i64,
                    "name",
                    "key",
                    "actor",
                    1_i64,
                    "{\"text\":\"\\u0061\"}"
                ],
            )
            .is_err()
        );
        assert!(
            tx.execute(
                "INSERT INTO sheets
                        (id, revision_id, zero_based_page_index, width_micropoints,
                         height_micropoints, rotation_degrees, unit, parent_content_sha256,
                         transform_json)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
                params![
                    "bad-transform",
                    "jcs-revision",
                    0_i64,
                    1_i64,
                    1_i64,
                    0_i64,
                    "pt",
                    "1".repeat(64),
                    "{\"\u{e000}\":1,\"\u{10000}\":2}"
                ],
            )
            .is_err()
        );
        assert!(
            tx.execute(
                "INSERT INTO source_records
                        (id, project_id, job_id, source_name, source_path, content_sha256,
                         metadata_json, created_at_ms)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                params![
                    "bad-metadata",
                    "jcs-project",
                    "jcs-job",
                    "name",
                    "<redacted>",
                    "1".repeat(64),
                    "{\"x\" :1}",
                    1_i64
                ],
            )
            .is_err()
        );
        assert!(
            tx.execute(
                "INSERT INTO evidence_objects
                        (id, project_id, job_id, document_revision_id, content_sha256,
                         parent_content_sha256, extraction_method, parameters_json, review_state,
                         created_at_ms)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
                params![
                    "bad-parameters",
                    "jcs-project-2",
                    "jcs-job",
                    "jcs-revision",
                    "1".repeat(64),
                    "1".repeat(64),
                    "fixture",
                    "{\"n\":1e0}",
                    "unreviewed",
                    1_i64
                ],
            )
            .is_err()
        );
        for (id, before, after) in [
            ("bad-correction-before", "[] ", "[]"),
            ("bad-correction-after", "[]", "{\"x\":\"\\/\"}"),
        ] {
            assert!(
                tx.execute(
                    "INSERT INTO corrections
                            (id, project_id, evidence_id, actor, reason, before_json, after_json,
                             created_at_ms)
                         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                    params![
                        id,
                        "jcs-project",
                        "jcs-evidence",
                        "actor",
                        "reason",
                        before,
                        after,
                        1_i64
                    ],
                )
                .is_err(),
                "non-JCS correction JSON was accepted for {id}"
            );
        }
        for (id, sequence, before, after, hash) in [
            (
                "bad-audit-before",
                1_i64,
                "{\"x\":-0}",
                "{}",
                "2".repeat(64),
            ),
            (
                "bad-audit-after",
                2_i64,
                "{}",
                "{\"x\":1.00}",
                "3".repeat(64),
            ),
        ] {
            assert!(
                tx.execute(
                    "INSERT INTO audit_events
                            (id, sequence, project_id, actor, action, subject_type, subject_id,
                             before_json, after_json, reason, occurred_at_ms, previous_hash,
                             event_hash)
                         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)",
                    params![
                        id,
                        sequence,
                        "jcs-project",
                        "actor",
                        "project_created",
                        "project",
                        "jcs-project",
                        before,
                        after,
                        "reason",
                        1_i64,
                        "0".repeat(64),
                        hash
                    ],
                )
                .is_err(),
                "non-JCS audit JSON was accepted for {id}"
            );
        }
        Ok(())
    })
    .expect("check every JCS insertion boundary");
}

#[test]
fn immutable_foundation_rows_reject_updates_and_deletes() {
    let database = TestDatabase::new("immutable");
    drop(migrated_store(&database));
    insert_complete_fixture(&database, "fixture");

    with_raw_verifier_transaction(&database, |tx| {
            let mutations = [
                "UPDATE content_objects SET byte_length = 99 WHERE sha256 = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'",
                "DELETE FROM content_objects WHERE sha256 = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'",
                "UPDATE document_revisions SET created_by = 'changed' WHERE id = 'revision-1'",
                "DELETE FROM document_revisions WHERE id = 'revision-1'",
                "UPDATE sheets SET rotation_degrees = 90 WHERE id = 'sheet-1'",
                "DELETE FROM sheets WHERE id = 'sheet-1'",
                "UPDATE evidence_objects SET review_state = 'accepted' WHERE id = 'evidence-1'",
                "DELETE FROM evidence_objects WHERE id = 'evidence-1'",
                "UPDATE ingest_events SET actor = 'changed' WHERE id = 'ingest-1'",
                "DELETE FROM ingest_events WHERE id = 'ingest-1'",
                "UPDATE audit_events SET actor = 'changed' WHERE id = 'audit-1'",
                "DELETE FROM audit_events WHERE id = 'audit-1'",
            ];
            for sql in mutations {
                assert!(tx.execute(sql, []).is_err(), "mutation unexpectedly passed: {sql}");
            }
            Ok(())
    })
    .expect("immutable checks complete");
}

#[test]
fn quarantined_content_cannot_create_a_revision_or_sheet() {
    let database = TestDatabase::new("quarantine-boundary");
    drop(migrated_store(&database));

    with_raw_verifier_transaction(&database, |tx| {
        tx.execute(
            "INSERT INTO content_objects
                    (sha256, byte_length, admission_state, vault_key, created_at_ms, created_by,
                     quarantine_reason)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                "f".repeat(64),
                7_i64,
                "quarantined",
                "quarantine-key",
                1_i64,
                "actor",
                "{}"
            ],
        )
        .expect("insert quarantined content");
        tx.execute(
            "INSERT INTO documents (id, created_at_ms, created_by) VALUES (?1, ?2, ?3)",
            params!["quarantined-document", 1_i64, "actor"],
        )
        .expect("insert document shell");
        assert!(
            tx.execute(
                "INSERT INTO document_revisions
                        (id, document_id, content_sha256, created_at_ms, created_by)
                     VALUES (?1, ?2, ?3, ?4, ?5)",
                params![
                    "quarantined-revision",
                    "quarantined-document",
                    "f".repeat(64),
                    1_i64,
                    "actor"
                ],
            )
            .is_err()
        );
        let revision_count = tx
            .query_row(
                "SELECT count(*) FROM document_revisions WHERE id = ?1",
                ["quarantined-revision"],
                |row| row.get::<_, i64>(0),
            )
            .expect("count rejected revision");
        let sheet_count = tx
            .query_row("SELECT count(*) FROM sheets", [], |row| {
                row.get::<_, i64>(0)
            })
            .expect("count rejected sheets");
        assert_eq!((revision_count, sheet_count), (0, 0));
        Ok(())
    })
    .expect("verify quarantine boundary");
}

#[test]
fn raw_transactions_enforce_sheet_scale_and_accepted_evidence_lineage() {
    let database = TestDatabase::new("raw-lineage-boundary");
    drop(migrated_store(&database));
    insert_complete_fixture(&database, "lineage");

    with_raw_verifier_transaction(&database, |tx| {
        for (digest, state, reason) in [
            ("d".repeat(64), "accepted", None),
            ("f".repeat(64), "quarantined", Some("{}")),
        ] {
            tx.execute(
                "INSERT INTO content_objects
                        (sha256, byte_length, admission_state, vault_key, created_at_ms,
                         created_by, quarantine_reason)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                params![
                    digest,
                    1_i64,
                    state,
                    format!("vault-{state}"),
                    1_i64,
                    "actor",
                    reason
                ],
            )
            .expect("insert lineage fixture content");
        }

        for (sheet_id, parent_hash) in [
            ("sheet-quarantined-parent", "f".repeat(64)),
            ("sheet-mismatched-accepted-parent", "d".repeat(64)),
        ] {
            assert!(
                tx.execute(
                    "INSERT INTO sheets
                            (id, revision_id, zero_based_page_index, width_micropoints,
                             height_micropoints, rotation_degrees, unit, parent_content_sha256,
                             transform_json)
                         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
                    params![
                        sheet_id,
                        "revision-1",
                        1_i64,
                        1_i64,
                        1_i64,
                        0_i64,
                        "pt",
                        parent_hash,
                        "{}"
                    ],
                )
                .is_err(),
                "raw SQL attached invalid parent content to a sheet"
            );
            assert!(
                tx.execute(
                    "INSERT INTO scales
                            (id, sheet_id, numerator, denominator, source, created_at_ms,
                             created_by)
                         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                    params![
                        format!("scale-{sheet_id}"),
                        sheet_id,
                        1_i64,
                        1_i64,
                        "fixture",
                        1_i64,
                        "actor"
                    ],
                )
                .is_err(),
                "raw SQL attached a scale to an invalid sheet"
            );
        }

        for (id, derivative, parent) in [
            (
                "accepted-quarantined-derivative",
                "f".repeat(64),
                "a".repeat(64),
            ),
            (
                "accepted-quarantined-parent",
                "d".repeat(64),
                "f".repeat(64),
            ),
            ("accepted-mismatched-parent", "d".repeat(64), "d".repeat(64)),
        ] {
            assert!(
                tx.execute(
                    "INSERT INTO evidence_objects
                            (id, project_id, job_id, document_revision_id, content_sha256,
                             parent_content_sha256, extraction_method, parameters_json,
                             review_state, created_at_ms)
                         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
                    params![
                        id,
                        "project-1",
                        "job-1",
                        "revision-1",
                        derivative,
                        parent,
                        "fixture",
                        "{}",
                        "accepted",
                        1_i64
                    ],
                )
                .is_err(),
                "raw SQL admitted invalid accepted evidence lineage: {id}"
            );
        }

        tx.execute(
            "INSERT INTO evidence_objects
                    (id, project_id, job_id, document_revision_id, content_sha256,
                     parent_content_sha256, extraction_method, parameters_json, review_state,
                     created_at_ms)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            params![
                "unreviewed-quarantined-evidence",
                "project-1",
                "job-1",
                "revision-1",
                "f".repeat(64),
                "f".repeat(64),
                "fixture",
                "{}",
                "unreviewed",
                1_i64
            ],
        )
        .expect("non-accepted review state preserves quarantined evidence");
        Ok(())
    })
    .expect("complete raw lineage boundary checks");
}

#[test]
fn a_schema_newer_than_the_binary_fails_closed() {
    let database = TestDatabase::new("future-version");
    drop(migrated_store(&database));
    with_raw_verifier_transaction(&database, |tx| {
        tx.execute(
            "INSERT INTO schema_migrations (version, sha256) VALUES (?1, ?2)",
            params![2_i64, "d".repeat(64)],
        )
        .expect("insert future version");
        Ok(())
    })
    .expect("commit future marker");

    let mut store = Store::open_writer(&database.path).expect("reopen future-version database");
    assert!(matches!(store.migrate(), Err(HeleosError::Migration)));
}

#[test]
fn a_migration_hash_mismatch_fails_closed() {
    let database = TestDatabase::new("migration-hash");
    drop(migrated_store(&database));
    with_raw_verifier_transaction(&database, |tx| {
        tx.execute(
            "UPDATE schema_migrations SET sha256 = ?1 WHERE version = ?2",
            params!["e".repeat(64), 1_i64],
        )
        .expect("tamper migration hash");
        Ok(())
    })
    .expect("commit hash tamper");

    let mut store = Store::open_writer(&database.path).expect("reopen tampered database");
    assert!(matches!(store.migrate(), Err(HeleosError::Migration)));
}

#[test]
fn writer_lock_process_helper() {
    let Some(database) = env::var_os("HELEOS_TEST_LOCK_DATABASE") else {
        return;
    };
    let _store = Store::open_writer(database).expect("child obtains writer lock");
    println!("HELEOS_WRITER_LOCKED");
    std::io::stdout().flush().expect("flush child readiness");
    let mut release = [0_u8; 1];
    let _ = std::io::stdin().read(&mut release);
}

#[test]
fn paused_writer_lock_process_helper() {
    let Some(database) = env::var_os("HELEOS_TEST_PAUSED_WRITER_DATABASE") else {
        return;
    };
    let database = PathBuf::from(database);
    let lock_path = database_sidecar(&database, ".writer.lock");
    let lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .open(&lock_path)
        .expect("create paused-writer application lock");
    apply_private_permissions(&lock_path).expect("harden paused-writer application lock");
    lock.lock_exclusive()
        .expect("acquire paused-writer exclusive lock");
    println!("HELEOS_PAUSED_WRITER_LOCKED");
    std::io::stdout()
        .flush()
        .expect("flush paused-writer signal");
    let mut release = [0_u8; 1];
    let _ = std::io::stdin().read(&mut release);
}

#[test]
fn crash_left_wal_process_helper() {
    let Some(database) = env::var_os("HELEOS_TEST_CRASH_DATABASE") else {
        return;
    };
    let mut store = Store::open_writer(database).expect("crash fixture opens writer");
    store.migrate().expect("crash fixture migrates");
    drop(store);
    let crash_database = PathBuf::from(
        env::var_os("HELEOS_TEST_CRASH_DATABASE").expect("crash database remains configured"),
    );
    let connection = raw_verifier_connection_path(&crash_database);
    connection
        .execute(
            "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
             VALUES (?1, ?2, ?3, ?4, ?5)",
            params!["crash-project", "name", 1_i64, "actor", "INTERNAL"],
        )
        .expect("write crash-left WAL row");
    std::process::exit(0);
}

#[test]
fn independent_processes_cannot_both_hold_the_writer_lock() {
    let database = TestDatabase::new("process-lock");
    let mut child = Command::new(env::current_exe().expect("locate test executable"))
        .arg("--exact")
        .arg("writer_lock_process_helper")
        .arg("--nocapture")
        .env("HELEOS_TEST_LOCK_DATABASE", &database.path)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .expect("spawn lock holder");

    let stdout = child.stdout.take().expect("capture child stdout");
    let mut reader = BufReader::new(stdout);
    let mut line = String::new();
    loop {
        let bytes = reader.read_line(&mut line).expect("read child readiness");
        assert_ne!(bytes, 0, "child exited before acquiring the lock");
        if line.contains("HELEOS_WRITER_LOCKED") {
            break;
        }
        line.clear();
    }

    assert!(matches!(
        Store::open_writer(&database.path),
        Err(HeleosError::WriterBusy)
    ));
    drop(child.stdin.take());
    assert!(child.wait().expect("wait for lock holder").success());
}

#[test]
fn reader_contends_on_existing_lock_before_database_or_sidecar_inspection() {
    let database = TestDatabase::new("reader-lock-first");
    apply_private_permissions(&database.root).expect("harden paused-writer parent");
    let mut child = Command::new(env::current_exe().expect("locate test executable"))
        .arg("--exact")
        .arg("paused_writer_lock_process_helper")
        .arg("--nocapture")
        .env("HELEOS_TEST_PAUSED_WRITER_DATABASE", &database.path)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .expect("spawn paused writer");
    let stdout = child.stdout.take().expect("capture paused-writer stdout");
    let mut reader = BufReader::new(stdout);
    let mut line = String::new();
    loop {
        let bytes = reader
            .read_line(&mut line)
            .expect("read paused-writer readiness");
        assert_ne!(bytes, 0, "paused writer exited before locking");
        if line.contains("HELEOS_PAUSED_WRITER_LOCKED") {
            break;
        }
        line.clear();
    }
    assert!(!database.path.exists());

    match Store::open_read_only(&database.path) {
        Err(HeleosError::WriterBusy) => {}
        Err(error) => panic!("reader returned {error:?} instead of exact WriterBusy"),
        Ok(_) => panic!("reader opened while the exclusive application lock was held"),
    }

    drop(child.stdin.take());
    assert!(child.wait().expect("wait for paused writer").success());
}

#[cfg(unix)]
#[test]
fn writer_denied_by_live_reader_does_not_mutate_authoritative_metadata() {
    use std::os::unix::fs::PermissionsExt;

    let database = TestDatabase::new("denied-writer-no-mutation");
    spawn_crash_left_writer(&database);
    let wal = database_sidecar(&database.path, "-wal");
    let shm = database_sidecar(&database.path, "-shm");
    let reader = Store::open_read_only(&database.path).expect("hold shared reader lock");

    for path in [&database.path, &wal, &shm] {
        fs::set_permissions(path, fs::Permissions::from_mode(0o640))
            .expect("make authoritative source metadata detectably broad");
    }
    let paths = [&database.root, &database.path, &wal, &shm];
    let before = paths
        .iter()
        .map(|path| UnixMetadataSnapshot::read(path))
        .collect::<Vec<_>>();

    assert!(matches!(
        Store::open_writer(&database.path),
        Err(HeleosError::WriterBusy)
    ));
    let after = paths
        .iter()
        .map(|path| UnixMetadataSnapshot::read(path))
        .collect::<Vec<_>>();
    assert_eq!(after, before);

    drop(reader);
}

#[test]
fn read_only_snapshot_denies_an_active_writer_with_exact_writer_busy() {
    let database = TestDatabase::new("reader-active-writer");
    let _writer = migrated_store(&database);

    assert!(matches!(
        Store::open_read_only(&database.path),
        Err(HeleosError::WriterBusy)
    ));
}

#[test]
fn read_only_snapshot_retains_shared_lock_for_its_lifetime() {
    let database = TestDatabase::new("reader-shared-lock");
    drop(migrated_store(&database));
    let reader = Store::open_read_only(&database.path).expect("open snapshot reader");

    assert!(matches!(
        Store::open_writer(&database.path),
        Err(HeleosError::WriterBusy)
    ));
    drop(reader);
    drop(Store::open_writer(&database.path).expect("writer resumes after reader drop"));
}

#[cfg(unix)]
#[test]
fn writer_fails_closed_before_mutation_after_lock_path_replacement() {
    use std::os::unix::fs::PermissionsExt;

    let database = TestDatabase::new("lock-replacement");
    let mut writer = migrated_store(&database);
    let lock_path = writer
        .writer_lock()
        .expect("writer retains lock")
        .path()
        .to_owned();
    let displaced_lock = database.root.join("displaced.writer.lock");

    fs::rename(&lock_path, &displaced_lock).expect("displace held writer lock path");
    File::create(&lock_path).expect("create replacement writer lock path");
    fs::set_permissions(&lock_path, fs::Permissions::from_mode(0o600))
        .expect("harden replacement lock path");

    assert!(matches!(writer.migrate(), Err(HeleosError::PolicyDenied)));
}

#[test]
fn read_only_verification_has_no_mutation_capability() {
    let database = TestDatabase::new("read-only");
    let store = migrated_store(&database);
    drop(store);

    let read_only = Store::open_read_only(&database.path).expect("open read-only store");
    assert_eq!(read_only.schema_version().expect("read schema version"), 1);
    assert!(
        read_only
            .verify_integrity()
            .expect("verify read-only")
            .is_clean()
    );
}

#[cfg(unix)]
#[test]
fn read_only_open_does_not_change_parent_database_wal_or_shm_metadata() {
    let database = TestDatabase::new("read-only-metadata");
    spawn_crash_left_writer(&database);

    let wal = database_sidecar(&database.path, "-wal");
    let shm = database_sidecar(&database.path, "-shm");
    assert!(wal.is_file(), "WAL must exist for the snapshot");
    assert!(shm.is_file(), "SHM must exist for the snapshot");
    let paths = [&database.root, &database.path, &wal, &shm];
    let before = paths
        .iter()
        .map(|path| UnixMetadataSnapshot::read(path))
        .collect::<Vec<_>>();
    let bytes_before = [&database.path, &wal, &shm]
        .map(|path| fs::read(path).expect("read source snapshot bytes"));

    let reader = Store::open_read_only(&database.path).expect("open verified reader");
    assert_eq!(reader.schema_version().expect("reader schema version"), 1);
    assert!(
        reader
            .verify_integrity()
            .expect("verify recovered snapshot")
            .is_clean()
    );
    drop(reader);

    let after = paths
        .iter()
        .map(|path| UnixMetadataSnapshot::read(path))
        .collect::<Vec<_>>();
    assert_eq!(after, before);
    assert_eq!(
        [&database.path, &wal, &shm]
            .map(|path| fs::read(path).expect("reread source snapshot bytes")),
        bytes_before
    );
}

#[cfg(unix)]
#[test]
fn read_only_open_does_not_create_missing_sidecars_or_mutate_clean_source() {
    let database = TestDatabase::new("read-only-clean-source");
    drop(migrated_store(&database));
    let checkpoint = Connection::open(&database.path).expect("open clean checkpoint fixture");
    checkpoint
        .execute_batch("PRAGMA wal_checkpoint(TRUNCATE);")
        .expect("checkpoint clean fixture database");
    drop(checkpoint);
    let wal = database_sidecar(&database.path, "-wal");
    let shm = database_sidecar(&database.path, "-shm");
    for sidecar in [&wal, &shm] {
        match fs::remove_file(sidecar) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => panic!("remove clean SQLite sidecar: {error}"),
        }
    }
    let metadata_before = [
        UnixMetadataSnapshot::read(&database.root),
        UnixMetadataSnapshot::read(&database.path),
    ];
    let database_bytes_before = fs::read(&database.path).expect("read clean source bytes");

    let reader = Store::open_read_only(&database.path).expect("open clean snapshot reader");
    assert_eq!(reader.schema_version().expect("snapshot schema version"), 1);
    drop(reader);

    assert!(!wal.exists());
    assert!(!shm.exists());
    assert_eq!(
        [
            UnixMetadataSnapshot::read(&database.root),
            UnixMetadataSnapshot::read(&database.path),
        ],
        metadata_before
    );
    assert_eq!(
        fs::read(&database.path).expect("reread clean source bytes"),
        database_bytes_before
    );
}

#[test]
fn read_only_snapshot_recovers_a_crash_left_wal() {
    let database = TestDatabase::new("read-only-crash-recovery");
    spawn_crash_left_writer(&database);
    assert!(database_sidecar(&database.path, "-wal").is_file());

    let reader = Store::open_read_only(&database.path).expect("open crash-left snapshot");
    assert_eq!(
        reader.schema_version().expect("recovered schema version"),
        1
    );
    assert!(
        reader
            .verify_integrity()
            .expect("verify crash-left snapshot")
            .is_clean()
    );
}

#[cfg(unix)]
#[test]
fn read_only_open_rejects_broad_permissions_without_repairing_them() {
    use std::os::unix::fs::{PermissionsExt, symlink};

    let database = TestDatabase::new("read-only-permissions");
    spawn_crash_left_writer(&database);
    let wal = database_sidecar(&database.path, "-wal");
    let shm = database_sidecar(&database.path, "-shm");

    fs::set_permissions(&database.root, fs::Permissions::from_mode(0o755))
        .expect("make parent broad");
    assert!(matches!(
        Store::open_read_only(&database.path),
        Err(HeleosError::PolicyDenied)
    ));
    assert_eq!(
        fs::symlink_metadata(&database.root)
            .expect("parent metadata")
            .permissions()
            .mode()
            & 0o777,
        0o755
    );
    fs::set_permissions(&database.root, fs::Permissions::from_mode(0o700))
        .expect("restore parent mode");

    fs::set_permissions(&database.path, fs::Permissions::from_mode(0o644))
        .expect("make database broad");
    assert!(matches!(
        Store::open_read_only(&database.path),
        Err(HeleosError::PolicyDenied)
    ));
    assert_eq!(
        fs::symlink_metadata(&database.path)
            .expect("database metadata")
            .permissions()
            .mode()
            & 0o777,
        0o644
    );
    fs::set_permissions(&database.path, fs::Permissions::from_mode(0o600))
        .expect("restore database mode");

    fs::set_permissions(&wal, fs::Permissions::from_mode(0o644)).expect("make WAL broad");
    assert!(matches!(
        Store::open_read_only(&database.path),
        Err(HeleosError::PolicyDenied)
    ));
    assert_eq!(
        fs::symlink_metadata(&wal)
            .expect("WAL metadata")
            .permissions()
            .mode()
            & 0o777,
        0o644
    );
    fs::set_permissions(&wal, fs::Permissions::from_mode(0o600)).expect("restore WAL mode");

    fs::set_permissions(&shm, fs::Permissions::from_mode(0o644)).expect("make SHM broad");
    assert!(matches!(
        Store::open_read_only(&database.path),
        Err(HeleosError::PolicyDenied)
    ));
    assert_eq!(
        fs::symlink_metadata(&shm)
            .expect("SHM metadata")
            .permissions()
            .mode()
            & 0o777,
        0o644
    );
    fs::set_permissions(&shm, fs::Permissions::from_mode(0o600)).expect("restore SHM mode");

    let target = database.root.join("hostile-sidecar-target");
    File::create(&target).expect("create hostile target");
    fs::remove_file(&wal).expect("remove real WAL before hostile replacement");
    symlink(&target, &wal).expect("create WAL symlink");
    assert!(matches!(
        Store::open_read_only(&database.path),
        Err(HeleosError::PolicyDenied)
    ));
    fs::remove_file(&wal).expect("remove WAL symlink");

    fs::remove_file(&shm).expect("remove real SHM before hostile replacement");
    fs::create_dir(&shm).expect("create nonregular SHM");
    assert!(matches!(
        Store::open_read_only(&database.path),
        Err(HeleosError::PolicyDenied)
    ));
}

#[cfg(unix)]
#[test]
fn writer_hardens_existing_wal_and_shm_before_returning() {
    use std::os::unix::fs::PermissionsExt;

    let database = TestDatabase::new("writer-sidecar-permissions");
    let writer = migrated_store(&database);
    let flags = OpenFlags::SQLITE_OPEN_READ_ONLY
        | OpenFlags::SQLITE_OPEN_NO_MUTEX
        | OpenFlags::SQLITE_OPEN_NOFOLLOW
        | OpenFlags::SQLITE_OPEN_PRIVATE_CACHE;
    let keeper = Connection::open_with_flags(&database.path, flags)
        .expect("open connection that retains SQLite sidecars");
    keeper
        .query_row("SELECT count(*) FROM projects", [], |row| {
            row.get::<_, i64>(0)
        })
        .expect("activate sidecar keeper");
    drop(writer);

    let wal = database_sidecar(&database.path, "-wal");
    let shm = database_sidecar(&database.path, "-shm");
    assert!(wal.is_file());
    assert!(shm.is_file());
    fs::set_permissions(&wal, fs::Permissions::from_mode(0o644)).expect("make WAL broad");
    fs::set_permissions(&shm, fs::Permissions::from_mode(0o644)).expect("make SHM broad");

    let hardened_writer = Store::open_writer(&database.path).expect("open hardening writer");
    for sidecar in [&wal, &shm] {
        assert_eq!(
            fs::symlink_metadata(sidecar)
                .expect("read hardened sidecar metadata")
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
    }
    drop(hardened_writer);
    drop(keeper);
}

#[test]
fn integrity_quick_and_foreign_key_checks_report_clean() {
    let database = TestDatabase::new("integrity");
    let store = migrated_store(&database);
    let report = store.verify_integrity().expect("verify integrity");

    assert!(report.integrity_check_violations.is_empty());
    assert!(!report.integrity_check_truncated);
    assert!(report.quick_check_violations.is_empty());
    assert!(!report.quick_check_truncated);
    assert!(report.foreign_key_violations.is_empty());
    assert!(!report.foreign_key_check_truncated);
    assert!(report.is_clean());
}

#[test]
fn integrity_reporting_does_not_silently_stop_at_sqlites_default_100_rows() {
    let database = TestDatabase::new("integrity-dirty");
    drop(migrated_store(&database));

    let connection = raw_verifier_connection(&database);
    connection
        .pragma_update(None, "foreign_keys", "OFF")
        .expect("permit deliberate foreign-key violations");
    let page_size: u64 = connection
        .pragma_query_value(None, "page_size", |row| row.get::<_, i64>(0))
        .expect("read SQLite page size")
        .try_into()
        .expect("positive SQLite page size");
    connection
        .execute("CREATE TABLE fixture_rows (value INTEGER) STRICT", [])
        .expect("create indexed fixture table");
    for index in 0..150_i64 {
        connection
            .execute("INSERT INTO fixture_rows (value) VALUES (?1)", [index])
            .expect("insert indexed fixture row");
        connection
            .execute(
                "INSERT INTO project_documents
                    (project_id, document_id, linked_at_ms, linked_by)
                 VALUES (?1, ?2, ?3, ?4)",
                params![
                    format!("missing-project-{index}"),
                    format!("missing-document-{index}"),
                    index,
                    "actor"
                ],
            )
            .expect("insert deliberate foreign-key violation");
    }
    connection
        .execute("CREATE INDEX fixture_rows_index ON fixture_rows(value)", [])
        .expect("create populated fixture index");
    connection
        .execute("CREATE TABLE fixture_empty (value INTEGER) STRICT", [])
        .expect("create empty fixture table");
    connection
        .execute(
            "CREATE INDEX fixture_empty_index ON fixture_empty(value)",
            [],
        )
        .expect("create empty fixture index");
    connection
        .execute("CREATE TABLE fixture_corrupt (value INTEGER) STRICT", [])
        .expect("create independently corruptible table");
    let root_page = |name: &str, object_type: &str| -> u64 {
        connection
            .query_row(
                "SELECT rootpage FROM sqlite_schema WHERE type = ?1 AND name = ?2",
                params![object_type, name],
                |row| row.get::<_, i64>(0),
            )
            .expect("read fixture root page")
            .try_into()
            .expect("positive fixture root page")
    };
    let populated_index_root = root_page("fixture_rows_index", "index");
    let empty_index_root = root_page("fixture_empty_index", "index");
    connection
        .pragma_update(None, "writable_schema", "ON")
        .expect("enable corruption fixture schema edit");
    connection
        .execute(
            "DELETE FROM sqlite_schema WHERE type = 'table' AND name = ?1",
            ["fixture_corrupt"],
        )
        .expect("orphan fixture table root page");
    let schema_version = connection
        .pragma_query_value(None, "schema_version", |row| row.get::<_, i64>(0))
        .expect("read schema version cookie");
    connection
        .pragma_update(None, "schema_version", schema_version + 1)
        .expect("invalidate the fixture schema cache");
    connection
        .pragma_update(None, "writable_schema", "OFF")
        .expect("disable corruption fixture schema edit");
    drop(connection);
    let mut database_file = OpenOptions::new()
        .read(true)
        .write(true)
        .open(&database.path)
        .expect("open database file for deliberate corruption");
    let mut empty_index_page = vec![0_u8; page_size.try_into().expect("page size fits usize")];
    database_file
        .seek(SeekFrom::Start((empty_index_root - 1) * page_size))
        .expect("seek to empty index root page");
    database_file
        .read_exact(&mut empty_index_page)
        .expect("read empty index root page");
    database_file
        .seek(SeekFrom::Start((populated_index_root - 1) * page_size))
        .expect("seek to populated index root page");
    database_file
        .write_all(&empty_index_page)
        .expect("replace populated index with a valid empty b-tree");
    database_file
        .sync_all()
        .expect("persist deliberate corruption");
    drop(database_file);
    for suffix in ["-wal", "-shm"] {
        let sidecar = database_sidecar(&database.path, suffix);
        if sidecar.exists() {
            apply_private_permissions(&sidecar).expect("harden raw fixture sidecar");
        }
    }

    let store = Store::open_read_only(&database.path).expect("open dirty database read-only");
    let report = store.verify_integrity().expect("report dirty integrity");

    assert_eq!(
        report.integrity_check_violations.len(),
        INTEGRITY_VIOLATION_LIMIT
    );
    assert!(report.integrity_check_truncated);
    assert!(!report.quick_check_violations.is_empty());
    assert!(!report.quick_check_truncated);
    assert_eq!(
        report.foreign_key_violations.len(),
        INTEGRITY_VIOLATION_LIMIT
    );
    assert!(report.foreign_key_check_truncated);
    assert!(!report.is_clean());
}

#[test]
fn private_permissions_are_applied_and_verified_by_readback() {
    let database = TestDatabase::new("permissions");
    let file_path = database.root.join("private.bin");
    File::create(&file_path).expect("create private file");

    apply_private_permissions(&database.root).expect("harden directory");
    apply_private_permissions(&file_path).expect("harden file");
    verify_private_permissions(&database.root).expect("verify directory permissions");
    verify_private_permissions(&file_path).expect("verify file permissions");
}

#[cfg(unix)]
#[test]
fn unix_private_permissions_have_exact_modes_and_reject_hostile_readback() {
    use std::os::unix::fs::PermissionsExt;

    let database = TestDatabase::new("permission-modes");
    let file_path = database.root.join("private.bin");
    File::create(&file_path).expect("create private file");

    apply_private_permissions(&database.root).expect("harden directory");
    apply_private_permissions(&file_path).expect("harden file");
    assert_eq!(
        fs::symlink_metadata(&database.root)
            .expect("directory metadata")
            .permissions()
            .mode()
            & 0o777,
        0o700
    );
    assert_eq!(
        fs::symlink_metadata(&file_path)
            .expect("file metadata")
            .permissions()
            .mode()
            & 0o777,
        0o600
    );

    fs::set_permissions(&database.root, fs::Permissions::from_mode(0o750))
        .expect("make directory permission hostile");
    assert!(matches!(
        verify_private_permissions(&database.root),
        Err(HeleosError::PolicyDenied)
    ));
    fs::set_permissions(&database.root, fs::Permissions::from_mode(0o700))
        .expect("restore directory permission");
    fs::set_permissions(&file_path, fs::Permissions::from_mode(0o640))
        .expect("make file permission hostile");
    assert!(matches!(
        verify_private_permissions(&file_path),
        Err(HeleosError::PolicyDenied)
    ));
}

#[cfg(unix)]
#[test]
fn symlink_and_non_regular_database_or_lock_paths_are_rejected() {
    use std::os::unix::fs::symlink;

    let database = TestDatabase::new("path-types");
    let target = database.root.join("target.sqlite3");
    File::create(&target).expect("create symlink target");
    symlink(&target, &database.path).expect("create database symlink");
    assert!(matches!(
        Store::open_writer(&database.path),
        Err(HeleosError::PolicyDenied)
    ));
    fs::remove_file(&database.path).expect("remove database symlink");
    let lock_path = database_sidecar(&database.path, ".writer.lock");
    fs::remove_file(&lock_path).expect("remove lock created before database inspection");

    let directory_database = database.root.join("directory.sqlite3");
    fs::create_dir(&directory_database).expect("create non-regular database path");
    assert!(matches!(
        Store::open_writer(&directory_database),
        Err(HeleosError::PolicyDenied)
    ));

    let lock_target = database.root.join("lock-target");
    File::create(&lock_target).expect("create lock target");
    symlink(&lock_target, &lock_path).expect("create lock symlink");
    assert!(matches!(
        Store::open_writer(&database.path),
        Err(HeleosError::PolicyDenied)
    ));
}

#[test]
fn in_memory_store_has_the_same_schema_and_defensive_policy() {
    let mut store = Store::open_in_memory().expect("open in-memory store");
    store.migrate().expect("migrate in-memory store");
    assert_eq!(store.schema_version().expect("schema version"), 1);
}
