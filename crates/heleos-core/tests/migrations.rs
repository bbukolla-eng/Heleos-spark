#![forbid(unsafe_code)]

use std::env;
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Read, Write};
use std::path::PathBuf;
use std::process::{Command, Stdio};

use heleos_core::{HeleosError, Store, apply_private_permissions, verify_private_permissions};
use rusqlite::config::DbConfig;
use rusqlite::params;
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

fn migrated_store(database: &TestDatabase) -> Store {
    let mut store = Store::open_writer(&database.path).expect("open writer");
    store.migrate().expect("migrate database");
    store
}

fn insert_complete_fixture(store: &mut Store, payload: &str) {
    let json = serde_json::to_string(&payload).expect("encode payload as JSON string");
    store
        .with_immediate_transaction(|tx| {
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
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, NULL, NULL, NULL, ?7, ?8, ?9, NULL, ?10, ?11)",
                params![
                    "job-1",
                    "project-1",
                    "pdf_ingest",
                    payload,
                    "succeeded",
                    1_i64,
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
                    (id, project_id, job_id, content_sha256, outcome, source_name, source_path,
                     idempotency_key, actor, terminal_at_ms, details_json)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
                params![
                    "ingest-1",
                    "project-1",
                    "job-1",
                    "a".repeat(64),
                    "accepted_new",
                    payload,
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
                    payload,
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
                    payload,
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
fn writer_connection_enforces_all_foundation_sqlite_settings() {
    let database = TestDatabase::new("connection-settings");
    let mut store = migrated_store(&database);

    let settings = store
        .with_immediate_transaction(|tx| {
            let foreign_keys = tx
                .pragma_query_value(None, "foreign_keys", |row| row.get::<_, i64>(0))
                .expect("query foreign_keys");
            let journal_mode = tx
                .pragma_query_value(None, "journal_mode", |row| row.get::<_, String>(0))
                .expect("query journal mode");
            let synchronous = tx
                .pragma_query_value(None, "synchronous", |row| row.get::<_, i64>(0))
                .expect("query synchronous");
            let trusted_schema = tx
                .db_config(DbConfig::SQLITE_DBCONFIG_TRUSTED_SCHEMA)
                .expect("query trusted schema");
            let defensive = tx
                .db_config(DbConfig::SQLITE_DBCONFIG_DEFENSIVE)
                .expect("query defensive mode");
            Ok((
                foreign_keys,
                journal_mode,
                synchronous,
                trusted_schema,
                defensive,
            ))
        })
        .expect("inspect settings");

    assert_eq!(settings, (1, "wal".to_owned(), 2, false, true));
}

#[test]
fn foundation_migration_creates_exact_required_tables() {
    let database = TestDatabase::new("table-set");
    let mut store = migrated_store(&database);
    let tables = store
        .with_immediate_transaction(|tx| {
            let mut statement = tx
                .prepare(
                    "SELECT name FROM sqlite_schema
                     WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                     ORDER BY name",
                )
                .expect("prepare table query");
            let names = statement
                .query_map([], |row| row.get::<_, String>(0))
                .expect("query table names")
                .collect::<rusqlite::Result<Vec<_>>>()
                .expect("collect table names");
            Ok(names)
        })
        .expect("inspect schema");

    assert_eq!(tables, TABLES);
}

#[test]
fn bound_sql_payloads_remain_inert_data_in_every_sensitive_text_class() {
    let database = TestDatabase::new("bound-values");
    let mut store = migrated_store(&database);
    let payload = "Robert'); DROP TABLE projects; --\n../vault/$HOME/\u{001b}[31m";
    insert_complete_fixture(&mut store, payload);

    let recovered = store
        .with_immediate_transaction(|tx| {
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
    assert_eq!(recovered.3, payload);
    assert_eq!(
        serde_json::from_str::<String>(&recovered.4).expect("decode stored JSON"),
        payload
    );
}

#[test]
fn foreign_keys_json_and_exact_persisted_enums_fail_closed() {
    let database = TestDatabase::new("constraints");
    let mut store = migrated_store(&database);

    store
        .with_immediate_transaction(|tx| {
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
                        (id, project_id, kind, idempotency_key, state, attempt, budget_json,
                         input_json, checkpoint_json, created_at_ms, updated_at_ms)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
                    params![
                        "job-invalid",
                        "project-valid",
                        "pdf_ingest",
                        "key",
                        "paused",
                        0_i64,
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
                    (id, project_id, kind, idempotency_key, state, attempt, budget_json,
                     input_json, checkpoint_json, created_at_ms, updated_at_ms)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
                params![
                    "job-valid",
                    "project-valid",
                    "pdf_ingest",
                    "valid-key",
                    "queued",
                    0_i64,
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
fn immutable_foundation_rows_reject_updates_and_deletes() {
    let database = TestDatabase::new("immutable");
    let mut store = migrated_store(&database);
    insert_complete_fixture(&mut store, "fixture");

    store
        .with_immediate_transaction(|tx| {
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
    let mut store = migrated_store(&database);

    store
        .with_immediate_transaction(|tx| {
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
                    "corrupt"
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
fn a_failing_second_migration_rolls_back_schema_and_version() {
    let database = TestDatabase::new("migration-rollback");
    let mut store = migrated_store(&database);

    let result = store.migrate_with_test_migration(
        2,
        "CREATE TABLE must_rollback (id TEXT PRIMARY KEY);
         INSERT INTO table_that_does_not_exist (id) VALUES ('failure');",
    );
    assert!(matches!(result, Err(HeleosError::Migration)));
    assert_eq!(store.schema_version().expect("schema version"), 1);

    let exists = store
        .with_immediate_transaction(|tx| {
            Ok(tx
                .query_row(
                    "SELECT count(*) FROM sqlite_schema WHERE type = 'table' AND name = ?1",
                    ["must_rollback"],
                    |row| row.get::<_, i64>(0),
                )
                .expect("query rolled-back table"))
        })
        .expect("inspect schema");
    assert_eq!(exists, 0);
}

#[test]
fn a_schema_newer_than_the_binary_fails_closed() {
    let database = TestDatabase::new("future-version");
    let mut store = migrated_store(&database);
    store
        .with_immediate_transaction(|tx| {
            tx.execute(
                "INSERT INTO schema_migrations (version, sha256) VALUES (?1, ?2)",
                params![2_i64, "d".repeat(64)],
            )
            .expect("insert future version");
            Ok(())
        })
        .expect("commit future marker");

    assert!(matches!(store.migrate(), Err(HeleosError::Migration)));
}

#[test]
fn a_migration_hash_mismatch_fails_closed() {
    let database = TestDatabase::new("migration-hash");
    let mut store = migrated_store(&database);
    store
        .with_immediate_transaction(|tx| {
            tx.execute(
                "UPDATE schema_migrations SET sha256 = ?1 WHERE version = ?2",
                params!["e".repeat(64), 1_i64],
            )
            .expect("tamper migration hash");
            Ok(())
        })
        .expect("commit hash tamper");

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
fn read_only_verification_has_no_mutation_capability() {
    let database = TestDatabase::new("read-only");
    let store = migrated_store(&database);
    drop(store);

    let mut read_only = Store::open_read_only(&database.path).expect("open read-only store");
    assert_eq!(read_only.schema_version().expect("read schema version"), 1);
    assert!(
        read_only
            .verify_integrity()
            .expect("verify read-only")
            .is_clean()
    );
    assert!(matches!(
        read_only.with_immediate_transaction(|_| Ok(())),
        Err(HeleosError::PolicyDenied)
    ));
}

#[test]
fn integrity_quick_and_foreign_key_checks_report_clean() {
    let database = TestDatabase::new("integrity");
    let store = migrated_store(&database);
    let report = store.verify_integrity().expect("verify integrity");

    assert!(report.integrity_check_violations.is_empty());
    assert!(report.quick_check_violations.is_empty());
    assert!(report.foreign_key_violations.is_empty());
    assert!(report.is_clean());
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
fn symlink_and_non_regular_database_or_lock_paths_are_rejected() {
    use std::ffi::OsString;
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

    let directory_database = database.root.join("directory.sqlite3");
    fs::create_dir(&directory_database).expect("create non-regular database path");
    assert!(matches!(
        Store::open_writer(&directory_database),
        Err(HeleosError::PolicyDenied)
    ));

    let mut lock_name = OsString::from(database.path.as_os_str());
    lock_name.push(".writer.lock");
    let lock_path = PathBuf::from(lock_name);
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
    let defensive = store
        .with_immediate_transaction(|tx| {
            Ok(tx
                .db_config(DbConfig::SQLITE_DBCONFIG_DEFENSIVE)
                .expect("query defensive mode"))
        })
        .expect("inspect in-memory settings");
    assert!(defensive);
}
