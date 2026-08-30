//! Encrypted backup and staged restore contracts.

use std::fs::{self, OpenOptions};
use std::path::{Path, PathBuf};
use std::thread;
use std::time::{Duration, Instant};

use rusqlite::backup::{Backup, StepResult};
use rusqlite::{Connection, OpenFlags, Transaction, TransactionBehavior};

use crate::{
    HeleosError, Result, Store, VaultInventory, apply_private_permissions,
    verify_private_permissions,
};

#[cfg_attr(
    not(test),
    expect(
        dead_code,
        reason = "private container primitive is wired into BackupService::create in the next reviewed slice"
    )
)]
mod archive;
mod manifest;

const DATABASE_SNAPSHOT_BYTES_MAX: u64 = 16 * 1024 * 1024 * 1024;
const DATABASE_SNAPSHOT_PAGES_PER_STEP: i32 = 16;
const DATABASE_SNAPSHOT_BUSY_RETRIES_MAX: u32 = 128;
const DATABASE_SNAPSHOT_BUSY_RETRY_PAUSE: Duration = Duration::from_millis(5);
const DATABASE_SNAPSHOT_BUSY_DEADLINE: Duration = Duration::from_secs(5);

#[derive(Clone, Copy, Debug, Default)]
pub struct BackupService;

#[cfg_attr(
    not(test),
    expect(
        dead_code,
        reason = "owned snapshot is consumed by BackupService::create in the next reviewed slice"
    )
)]
struct DatabaseSnapshot {
    directory: tempfile::TempDir,
    database_path: PathBuf,
    inventory: VaultInventory,
}

#[derive(Clone, Copy)]
struct SnapshotDatabaseBounds {
    page_size: u64,
    maximum_page_count: u64,
}

impl DatabaseSnapshot {
    #[cfg(test)]
    fn root_path(&self) -> &Path {
        self.directory.path()
    }
}

#[cfg(test)]
enum SnapshotStepTestOutcome {
    Failure,
}

#[cfg(test)]
struct SnapshotTestControl<'a> {
    before_inventory: Option<Box<dyn FnMut() + 'a>>,
    after_inventory: Option<Box<dyn FnMut() + 'a>>,
    after_more: Option<Box<dyn FnMut() + 'a>>,
    before_step: Option<Box<dyn FnMut() + 'a>>,
    database_bytes_max: Option<u64>,
    step_outcomes: std::collections::VecDeque<SnapshotStepTestOutcome>,
}

#[cfg(test)]
impl<'a> SnapshotTestControl<'a> {
    fn new(before_inventory: impl FnMut() + 'a, after_inventory: impl FnMut() + 'a) -> Self {
        Self {
            before_inventory: Some(Box::new(before_inventory)),
            after_inventory: Some(Box::new(after_inventory)),
            after_more: None,
            before_step: None,
            database_bytes_max: None,
            step_outcomes: std::collections::VecDeque::new(),
        }
    }

    fn after_more(mut self, callback: impl FnMut() + 'a) -> Self {
        self.after_more = Some(Box::new(callback));
        self
    }

    fn before_step(mut self, callback: impl FnMut() + 'a) -> Self {
        self.before_step = Some(Box::new(callback));
        self
    }

    const fn with_database_bytes_max(mut self, maximum: u64) -> Self {
        self.database_bytes_max = Some(maximum);
        self
    }

    fn step_outcomes(
        mut self,
        outcomes: impl IntoIterator<Item = SnapshotStepTestOutcome>,
    ) -> Self {
        self.step_outcomes.extend(outcomes);
        self
    }

    fn run_before_inventory(&mut self) {
        if let Some(mut callback) = self.before_inventory.take() {
            callback();
        }
    }

    fn run_after_inventory(&mut self) {
        if let Some(mut callback) = self.after_inventory.take() {
            callback();
        }
    }

    fn run_after_more(&mut self) {
        if let Some(mut callback) = self.after_more.take() {
            callback();
        }
    }
}

#[cfg_attr(
    not(test),
    expect(
        dead_code,
        reason = "private primitive is wired into BackupService::create in the next reviewed slice"
    )
)]
fn create_database_snapshot(store: &mut Store, staging_parent: &Path) -> Result<DatabaseSnapshot> {
    create_database_snapshot_inner(
        store,
        staging_parent,
        #[cfg(test)]
        None,
    )
}

#[cfg(test)]
fn create_database_snapshot_with_test_control(
    store: &mut Store,
    staging_parent: &Path,
    control: &mut SnapshotTestControl<'_>,
) -> Result<DatabaseSnapshot> {
    create_database_snapshot_inner(store, staging_parent, Some(control))
}

fn create_database_snapshot_inner(
    store: &mut Store,
    staging_parent: &Path,
    #[cfg(test)] mut control: Option<&mut SnapshotTestControl<'_>>,
) -> Result<DatabaseSnapshot> {
    require_database_snapshot_source(store)?;

    #[cfg(not(test))]
    let database_bytes_max = DATABASE_SNAPSHOT_BYTES_MAX;
    #[cfg(test)]
    let database_bytes_max = control
        .as_deref()
        .and_then(|control| control.database_bytes_max)
        .unwrap_or(DATABASE_SNAPSHOT_BYTES_MAX);

    let canonical_staging_parent = fs::canonicalize(staging_parent).map_err(HeleosError::Io)?;
    if canonical_staging_parent != staging_parent {
        return Err(HeleosError::PolicyDenied);
    }
    verify_private_permissions(&canonical_staging_parent)?;
    if !fs::symlink_metadata(&canonical_staging_parent)
        .map_err(HeleosError::Io)?
        .is_dir()
    {
        return Err(HeleosError::PolicyDenied);
    }

    let directory = tempfile::Builder::new()
        .prefix(".heleos-backup-database-")
        .tempdir_in(&canonical_staging_parent)
        .map_err(HeleosError::Io)?;
    apply_private_permissions(directory.path())?;
    verify_private_permissions(directory.path())?;

    let database_path = directory.path().join("snapshot.sqlite3");
    create_private_staging_file(&database_path)?;
    let mut destination = open_staging_database(&database_path)?;

    let transaction = Transaction::new_unchecked(&store.connection, TransactionBehavior::Deferred)
        .map_err(|_| HeleosError::Database)?;
    #[cfg(test)]
    if let Some(control) = control.as_deref_mut() {
        control.run_before_inventory();
    }

    let snapshot_result = match store.vault_inventory_rows() {
        Ok(inventory) => {
            #[cfg(test)]
            if let Some(control) = control.as_deref_mut() {
                control.run_after_inventory();
            }

            match snapshot_database_bounds(&transaction, database_bytes_max)
                .and_then(|bounds| configure_staging_database(&destination, bounds))
            {
                Ok(()) => match Backup::new(&transaction, &mut destination) {
                    Ok(backup) => {
                        let result = step_database_snapshot(
                            &backup,
                            #[cfg(test)]
                            control,
                        );
                        drop(backup);
                        result.map(|()| inventory)
                    }
                    Err(error) => Err(map_backup_sqlite_error(error)),
                },
                Err(error) => Err(error),
            }
        }
        Err(error) => Err(error),
    };

    let transaction_result = transaction.finish().map_err(|_| HeleosError::Database);
    let destination_result = destination.close().map_err(|_| HeleosError::Database);
    let inventory =
        combine_snapshot_teardown_results(snapshot_result, transaction_result, destination_result)?;

    require_database_snapshot_source(store)?;
    validate_staged_database(directory.path(), &database_path, database_bytes_max)?;

    Ok(DatabaseSnapshot {
        directory,
        database_path,
        inventory,
    })
}

