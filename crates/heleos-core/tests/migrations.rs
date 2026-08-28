#![forbid(unsafe_code)]

use std::env;
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use heleos_core::{
    HeleosError, INTEGRITY_VIOLATION_LIMIT, Store, apply_private_permissions,
    verify_private_permissions,
};
use rusqlite::config::DbConfig;
use rusqlite::{Connection, OpenFlags, params};
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
            let busy_timeout = tx
                .pragma_query_value(None, "busy_timeout", |row| row.get::<_, i64>(0))
                .expect("query busy timeout");
            let query_only = tx
                .pragma_query_value(None, "query_only", |row| row.get::<_, i64>(0))
                .expect("query writer query_only");
            let temp_store = tx
                .pragma_query_value(None, "temp_store", |row| row.get::<_, i64>(0))
                .expect("query temp store");
            let trusted_schema = tx
                .db_config(DbConfig::SQLITE_DBCONFIG_TRUSTED_SCHEMA)
                .expect("query trusted schema");
            let defensive = tx
                .db_config(DbConfig::SQLITE_DBCONFIG_DEFENSIVE)
                .expect("query defensive mode");
            let dqs_ddl = tx
                .db_config(DbConfig::SQLITE_DBCONFIG_DQS_DDL)
                .expect("query DQS DDL mode");
            let dqs_dml = tx
                .db_config(DbConfig::SQLITE_DBCONFIG_DQS_DML)
                .expect("query DQS DML mode");
            let attach_create = tx
                .db_config(DbConfig::SQLITE_DBCONFIG_ENABLE_ATTACH_CREATE)
                .expect("query attach create mode");
            let attach_write = tx
                .db_config(DbConfig::SQLITE_DBCONFIG_ENABLE_ATTACH_WRITE)
                .expect("query attach write mode");
            Ok((
                foreign_keys,
                journal_mode,
                synchronous,
                busy_timeout,
                query_only,
                temp_store,
                trusted_schema,
                defensive,
                dqs_ddl,
                dqs_dml,
                attach_create,
                attach_write,
            ))
        })
        .expect("inspect settings");

    assert_eq!(
        settings,
        (
            1,
            "wal".to_owned(),
            2,
            5_000,
            0,
            2,
            false,
            true,
            false,
            false,
            false,
            false,
        )
    );
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
fn declared_uniqueness_foreign_keys_and_delete_actions_are_independently_enforced() {
    let database = TestDatabase::new("constraint-matrix");
    let mut store = migrated_store(&database);
    insert_complete_fixture(&mut store, "collision");

    store
        .with_immediate_transaction(|tx| {
            let uniqueness_collisions = [
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
                        (id, project_id, kind, idempotency_key, state, attempt, budget_json,
                         input_json, checkpoint_json, created_at_ms, updated_at_ms)
                     VALUES ('job-2', 'project-1', 'pdf_ingest', 'collision', 'queued', 0,
                        '{}', '{}', '{}', 1, 1)",
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
                     VALUES ('audit-2', 1, 'project-1', 'actor', 'action', 'project',
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
                     VALUES ('audit-3', 2, 'project-1', 'actor', 'action', 'project',
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
    let mut store = migrated_store(&database);

    store
        .with_immediate_transaction(|tx| {
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
                    (id, project_id, kind, idempotency_key, state, attempt, budget_json,
                     input_json, checkpoint_json, created_at_ms, updated_at_ms)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
                params![
                    "jcs-job",
                    "jcs-project",
                    "pdf_ingest",
                    "canonical",
                    "queued",
                    0_i64,
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
                            (id, project_id, kind, idempotency_key, state, attempt, budget_json,
                             input_json, checkpoint_json, created_at_ms, updated_at_ms)
                         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
                        params![
                            id,
                            "jcs-project",
                            "pdf_ingest",
                            key,
                            "queued",
                            0_i64,
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
                        (id, project_id, job_id, outcome, source_name, source_path,
                         idempotency_key, actor, terminal_at_ms, details_json)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
                    params![
                        "bad-details",
                        "jcs-project",
                        "jcs-job",
                        "interrupted",
                        "name",
                        "path",
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
                        "path",
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
                            "action",
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
fn crash_left_wal_process_helper() {
    let Some(database) = env::var_os("HELEOS_TEST_CRASH_DATABASE") else {
        return;
    };
    let mut store = Store::open_writer(database).expect("crash fixture opens writer");
    store.migrate().expect("crash fixture migrates");
    store
        .with_immediate_transaction(|tx| {
            tx.execute(
                "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
                params!["crash-project", "name", 1_i64, "actor", "INTERNAL"],
            )
            .expect("write crash-left WAL row");
            Ok(())
        })
        .expect("commit crash-left WAL row");
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
    assert!(matches!(
        writer.with_immediate_transaction(|tx| {
            tx.execute(
                "INSERT INTO projects (id, name, created_at_ms, created_by, data_class)
                 VALUES (?1, ?2, ?3, ?4, ?5)",
                params!["must-not-write", "name", 1_i64, "actor", "INTERNAL"],
            )
            .expect("operation must never run");
            Ok(())
        }),
        Err(HeleosError::PolicyDenied)
    ));
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
    let mut writer = migrated_store(&database);
    writer
        .with_immediate_transaction(|_| Ok(()))
        .expect("materialize SQLite sidecars");
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

    drop(writer);
    let target = database.root.join("hostile-sidecar-target");
    File::create(&target).expect("create hostile target");
    symlink(&target, &wal).expect("create WAL symlink");
    assert!(matches!(
        Store::open_read_only(&database.path),
        Err(HeleosError::PolicyDenied)
    ));
    fs::remove_file(&wal).expect("remove WAL symlink");

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

    let connection = Connection::open(&database.path).expect("open raw corruption fixture");
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