fn require_database_snapshot_source(store: &Store) -> Result<()> {
    if store.writer_lock().is_none() {
        return Err(HeleosError::PolicyDenied);
    }
    store.require_writer_capability()
}

fn create_private_staging_file(path: &Path) -> Result<()> {
    let mut options = OpenOptions::new();
    options.read(true).write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;

        options.mode(0o600);
    }
    let mut file = options.open(path).map_err(HeleosError::Io)?;
    crate::store::apply_private_permissions_to_handle(&mut file)?;
    file.sync_all().map_err(HeleosError::Io)
}

fn open_staging_database(path: &Path) -> Result<Connection> {
    let flags = OpenFlags::SQLITE_OPEN_READ_WRITE
        | OpenFlags::SQLITE_OPEN_NO_MUTEX
        | OpenFlags::SQLITE_OPEN_NOFOLLOW
        | OpenFlags::SQLITE_OPEN_PRIVATE_CACHE;
    let connection = Connection::open_with_flags(path, flags).map_err(|_| HeleosError::Database)?;
    connection
        .busy_timeout(DATABASE_SNAPSHOT_BUSY_DEADLINE)
        .map_err(|_| HeleosError::Database)?;
    connection
        .pragma_update(None, "synchronous", "FULL")
        .map_err(|_| HeleosError::Database)?;
    Ok(connection)
}

fn snapshot_database_bounds(
    transaction: &Transaction<'_>,
    database_bytes_max: u64,
) -> Result<SnapshotDatabaseBounds> {
    let page_size = transaction
        .pragma_query_value(Some("main"), "page_size", |row| row.get::<_, i64>(0))
        .map_err(|_| HeleosError::Database)?;
    let page_size = validate_sqlite_page_size(page_size)?;
    let page_count = transaction
        .pragma_query_value(Some("main"), "page_count", |row| row.get::<_, i64>(0))
        .map_err(|_| HeleosError::Database)?;
    let page_count = u64::try_from(page_count).map_err(|_| HeleosError::Database)?;
    let logical_bytes = page_size
        .checked_mul(page_count)
        .ok_or(HeleosError::ResourceLimit)?;
    if logical_bytes > database_bytes_max {
        return Err(HeleosError::ResourceLimit);
    }
    let maximum_page_count = database_bytes_max
        .checked_div(page_size)
        .filter(|page_count| *page_count > 0)
        .ok_or(HeleosError::ResourceLimit)?;

    Ok(SnapshotDatabaseBounds {
        page_size,
        maximum_page_count,
    })
}

fn validate_sqlite_page_size(page_size: i64) -> Result<u64> {
    let page_size = u64::try_from(page_size).map_err(|_| HeleosError::Database)?;
    if !(512..=65_536).contains(&page_size) || !page_size.is_power_of_two() {
        return Err(HeleosError::Database);
    }
    Ok(page_size)
}

fn configure_staging_database(
    connection: &Connection,
    bounds: SnapshotDatabaseBounds,
) -> Result<()> {
    let page_size = i64::try_from(bounds.page_size).map_err(|_| HeleosError::ResourceLimit)?;
    connection
        .pragma_update(Some("main"), "page_size", page_size)
        .map_err(|_| HeleosError::Database)?;
    let configured_page_size = connection
        .pragma_query_value(Some("main"), "page_size", |row| row.get::<_, i64>(0))
        .map_err(|_| HeleosError::Database)?;
    if validate_sqlite_page_size(configured_page_size)? != bounds.page_size {
        return Err(HeleosError::Database);
    }

    let maximum_page_count =
        i64::try_from(bounds.maximum_page_count).map_err(|_| HeleosError::ResourceLimit)?;
    connection
        .pragma_update(Some("main"), "max_page_count", maximum_page_count)
        .map_err(|_| HeleosError::Database)?;
    let configured_maximum = connection
        .pragma_query_value(Some("main"), "max_page_count", |row| row.get::<_, i64>(0))
        .map_err(|_| HeleosError::Database)?;
    let configured_maximum =
        u64::try_from(configured_maximum).map_err(|_| HeleosError::Database)?;
    if configured_maximum == 0 || configured_maximum != bounds.maximum_page_count {
        return Err(HeleosError::ResourceLimit);
    }
    Ok(())
}

fn step_database_snapshot(
    backup: &Backup<'_, '_>,
    #[cfg(test)] mut control: Option<&mut SnapshotTestControl<'_>>,
) -> Result<()> {
    let mut transient_retries = 0_u32;
    let mut transient_deadline = None;

    loop {
        let outcome = next_database_snapshot_step(
            backup,
            #[cfg(test)]
            control.as_deref_mut(),
        )?;
        match outcome {
            StepResult::Done => return Ok(()),
            StepResult::More =>
            {
                #[cfg(test)]
                if let Some(control) = control.as_deref_mut() {
                    control.run_after_more();
                }
            }
            StepResult::Busy | StepResult::Locked => {
                transient_retries = transient_retries
                    .checked_add(1)
                    .ok_or(HeleosError::ResourceLimit)?;
                if transient_retries > DATABASE_SNAPSHOT_BUSY_RETRIES_MAX {
                    return Err(HeleosError::Timeout);
                }
                let deadline = match transient_deadline {
                    Some(deadline) => deadline,
                    None => {
                        let deadline = Instant::now()
                            .checked_add(DATABASE_SNAPSHOT_BUSY_DEADLINE)
                            .ok_or(HeleosError::ResourceLimit)?;
                        transient_deadline = Some(deadline);
                        deadline
                    }
                };
                if Instant::now() >= deadline {
                    return Err(HeleosError::Timeout);
                }
                thread::sleep(DATABASE_SNAPSHOT_BUSY_RETRY_PAUSE);
            }
            _ => return Err(HeleosError::Database),
        }
    }
}

#[cfg(not(test))]
fn next_database_snapshot_step(backup: &Backup<'_, '_>) -> Result<StepResult> {
    backup
        .step(DATABASE_SNAPSHOT_PAGES_PER_STEP)
        .map_err(map_backup_sqlite_error)
}

#[cfg(test)]
fn next_database_snapshot_step(
    backup: &Backup<'_, '_>,
    control: Option<&mut SnapshotTestControl<'_>>,
) -> Result<StepResult> {
    let injected = control.and_then(|control| {
        if let Some(callback) = control.before_step.as_mut() {
            callback();
        }
        control.step_outcomes.pop_front()
    });
    match injected {
        Some(SnapshotStepTestOutcome::Failure) => Err(HeleosError::Database),
        None => backup
            .step(DATABASE_SNAPSHOT_PAGES_PER_STEP)
            .map_err(map_backup_sqlite_error),
    }
}

fn map_backup_sqlite_error(error: rusqlite::Error) -> HeleosError {
    match error {
        rusqlite::Error::SqliteFailure(sqlite, _)
            if sqlite.code == rusqlite::ffi::ErrorCode::DiskFull =>
        {
            HeleosError::ResourceLimit
        }
        _ => HeleosError::Database,
    }
}

fn combine_snapshot_teardown_results<T>(
    operation: Result<T>,
    transaction: Result<()>,
    destination: Result<()>,
) -> Result<T> {
    transaction?;
    destination?;
    operation
}

fn validate_staged_database(
    root: &Path,
    database_path: &Path,
    database_bytes_max: u64,
) -> Result<()> {
    let mut entries = fs::read_dir(root).map_err(HeleosError::Io)?;
    let entry = entries
        .next()
        .transpose()
        .map_err(HeleosError::Io)?
        .ok_or(HeleosError::Integrity)?;
    if entry.path() != database_path
        || entries
            .next()
            .transpose()
            .map_err(HeleosError::Io)?
            .is_some()
    {
        return Err(HeleosError::Integrity);
    }
    verify_private_permissions(database_path)?;
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .open(database_path)
        .map_err(HeleosError::Io)?;
    let length = file.metadata().map_err(HeleosError::Io)?.len();
    if length > database_bytes_max {
        return Err(HeleosError::ResourceLimit);
    }
    file.sync_all().map_err(HeleosError::Io)
}

#[cfg(test)]
mod tests {
    use std::cell::Cell;
    use std::fs;
    use std::io::{Cursor, Read};
    use std::path::{Path, PathBuf};
    use std::rc::Rc;
    use std::str::FromStr;

    use ed25519_dalek::{Signature, SigningKey};
    use rusqlite::functions::FunctionFlags;
    use rusqlite::{Connection, params};

    use super::archive::{
        BackupEntryRecord, BackupPayload, BackupStagingTestFault, encode_entry_table,
        encrypt_age_stream, parse_entry_table, parse_plaintext_container_header,
        stage_encrypted_backup, stage_encrypted_backup_with_test_fault, validate_entry_table_claim,
        validate_plaintext_container_length, verify_plaintext_container, write_plaintext_container,
    };
    use super::manifest::{
        BackupDatabaseDescriptorV1, BackupManifestV1, sign_manifest, verify_manifest_signature,
    };
    use super::{
        SnapshotStepTestOutcome, SnapshotTestControl, create_database_snapshot,
        create_database_snapshot_with_test_control,
    };
    use crate::{
        ActorId, Clock, DataClass, HeleosError, IdGenerator, ProjectCreateRequest, ProjectId,
        ProjectService, Sha256Digest, Store, Vault, apply_private_permissions,
    };

    struct TestClock;

    impl Clock for TestClock {
        fn now_unix_ms(&self) -> i64 {
            1_700_000_000_000
        }
    }

    struct TestIds(Cell<u128>);

    impl TestIds {
        const fn new(first: u128) -> Self {
            Self(Cell::new(first))
        }
    }

    impl IdGenerator for TestIds {
        fn next_uuid(&self) -> uuid::Uuid {
            let value = self.0.get();
            self.0.set(value.checked_add(1).expect("bounded test IDs"));
            uuid::Uuid::from_u128(value)
        }
    }

    struct TestDatabase {
        _root: tempfile::TempDir,
        root: PathBuf,
        database: PathBuf,
        staging_parent: PathBuf,
    }

    impl TestDatabase {
        fn new() -> Self {
            let root = tempfile::tempdir().expect("create database test root");
            apply_private_permissions(root.path()).expect("harden database test root");
            let canonical_root = fs::canonicalize(root.path()).expect("canonicalize test root");
            let staging_parent = canonical_root.join("staging");
            fs::create_dir(&staging_parent).expect("create staging parent");
            apply_private_permissions(&staging_parent).expect("harden staging parent");
            Self {
                _root: root,
                database: canonical_root.join("foundation.sqlite3"),
                root: canonical_root,
                staging_parent,
            }
        }

        fn migrated_store(&self) -> Store {
            let mut store = Store::open_writer(&self.database).expect("open database writer");
            store.migrate().expect("migrate database");
            store
        }

        fn migrated_store_with_page_size(&self, page_size: i64) -> Store {
            let connection = Connection::open(&self.database).expect("create page-size fixture");
            connection
                .pragma_update(None, "page_size", page_size)
                .expect("set fixture page size");
            connection
                .execute_batch("VACUUM")
                .expect("persist fixture page size");
            let observed = connection
                .pragma_query_value(None, "page_size", |row| row.get::<_, i64>(0))
                .expect("read fixture page size");
            assert_eq!(observed, page_size);
            drop(connection);
            self.migrated_store()
        }

        fn raw_wal_connection(&self) -> Connection {
            let connection = Connection::open(&self.database).expect("open raw WAL connection");
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
                .expect("register raw JCS validator");
            connection
                .create_scalar_function("heleos_is_uuid", 1, flags, |context| {
                    let text = context.get::<String>(0)?;
                    Ok(uuid::Uuid::parse_str(&text)
                        .is_ok_and(|value| value.hyphenated().to_string() == text))
                })
                .expect("register raw UUID validator");
            connection
                .create_scalar_function("heleos_valid_text", 3, flags, |context| {
                    let text = context.get::<String>(0)?;
                    let max_bytes = context.get::<i64>(1)?;
                    let allow_ordinary_whitespace = context.get::<i64>(2)? != 0;
                    let valid_control = |character: char| {
                        allow_ordinary_whitespace && matches!(character, '\n' | '\r' | '\t')
                    };
                    Ok(max_bytes >= 0
                        && !text.is_empty()
                        && u64::try_from(text.len()).is_ok_and(|length| length <= max_bytes as u64)
                        && !text
                            .chars()
                            .any(|character| character.is_control() && !valid_control(character)))
                })
                .expect("register raw text validator");
            let journal_mode = connection
                .pragma_query_value(Some("main"), "journal_mode", |row| row.get::<_, String>(0))
                .expect("read raw journal mode");
            assert_eq!(journal_mode.to_ascii_lowercase(), "wal");
            connection
        }
    }

    fn insert_content(connection: &Connection, digest: Sha256Digest) {
        connection
            .execute(
                "INSERT INTO content_objects (
                     sha256, byte_length, media_type, admission_state, vault_key,
                     created_at_ms, created_by, quarantine_reason
                 ) VALUES (?1, 1, 'application/pdf', 'accepted', ?2, 1, 'backup-test', NULL)",
                params![digest.to_string(), Vault::object_key(digest)],
            )
            .expect("commit valid raw content row");
    }

    fn content_digests(database: &Path) -> Vec<String> {
        let connection = Connection::open(database).expect("open snapshot verifier");
        let mut statement = connection
            .prepare("SELECT sha256 FROM content_objects ORDER BY sha256")
            .expect("prepare content query");
        statement
            .query_map([], |row| row.get::<_, String>(0))
            .expect("query content rows")
            .collect::<rusqlite::Result<Vec<_>>>()
            .expect("collect content rows")
    }

    fn indexed_digest(index: u32) -> Sha256Digest {
        let mut bytes = [0_u8; 32];
        bytes[..4].copy_from_slice(&index.to_be_bytes());
        Sha256Digest::from_bytes(bytes)
    }

    fn create_audited_project(store: &mut Store, ids: &TestIds, name: &str) {
        ProjectService::new(store, &TestClock, ids)
            .expect("reuse store for project service")
            .create(ProjectCreateRequest {
                project_id: Some(ProjectId::from_uuid(ids.next_uuid())),
                name: name.to_owned(),
                actor: ActorId::from_str("backup-test").expect("test actor"),
                data_class: DataClass::Internal,
            })
            .expect("commit audited project after snapshot teardown");
    }

    struct PlaintextContainerFixture {
        bytes: Vec<u8>,
        signer: [u8; 32],
        signature_offset: usize,
        table_length_offset: usize,
        payload_offset: usize,
    }

    fn database_only_plaintext_container(payload: &[u8]) -> PlaintextContainerFixture {
        let signing_key = SigningKey::from_bytes(&[0x42; 32]);
        let signer = signing_key.verifying_key().to_bytes();
        let database_digest = Sha256Digest::hash_reader(payload).expect("hash fixture database");
        let records = [BackupEntryRecord::database(
            database_digest,
            u64::try_from(payload.len()).expect("bounded fixture database"),
        )
        .expect("construct fixture database record")];
        let table = encode_entry_table(&records).expect("encode fixture table");
        let manifest = BackupManifestV1::new(
            Sha256Digest::hash_reader(signer.as_slice()).expect("hash fixture signer"),
            Sha256Digest::hash_reader(table.as_slice()).expect("hash fixture table"),
            u64::try_from(table.len()).expect("bounded fixture table"),
            1,
            u64::try_from(payload.len()).expect("bounded fixture payload"),
            BackupDatabaseDescriptorV1::new(
                database_digest,
                u64::try_from(payload.len()).expect("bounded fixture database"),
            )
            .expect("construct fixture database descriptor"),
        )
        .expect("construct fixture manifest");
        let manifest_bytes = manifest.canonical_bytes().expect("encode fixture manifest");
        let signature =
            sign_manifest(&signing_key, &manifest_bytes).expect("sign fixture manifest");

        let mut bytes = Vec::new();
        bytes.extend_from_slice(b"HELEOSB1");
        bytes.extend_from_slice(
            &u64::try_from(manifest_bytes.len())
                .expect("bounded fixture manifest")
                .to_be_bytes(),
        );
        bytes.extend_from_slice(&manifest_bytes);
        bytes.extend_from_slice(&signer);
        let signature_offset = bytes.len();
        bytes.extend_from_slice(&signature);
        let table_length_offset = bytes.len();
        bytes.extend_from_slice(
            &u64::try_from(table.len())
                .expect("bounded fixture table")
                .to_be_bytes(),
        );
        bytes.extend_from_slice(&table);
        let payload_offset = bytes.len();
        bytes.extend_from_slice(payload);

        PlaintextContainerFixture {
            bytes,
            signer,
            signature_offset,
            table_length_offset,
            payload_offset,
        }
    }

    #[test]
    fn backup_manifest_has_one_exact_canonical_jcs_encoding() {
        let manifest = BackupManifestV1::new(
            Sha256Digest::from_bytes([0x11; 32]),
            Sha256Digest::from_bytes([0x22; 32]),
            41,
            1,
            3,
            BackupDatabaseDescriptorV1::new(Sha256Digest::from_bytes([0x33; 32]), 3)
                .expect("construct database descriptor"),
        )
        .expect("construct backup manifest");
        let bytes = manifest.canonical_bytes().expect("encode backup manifest");

        assert_eq!(
            bytes,
            br#"{"database":{"byte_length":3,"sha256":"3333333333333333333333333333333333333333333333333333333333333333"},"decoded_payload_byte_length":3,"entry_count":1,"entry_table_byte_length":41,"entry_table_sha256":"2222222222222222222222222222222222222222222222222222222222222222","schema":"heleos.backup-manifest/v1","signer_key_sha256":"1111111111111111111111111111111111111111111111111111111111111111"}"#
        );
        assert_eq!(
            BackupManifestV1::parse_canonical(&bytes).expect("parse canonical backup manifest"),
            manifest
        );
    }

    #[test]
    fn backup_manifest_parser_rejects_noncanonical_and_unknown_fields() {
        let noncanonical = br#" {"database":{"byte_length":3,"sha256":"3333333333333333333333333333333333333333333333333333333333333333"},"decoded_payload_byte_length":3,"entry_count":1,"entry_table_byte_length":41,"entry_table_sha256":"2222222222222222222222222222222222222222222222222222222222222222","schema":"heleos.backup-manifest/v1","signer_key_sha256":"1111111111111111111111111111111111111111111111111111111111111111"}"#;
        let unknown = br#"{"database":{"byte_length":3,"sha256":"3333333333333333333333333333333333333333333333333333333333333333"},"decoded_payload_byte_length":3,"entry_count":1,"entry_table_byte_length":41,"entry_table_sha256":"2222222222222222222222222222222222222222222222222222222222222222","schema":"heleos.backup-manifest/v1","signer_key_sha256":"1111111111111111111111111111111111111111111111111111111111111111","unknown":0}"#;

        for bytes in [noncanonical.as_slice(), unknown.as_slice()] {
            assert!(matches!(
                BackupManifestV1::parse_canonical(bytes),
                Err(HeleosError::InvalidBackupContainer)
            ));
        }
    }

    #[test]
    fn database_only_manifest_rejects_unaccounted_payload_bytes() {
        let bytes = br#"{"database":{"byte_length":3,"sha256":"3333333333333333333333333333333333333333333333333333333333333333"},"decoded_payload_byte_length":4,"entry_count":1,"entry_table_byte_length":41,"entry_table_sha256":"2222222222222222222222222222222222222222222222222222222222222222","schema":"heleos.backup-manifest/v1","signer_key_sha256":"1111111111111111111111111111111111111111111111111111111111111111"}"#;

        assert!(matches!(
            BackupManifestV1::parse_canonical(bytes),
            Err(HeleosError::InvalidBackupContainer)
        ));
    }

    #[test]
    fn manifest_signature_is_strictly_bound_to_the_exact_backup_domain() {
        let signing_key = SigningKey::from_bytes(&[0x42; 32]);
        let manifest = BackupManifestV1::new(
            Sha256Digest::hash_reader(signing_key.verifying_key().as_bytes().as_slice())
                .expect("hash signer key"),
            Sha256Digest::from_bytes([0x22; 32]),
            41,
            1,
            3,
            BackupDatabaseDescriptorV1::new(Sha256Digest::from_bytes([0x33; 32]), 3)
                .expect("construct database descriptor"),
        )
        .expect("construct signed manifest");
        let manifest_bytes = manifest.canonical_bytes().expect("encode signed manifest");
        let signature = sign_manifest(&signing_key, &manifest_bytes).expect("sign manifest");

        verify_manifest_signature(
            signing_key.verifying_key().as_bytes(),
            &manifest_bytes,
            &signature,
        )
        .expect("strictly verify manifest");
        let signature_value = Signature::from_bytes(&signature);
        let mut exact_domain_message = b"heleos-backup-v1\0".to_vec();
        exact_domain_message.extend_from_slice(&manifest_bytes);
        assert!(
            signing_key
                .verifying_key()
                .verify_strict(&exact_domain_message, &signature_value)
                .is_ok()
        );
        assert!(
            signing_key
                .verifying_key()
                .verify_strict(&manifest_bytes, &signature_value)
                .is_err()
        );

        let mut corrupted = signature;
        corrupted[0] ^= 1;
        assert!(matches!(
            verify_manifest_signature(
                signing_key.verifying_key().as_bytes(),
                &manifest_bytes,
                &corrupted,
            ),
            Err(HeleosError::BackupIntegrity)
        ));
    }

    #[test]
    fn entry_table_has_exact_41_byte_big_endian_golden_records() {
        let records = vec![
            BackupEntryRecord::database(Sha256Digest::from_bytes([0x10; 32]), 3)
                .expect("construct database record"),
            BackupEntryRecord::blob(Sha256Digest::from_bytes([0x20; 32]), 1)
                .expect("construct first blob record"),
            BackupEntryRecord::blob(Sha256Digest::from_bytes([0x30; 32]), 2)
                .expect("construct second blob record"),
        ];
        let encoded = encode_entry_table(&records).expect("encode entry table");
        let mut expected = Vec::new();
        expected.push(0);
        expected.extend_from_slice(&[0x10; 32]);
        expected.extend_from_slice(&3_u64.to_be_bytes());
        expected.push(1);
        expected.extend_from_slice(&[0x20; 32]);
        expected.extend_from_slice(&1_u64.to_be_bytes());
        expected.push(1);
        expected.extend_from_slice(&[0x30; 32]);
        expected.extend_from_slice(&2_u64.to_be_bytes());

        assert_eq!(encoded, expected);
        let parsed = parse_entry_table(&encoded).expect("parse entry table");
        assert_eq!(parsed.records(), records.as_slice());
        assert_eq!(parsed.decoded_payload_byte_length(), 6);
    }

    #[test]
    fn entry_table_claims_reject_caps_overflow_and_length_mismatch_without_allocation() {
        assert!(matches!(
            validate_entry_table_claim(2_000_002, 82_000_082),
            Err(HeleosError::ResourceLimit)
        ));
        assert!(matches!(
            validate_entry_table_claim(1, 80 * 1024 * 1024 + 1),
            Err(HeleosError::ResourceLimit)
        ));
        assert!(matches!(
            validate_entry_table_claim(u64::MAX, u64::MAX),
            Err(HeleosError::ResourceLimit)
        ));
        assert!(matches!(
            validate_entry_table_claim(1, 40),
            Err(HeleosError::InvalidBackupContainer)
        ));
        assert_eq!(
            validate_entry_table_claim(2_000_001, 82_000_041)
                .expect("accept exact maximum record count"),
            82_000_041
        );

        let plaintext_max = 520_u64 * 1024 * 1024 * 1024;
        let manifest_max = 16_u64 * 1024 * 1024;
        let table_max = 80_u64 * 1024 * 1024;
        let fixed_framing = 120_u64;
        let payload_at_limit = plaintext_max
            .checked_sub(manifest_max + table_max + fixed_framing)
            .expect("bounded exact payload maximum");
        assert_eq!(
            validate_plaintext_container_length(manifest_max, table_max, payload_at_limit)
                .expect("accept exact plaintext-container cap"),
            plaintext_max
        );
        assert!(matches!(
            validate_plaintext_container_length(manifest_max, table_max, payload_at_limit + 1),
            Err(HeleosError::ResourceLimit)
        ));
        assert!(matches!(
            validate_plaintext_container_length(u64::MAX, 1, 1),
            Err(HeleosError::ResourceLimit)
        ));
    }

    #[test]
    fn entry_table_parser_rejects_unknown_kinds_duplicate_database_and_unsorted_blobs() {
        let record = |kind: u8, digest: u8, byte_length: u64| {
            let mut bytes = Vec::with_capacity(41);
            bytes.push(kind);
            bytes.extend_from_slice(&[digest; 32]);
            bytes.extend_from_slice(&byte_length.to_be_bytes());
            bytes
        };
        let database = record(0, 0x10, 3);
        let first_blob = record(1, 0x20, 1);
        let earlier_blob = record(1, 0x19, 1);

        let mut unknown_kind = database.clone();
        unknown_kind[0] = 2;
        let mut duplicate_database = database.clone();
        duplicate_database.extend_from_slice(&database);
        let mut descending = database.clone();
        descending.extend_from_slice(&first_blob);
        descending.extend_from_slice(&earlier_blob);
        let mut duplicate_blob = database.clone();
        duplicate_blob.extend_from_slice(&first_blob);
        duplicate_blob.extend_from_slice(&first_blob);

        for bytes in [
            unknown_kind,
            first_blob,
            duplicate_database,
            descending,
            duplicate_blob,
            vec![0; 40],
        ] {
            assert!(matches!(
                parse_entry_table(&bytes),
                Err(HeleosError::InvalidBackupContainer)
            ));
        }
        assert!(matches!(
            BackupEntryRecord::database(
                Sha256Digest::from_bytes([0x10; 32]),
                16 * 1024 * 1024 * 1024 + 1,
            ),
            Err(HeleosError::ResourceLimit)
        ));
        assert!(matches!(
            BackupEntryRecord::blob(Sha256Digest::from_bytes([0x20; 32]), 256 * 1024 * 1024 + 1,),
            Err(HeleosError::ResourceLimit)
        ));
    }

    #[test]
    fn plaintext_header_checks_trusted_signer_and_signature_before_table_claims() {
        let fixture = database_only_plaintext_container(b"db!");
        let parsed = parse_plaintext_container_header(
            Cursor::new(fixture.bytes.as_slice()),
            &fixture.signer,
        )
        .expect("parse valid plaintext header");
        assert_eq!(parsed.entry_table().records().len(), 1);

        let mut oversized_table = fixture.bytes.clone();
        oversized_table[fixture.table_length_offset..fixture.table_length_offset + 8]
            .copy_from_slice(&u64::MAX.to_be_bytes());
        let wrong_trusted_signer = [0x99; 32];
        assert!(matches!(
            parse_plaintext_container_header(
                Cursor::new(oversized_table.as_slice()),
                &wrong_trusted_signer,
            ),
            Err(HeleosError::BackupIntegrity)
        ));
        assert!(matches!(
            parse_plaintext_container_header(
                Cursor::new(oversized_table.as_slice()),
                &fixture.signer,
            ),
            Err(HeleosError::ResourceLimit)
        ));

        let mut bad_signature_and_table = oversized_table;
        bad_signature_and_table[fixture.signature_offset] ^= 1;
        assert!(matches!(
            parse_plaintext_container_header(
                Cursor::new(bad_signature_and_table.as_slice()),
                &fixture.signer,
            ),
            Err(HeleosError::BackupIntegrity)
        ));

        let mut altered_table = fixture.bytes.clone();
        altered_table[fixture.table_length_offset + 8 + 1] ^= 1;
        assert!(matches!(
            parse_plaintext_container_header(
                Cursor::new(altered_table.as_slice()),
                &fixture.signer,
            ),
            Err(HeleosError::BackupIntegrity)
        ));
    }

    #[test]
    fn plaintext_total_cap_is_checked_from_the_signed_manifest_before_table_io() {
        let signing_key = SigningKey::from_bytes(&[0x42; 32]);
        let signer = signing_key.verifying_key().to_bytes();
        let manifest = BackupManifestV1::new(
            Sha256Digest::hash_reader(signer.as_slice()).expect("hash signer"),
            Sha256Digest::from_bytes([0x22; 32]),
            82,
            2,
            520_u64 * 1024 * 1024 * 1024,
            BackupDatabaseDescriptorV1::new(Sha256Digest::from_bytes([0x33; 32]), 0)
                .expect("construct bounded database descriptor"),
        )
        .expect("construct individually bounded hostile manifest");
        let manifest_bytes = manifest.canonical_bytes().expect("encode hostile manifest");
        let signature =
            sign_manifest(&signing_key, &manifest_bytes).expect("sign hostile manifest");
        let mut bytes = Vec::new();
        bytes.extend_from_slice(b"HELEOSB1");
        bytes.extend_from_slice(
            &u64::try_from(manifest_bytes.len())
                .expect("bounded manifest")
                .to_be_bytes(),
        );
        bytes.extend_from_slice(&manifest_bytes);
        bytes.extend_from_slice(&signer);
        bytes.extend_from_slice(&signature);

        assert!(matches!(
            parse_plaintext_container_header(Cursor::new(bytes), &signer),
            Err(HeleosError::ResourceLimit)
        ));
    }

    #[test]
    fn plaintext_payload_verifier_rejects_short_long_hash_mismatch_and_trailing_bytes() {
        let fixture = database_only_plaintext_container(b"database payload");
        verify_plaintext_container(Cursor::new(fixture.bytes.as_slice()), &fixture.signer)
            .expect("verify exact plaintext container");

        let mut short = fixture.bytes.clone();
        short.pop();
        assert!(matches!(
            verify_plaintext_container(Cursor::new(short.as_slice()), &fixture.signer),
            Err(HeleosError::InvalidBackupContainer)
        ));

        let mut hash_mismatch = fixture.bytes.clone();
        hash_mismatch[fixture.payload_offset] ^= 1;
        assert!(matches!(
            verify_plaintext_container(Cursor::new(hash_mismatch.as_slice()), &fixture.signer),
            Err(HeleosError::BackupIntegrity)
        ));

        let mut trailing = fixture.bytes.clone();
        trailing.push(0);
        assert!(matches!(
            verify_plaintext_container(Cursor::new(trailing.as_slice()), &fixture.signer),
            Err(HeleosError::InvalidBackupContainer)
        ));
    }

    #[test]
    fn plaintext_writer_matches_the_exact_database_only_golden_container() {
        let expected = database_only_plaintext_container(b"database payload");
        let signing_key = SigningKey::from_bytes(&[0x42; 32]);
        let mut database = Cursor::new(b"database payload".as_slice());
        let database_digest =
            Sha256Digest::hash_reader(b"database payload".as_slice()).expect("hash database");
        let mut payloads = [BackupPayload::new(
            BackupEntryRecord::database(database_digest, 16)
                .expect("construct database payload record"),
            &mut database,
        )];
        let mut actual = Vec::new();

        write_plaintext_container(&mut actual, &signing_key, &mut payloads)
            .expect("write plaintext container");

        assert_eq!(actual, expected.bytes);
    }

    #[test]
    fn age_stream_finishes_empty_and_multichunk_ciphertexts() {
        let identity = age::x25519::Identity::generate();
        let recipient = identity.to_public();
        for plaintext in [Vec::new(), vec![0x5a; 192 * 1024 + 17]] {
            let ciphertext = encrypt_age_stream(
                Cursor::new(plaintext.as_slice()),
                Vec::new(),
                &recipient,
                u64::try_from(plaintext.len()).expect("bounded plaintext"),
            )
            .expect("encrypt and finish age stream");
            let decryptor = age::Decryptor::new(ciphertext.as_slice()).expect("parse ciphertext");
            let mut decrypted = decryptor
                .decrypt(std::iter::once(&identity as &dyn age::Identity))
                .expect("decrypt ciphertext");
            let mut actual = Vec::new();
            decrypted
                .read_to_end(&mut actual)
                .expect("reach ciphertext integrity EOF");
            assert_eq!(actual, plaintext);
        }
    }

    #[test]
    fn verified_plaintext_is_encrypted_and_owned_staging_cleans_on_drop() {
        let root = tempfile::tempdir().expect("create encryption staging parent");
        apply_private_permissions(root.path()).expect("harden encryption staging parent");
        let staging_parent = fs::canonicalize(root.path()).expect("canonicalize staging parent");
        let identity = age::x25519::Identity::generate();
        let recipient = identity.to_public();
        let signing_key = SigningKey::from_bytes(&[0x42; 32]);
        let expected = database_only_plaintext_container(b"database payload");
        let mut database = Cursor::new(b"database payload".as_slice());
        let mut payloads = [BackupPayload::new(
            BackupEntryRecord::database(
                Sha256Digest::hash_reader(b"database payload".as_slice()).expect("hash database"),
                16,
            )
            .expect("construct database payload record"),
            &mut database,
        )];

        let staging =
            stage_encrypted_backup(&staging_parent, &signing_key, &recipient, &mut payloads)
                .expect("self-verify and encrypt plaintext staging");
        let entries = fs::read_dir(staging.root_path())
            .expect("read encrypted staging")
            .collect::<std::io::Result<Vec<_>>>()
            .expect("collect encrypted staging");
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].path(), staging.ciphertext_path());

        let ciphertext = fs::read(staging.ciphertext_path()).expect("read staged ciphertext");
        let decryptor =
            age::Decryptor::new(ciphertext.as_slice()).expect("parse staged ciphertext");
        let mut decrypted = decryptor
            .decrypt(std::iter::once(&identity as &dyn age::Identity))
            .expect("decrypt staged ciphertext");
        let mut plaintext = Vec::new();
        decrypted
            .read_to_end(&mut plaintext)
            .expect("reach staged ciphertext EOF");
        assert_eq!(plaintext, expected.bytes);

        drop(staging);
        assert!(
            fs::read_dir(&staging_parent)
                .expect("read emptied staging parent")
                .next()
                .is_none()
        );
    }

    #[test]
    fn staging_cleans_plaintext_and_ciphertext_on_self_verify_or_age_failure() {
        let root = tempfile::tempdir().expect("create failure staging parent");
        apply_private_permissions(root.path()).expect("harden failure staging parent");
        let staging_parent = fs::canonicalize(root.path()).expect("canonicalize staging parent");
        let identity = age::x25519::Identity::generate();
        let recipient = identity.to_public();
        let signing_key = SigningKey::from_bytes(&[0x42; 32]);
        let database_digest =
            Sha256Digest::hash_reader(b"database payload".as_slice()).expect("hash database");

        for (fault, expected_error) in [
            (
                BackupStagingTestFault::CorruptPlaintextBeforeVerification,
                "integrity",
            ),
            (BackupStagingTestFault::CiphertextByteLimit(512), "limit"),
        ] {
            let mut database = Cursor::new(b"database payload".as_slice());
            let mut payloads = [BackupPayload::new(
                BackupEntryRecord::database(database_digest, 16)
                    .expect("construct database payload record"),
                &mut database,
            )];
            let result = stage_encrypted_backup_with_test_fault(
                &staging_parent,
                &signing_key,
                &recipient,
                &mut payloads,
                fault,
            );
            match expected_error {
                "integrity" => assert!(matches!(result, Err(HeleosError::BackupIntegrity))),
                "limit" => match result {
                    Err(HeleosError::ResourceLimit) => {}
                    Err(error) => panic!("unexpected age failure: {error:?}"),
                    Ok(_) => panic!("age failure unexpectedly succeeded"),
                },
                _ => unreachable!("bounded fault matrix"),
            }
            assert!(
                fs::read_dir(&staging_parent)
                    .expect("read cleaned staging parent")
                    .next()
                    .is_none()
            );
        }
    }

    #[test]
    fn snapshot_rejects_in_memory_and_read_only_stores_before_staging() {
        let fixture = TestDatabase::new();
        let writer = fixture.migrated_store();
        let untouched_staging = fixture.root.join("must-not-be-created");
        let mut in_memory = Store::open_in_memory().expect("open in-memory store");

        assert!(matches!(
            create_database_snapshot(&mut in_memory, &untouched_staging),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(!untouched_staging.exists());

        drop(writer);
        let mut read_only = Store::open_read_only(&fixture.database).expect("open read-only store");
        assert!(matches!(
            create_database_snapshot(&mut read_only, &untouched_staging),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(!untouched_staging.exists());
    }

    #[test]
    fn inventory_read_is_the_first_database_read_and_pins_the_deferred_snapshot() {
        let fixture = TestDatabase::new();
        let mut store = fixture.migrated_store();
        let raw = fixture.raw_wal_connection();
        let before_inventory = Sha256Digest::from_bytes([0x11; 32]);
        let after_inventory = Sha256Digest::from_bytes([0x22; 32]);
        let mut control = SnapshotTestControl::new(
            || insert_content(&raw, before_inventory),
            || insert_content(&raw, after_inventory),
        );

        let snapshot = create_database_snapshot_with_test_control(
            &mut store,
            &fixture.staging_parent,
            &mut control,
        )
        .expect("create pinned database snapshot");

        assert_eq!(
            snapshot
                .inventory
                .entries
                .keys()
                .copied()
                .collect::<Vec<_>>(),
            vec![before_inventory]
        );
        assert_eq!(
            content_digests(&snapshot.database_path),
            vec![before_inventory.to_string()]
        );
        assert_eq!(
            content_digests(&fixture.database),
            vec![before_inventory.to_string(), after_inventory.to_string()]
        );

        let snapshot_root = snapshot.root_path().to_owned();
        drop(snapshot);
        assert!(!snapshot_root.exists());
        assert!(fixture.root.exists());
    }

    #[test]
    fn external_commit_after_more_does_not_restart_the_snapshot() {
        let fixture = TestDatabase::new();
        let mut store = fixture.migrated_store();
        let raw = fixture.raw_wal_connection();
        for index in 0..512 {
            insert_content(&raw, indexed_digest(index));
        }
        let late_digest = Sha256Digest::from_bytes([0xff; 32]);
        let observed_more = Rc::new(Cell::new(false));
        let observed_more_in_callback = Rc::clone(&observed_more);
        let mut control = SnapshotTestControl::new(|| {}, || {}).after_more(|| {
            observed_more_in_callback.set(true);
            insert_content(&raw, late_digest);
        });

        let snapshot = create_database_snapshot_with_test_control(
            &mut store,
            &fixture.staging_parent,
            &mut control,
        )
        .expect("create multi-page snapshot");

        assert!(
            observed_more.get(),
            "backup must expose a real More boundary"
        );
        assert!(!snapshot.inventory.entries.contains_key(&late_digest));
        assert_eq!(
            content_digests(&snapshot.database_path),
            snapshot
                .inventory
                .entries
                .keys()
                .map(ToString::to_string)
                .collect::<Vec<_>>()
        );
        assert!(content_digests(&fixture.database).contains(&late_digest.to_string()));
    }

    #[test]
    fn snapshot_success_and_step_failure_release_handles_and_leave_store_reusable() {
        let fixture = TestDatabase::new();
        let mut store = fixture.migrated_store();
        let ids = TestIds::new(100);

        let snapshot = create_database_snapshot(&mut store, &fixture.staging_parent)
            .expect("create reusable-store snapshot");
        let successful_root = snapshot.root_path().to_owned();
        drop(snapshot);
        assert!(!successful_root.exists());
        create_audited_project(&mut store, &ids, "After snapshot success");

        let mut control = SnapshotTestControl::new(|| {}, || {})
            .step_outcomes([SnapshotStepTestOutcome::Failure]);
        assert!(matches!(
            create_database_snapshot_with_test_control(
                &mut store,
                &fixture.staging_parent,
                &mut control,
            ),
            Err(HeleosError::Database)
        ));
        assert_eq!(
            fs::read_dir(&fixture.staging_parent)
                .expect("read staging parent after failed snapshot")
                .count(),
            0
        );
        create_audited_project(&mut store, &ids, "After snapshot failure");
    }

    #[test]
    fn oversized_64k_page_snapshot_is_rejected_before_backup_step() {
        const TEST_DATABASE_BYTES_MAX: u64 = 64 * 1024;

        let fixture = TestDatabase::new();
        let mut store = fixture.migrated_store_with_page_size(65_536);
        let page_size = store
            .connection
            .pragma_query_value(None, "page_size", |row| row.get::<_, i64>(0))
            .expect("read migrated page size");
        let page_count = store
            .connection
            .pragma_query_value(None, "page_count", |row| row.get::<_, i64>(0))
            .expect("read migrated page count");
        assert_eq!(page_size, 65_536);
        assert!(
            u64::try_from(page_size)
                .expect("positive fixture page size")
                .checked_mul(u64::try_from(page_count).expect("nonnegative fixture page count"))
                .expect("bounded fixture database")
                > TEST_DATABASE_BYTES_MAX
        );

        let step_attempts = Rc::new(Cell::new(0_u32));
        let observed_step_attempts = Rc::clone(&step_attempts);
        let mut control = SnapshotTestControl::new(|| {}, || {})
            .with_database_bytes_max(TEST_DATABASE_BYTES_MAX)
            .before_step(move || {
                observed_step_attempts.set(
                    observed_step_attempts
                        .get()
                        .checked_add(1)
                        .expect("bounded test step count"),
                );
            });

        assert!(matches!(
            create_database_snapshot_with_test_control(
                &mut store,
                &fixture.staging_parent,
                &mut control,
            ),
            Err(HeleosError::ResourceLimit)
        ));
        assert_eq!(step_attempts.get(), 0);
        assert_eq!(
            fs::read_dir(&fixture.staging_parent)
                .expect("read staging parent after size rejection")
                .count(),
            0
        );
        create_audited_project(&mut store, &TestIds::new(400), "After size rejection");
    }
}
