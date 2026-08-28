mod migration;
mod permissions;
mod schema;

use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;

use fs2::FileExt;
use rusqlite::config::DbConfig;
use rusqlite::functions::FunctionFlags;
use rusqlite::{Connection, OpenFlags, Transaction, TransactionBehavior};

use crate::{HeleosError, Result};

pub use migration::MigrationReport;
pub use permissions::{apply_private_permissions, verify_private_permissions};
pub use schema::{FOUNDATION_SCHEMA_VERSION, INTEGRITY_VIOLATION_LIMIT, IntegrityReport};

const BUSY_TIMEOUT: Duration = Duration::from_secs(5);
const INTEGRITY_CHECK_SQL: &str = "PRAGMA integrity_check(129)";
const QUICK_CHECK_SQL: &str = "PRAGMA quick_check(129)";
const GIBIBYTE: u64 = 1024 * 1024 * 1024;
const MAX_SNAPSHOT_DATABASE_BYTES: u64 = 16 * GIBIBYTE;
const MAX_SNAPSHOT_WAL_BYTES: u64 = 16 * GIBIBYTE;
const MAX_SNAPSHOT_COPY_BYTES: u64 = 32 * GIBIBYTE;
const MAX_SNAPSHOT_RECOVERY_ALLOWANCE: u64 = 17 * GIBIBYTE;
const SNAPSHOT_RECOVERY_OVERHEAD: u64 = GIBIBYTE;
const MINIMUM_FREE_SPACE_RESERVE: u64 = 20 * GIBIBYTE;
const SNAPSHOT_COPY_CHUNK_BYTES: usize = 1024 * 1024;

#[derive(Debug)]
pub struct WriterLock {
    file: File,
    path: PathBuf,
    identity: FileMarker,
}

impl WriterLock {
    fn acquire(database_path: &Path) -> Result<Self> {
        let path = sidecar_path(database_path, ".writer.lock");
        let (file, identity) =
            open_checked_regular_file(&path, true, true, PermissionPolicy::ApplyAndVerify)?;
        match FileExt::try_lock_exclusive(&file) {
            Ok(()) => {}
            Err(error) if lock_is_busy(&error) => return Err(HeleosError::WriterBusy),
            Err(error) => return Err(HeleosError::Io(error)),
        }
        recheck_file_identity(&path, &file, &identity)?;
        Ok(Self {
            file,
            path,
            identity,
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    fn recheck_identity(&self) -> Result<()> {
        recheck_file_identity(&self.path, &self.file, &self.identity)
    }
}

impl Drop for WriterLock {
    fn drop(&mut self) {
        let _ = FileExt::unlock(&self.file);
    }
}

#[derive(Debug)]
struct ReaderLock {
    file: File,
    path: PathBuf,
    identity: FileMarker,
}

impl ReaderLock {
    fn acquire(database_path: &Path) -> Result<Self> {
        let path = sidecar_path(database_path, ".writer.lock");
        let (file, identity) =
            open_checked_regular_file(&path, false, false, PermissionPolicy::VerifyOnly)?;
        match FileExt::try_lock_shared(&file) {
            Ok(()) => {}
            Err(error) if lock_is_busy(&error) => return Err(HeleosError::WriterBusy),
            Err(error) => return Err(HeleosError::Io(error)),
        }
        recheck_file_identity(&path, &file, &identity)?;
        Ok(Self {
            file,
            path,
            identity,
        })
    }

    fn recheck_identity(&self) -> Result<()> {
        recheck_file_identity(&self.path, &self.file, &self.identity)
    }
}

impl Drop for ReaderLock {
    fn drop(&mut self) {
        let _ = FileExt::unlock(&self.file);
    }
}

pub struct Store {
    // Rust drops fields in declaration order. For readers this closes staging SQLite, then the
    // checked source DB/WAL/SHM handles, then the shared lock, and finally deletes staging.
    pub(super) connection: Connection,
    database_identity_handle: Option<File>,
    source_sidecar_handles: Vec<CheckedSourceFile>,
    writer_lock: Option<WriterLock>,
    reader_lock: Option<ReaderLock>,
    database_path: Option<PathBuf>,
    _snapshot_directory: Option<tempfile::TempDir>,
    pub(super) read_only: bool,
}

impl Store {
    pub fn open_writer(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        check_parent(path, PermissionPolicy::ApplyAndVerify)?;
        reject_invalid_existing_path(path)?;
        check_sqlite_sidecars(path, PermissionPolicy::ApplyAndVerify)?;
        let writer_lock = WriterLock::acquire(path)?;
        let (database_file, identity) =
            open_checked_regular_file(path, true, true, PermissionPolicy::ApplyAndVerify)?;
        writer_lock.recheck_identity()?;

        let flags = OpenFlags::SQLITE_OPEN_READ_WRITE
            | OpenFlags::SQLITE_OPEN_NO_MUTEX
            | OpenFlags::SQLITE_OPEN_NOFOLLOW
            | OpenFlags::SQLITE_OPEN_PRIVATE_CACHE;
        let connection = Connection::open_with_flags(path, flags).map_err(database_error)?;
        recheck_file_identity(path, &database_file, &identity)?;
        configure_connection(&connection, ConnectionKind::FileWriter)?;
        recheck_file_identity(path, &database_file, &identity)?;

        let store = Self {
            connection,
            database_identity_handle: Some(database_file),
            source_sidecar_handles: Vec::new(),
            writer_lock: Some(writer_lock),
            reader_lock: None,
            database_path: Some(path.to_owned()),
            _snapshot_directory: None,
            read_only: false,
        };
        store.harden_sqlite_sidecars()?;

        Ok(store)
    }

    pub fn open_read_only(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        check_parent(path, PermissionPolicy::VerifyOnly)?;
        reject_invalid_existing_path(path)?;
        verify_private_permissions(path)?;
        check_sqlite_sidecars(path, PermissionPolicy::VerifyOnly)?;
        let reader_lock = ReaderLock::acquire(path)?;
        check_parent(path, PermissionPolicy::VerifyOnly)?;
        reject_invalid_existing_path(path)?;
        check_sqlite_sidecars(path, PermissionPolicy::VerifyOnly)?;
        let (database_file, identity) =
            open_checked_regular_file(path, false, false, PermissionPolicy::VerifyOnly)?;
        let wal_path = sidecar_path(path, "-wal");
        let shm_path = sidecar_path(path, "-shm");
        let wal_file = open_optional_verified_file(&wal_path)?;
        let shm_file = open_optional_verified_file(&shm_path)?;
        let source_lengths = snapshot_source_lengths(&database_file, wal_file.as_ref())?;
        let snapshot_directory = tempfile::Builder::new()
            .prefix("heleos-read-snapshot-")
            .tempdir()
            .map_err(HeleosError::Io)?;
        apply_private_permissions(snapshot_directory.path())?;
        let snapshot_root = fs::canonicalize(snapshot_directory.path()).map_err(HeleosError::Io)?;
        ensure_snapshot_capacity(&snapshot_root, source_lengths)?;
        let snapshot_database = snapshot_root.join("snapshot.sqlite3");
        copy_snapshot_file(
            &database_file,
            &snapshot_database,
            source_lengths.database_bytes,
        )?;
        if let Some(wal_file) = &wal_file {
            copy_snapshot_file(
                &wal_file.file,
                &sidecar_path(&snapshot_database, "-wal"),
                source_lengths.wal_bytes,
            )?;
        }
        recheck_snapshot_source_lengths(&database_file, wal_file.as_ref(), source_lengths)?;
        recheck_file_identity(path, &database_file, &identity)?;
        recheck_optional_file(&wal_path, wal_file.as_ref())?;
        recheck_optional_file(&shm_path, shm_file.as_ref())?;
        reader_lock.recheck_identity()?;
        check_parent(path, PermissionPolicy::VerifyOnly)?;
        check_sqlite_sidecars(path, PermissionPolicy::VerifyOnly)?;

        let flags = OpenFlags::SQLITE_OPEN_READ_WRITE
            | OpenFlags::SQLITE_OPEN_NO_MUTEX
            | OpenFlags::SQLITE_OPEN_NOFOLLOW
            | OpenFlags::SQLITE_OPEN_PRIVATE_CACHE;
        let connection =
            Connection::open_with_flags(&snapshot_database, flags).map_err(database_error)?;
        recheck_file_identity(path, &database_file, &identity)?;
        configure_connection(&connection, ConnectionKind::FileReader)?;
        recheck_file_identity(path, &database_file, &identity)?;
        check_sqlite_sidecars(path, PermissionPolicy::VerifyOnly)?;
        reader_lock.recheck_identity()?;
        harden_snapshot_files(&snapshot_database)?;
        validate_recovered_snapshot(&snapshot_root, &snapshot_database, source_lengths)?;

        Ok(Self {
            connection,
            database_identity_handle: Some(database_file),
            source_sidecar_handles: wal_file.into_iter().chain(shm_file).collect(),
            writer_lock: None,
            reader_lock: Some(reader_lock),
            database_path: Some(path.to_owned()),
            _snapshot_directory: Some(snapshot_directory),
            read_only: true,
        })
    }

    pub fn open_in_memory() -> Result<Self> {
        let connection = Connection::open_in_memory().map_err(database_error)?;
        configure_connection(&connection, ConnectionKind::MemoryWriter)?;
        Ok(Self {
            connection,
            database_identity_handle: None,
            source_sidecar_handles: Vec::new(),
            writer_lock: None,
            reader_lock: None,
            database_path: None,
            _snapshot_directory: None,
            read_only: false,
        })
    }

    pub fn with_immediate_transaction<T>(
        &mut self,
        operation: impl FnOnce(&Transaction<'_>) -> Result<T>,
    ) -> Result<T> {
        if self.read_only {
            return Err(HeleosError::PolicyDenied);
        }
        self.recheck_writer_lock_identity()?;
        self.recheck_database_identity()?;
        let transaction = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(database_error)?;
        let value = operation(&transaction)?;
        transaction.commit().map_err(database_error)?;
        self.recheck_writer_lock_identity()?;
        self.harden_sqlite_sidecars()?;
        self.recheck_database_identity()?;
        Ok(value)
    }

    pub fn verify_integrity(&self) -> Result<IntegrityReport> {
        self.recheck_database_identity()?;
        let (integrity_check_violations, integrity_check_truncated) =
            collect_single_column_check(&self.connection, INTEGRITY_CHECK_SQL)?;
        let (quick_check_violations, quick_check_truncated) =
            collect_single_column_check(&self.connection, QUICK_CHECK_SQL)?;

        let mut statement = self
            .connection
            .prepare("PRAGMA foreign_key_check")
            .map_err(database_error)?;
        let rows = statement
            .query_map([], |row| {
                let table: String = row.get(0)?;
                let row_id: Option<i64> = row.get(1)?;
                let parent: String = row.get(2)?;
                let foreign_key_index: i64 = row.get(3)?;
                Ok(format!(
                    "table={table};row_id={row_id:?};parent={parent};foreign_key={foreign_key_index}"
                ))
            })
            .map_err(database_error)?;
        let mut foreign_key_violations = Vec::new();
        for row in rows {
            foreign_key_violations.push(row.map_err(database_error)?);
            if foreign_key_violations.len() > INTEGRITY_VIOLATION_LIMIT {
                break;
            }
        }
        let foreign_key_check_truncated = foreign_key_violations.len() > INTEGRITY_VIOLATION_LIMIT;
        foreign_key_violations.truncate(INTEGRITY_VIOLATION_LIMIT);

        Ok(IntegrityReport {
            integrity_check_violations,
            integrity_check_truncated,
            quick_check_violations,
            quick_check_truncated,
            foreign_key_violations,
            foreign_key_check_truncated,
        })
    }

    pub fn writer_lock(&self) -> Option<&WriterLock> {
        self.writer_lock.as_ref()
    }

    pub(super) fn harden_sqlite_sidecars(&self) -> Result<()> {
        let Some(database_path) = &self.database_path else {
            return Ok(());
        };
        for suffix in ["-wal", "-shm"] {
            let path = sidecar_path(database_path, suffix);
            match fs::symlink_metadata(&path) {
                Ok(_) => {
                    apply_private_permissions(&path)?;
                    verify_private_permissions(&path)?;
                }
                Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                Err(error) => return Err(HeleosError::Io(error)),
            }
        }
        Ok(())
    }

    pub(super) fn recheck_database_identity(&self) -> Result<()> {
        if let (Some(path), Some(file)) = (&self.database_path, &self.database_identity_handle) {
            let identity = FileMarker::from_metadata(&file.metadata().map_err(HeleosError::Io)?);
            recheck_file_identity(path, file, &identity)?;
        }
        if let Some(reader_lock) = &self.reader_lock {
            reader_lock.recheck_identity()?;
        }
        for sidecar in &self.source_sidecar_handles {
            sidecar.recheck()?;
        }
        Ok(())
    }

    pub(super) fn recheck_writer_lock_identity(&self) -> Result<()> {
        match (&self.database_path, &self.writer_lock) {
            (Some(_), Some(writer_lock)) => writer_lock.recheck_identity(),
            (None, None) => Ok(()),
            _ => Err(HeleosError::PolicyDenied),
        }
    }
}

#[derive(Debug)]
struct CheckedSourceFile {
    file: File,
    path: PathBuf,
    marker: FileMarker,
}

impl CheckedSourceFile {
    fn recheck(&self) -> Result<()> {
        recheck_file_identity(&self.path, &self.file, &self.marker)
    }
}

fn open_optional_verified_file(path: &Path) -> Result<Option<CheckedSourceFile>> {
    match fs::symlink_metadata(path) {
        Ok(_) => open_checked_regular_file(path, false, false, PermissionPolicy::VerifyOnly).map(
            |(file, marker)| {
                Some(CheckedSourceFile {
                    file,
                    path: path.to_owned(),
                    marker,
                })
            },
        ),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(HeleosError::Io(error)),
    }
}

fn recheck_optional_file(path: &Path, opened: Option<&CheckedSourceFile>) -> Result<()> {
    match opened {
        Some(file) => file.recheck(),
        None => match fs::symlink_metadata(path) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Ok(_) => Err(HeleosError::PolicyDenied),
            Err(error) => Err(HeleosError::Io(error)),
        },
    }
}

fn copy_snapshot_file(source: &File, destination: &Path, expected_bytes: u64) -> Result<()> {
    let mut source = source.try_clone().map_err(HeleosError::Io)?;
    source.seek(SeekFrom::Start(0)).map_err(HeleosError::Io)?;
    let mut destination_file = OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .open(destination)
        .map_err(HeleosError::Io)?;
    stream_and_sync_snapshot(
        &mut source,
        &mut destination_file,
        |source, destination| copy_snapshot_chunks(source, destination, expected_bytes),
        File::sync_all,
    )
    .map_err(HeleosError::Io)?;
    apply_private_permissions(destination)
}

#[derive(Clone, Copy)]
struct SnapshotSourceLengths {
    database_bytes: u64,
    wal_bytes: u64,
    copy_bytes: u64,
    recovery_allowance: u64,
    maximum_staging_bytes: u64,
}

fn snapshot_source_lengths(
    database: &File,
    wal: Option<&CheckedSourceFile>,
) -> Result<SnapshotSourceLengths> {
    let database_bytes = database.metadata().map_err(HeleosError::Io)?.len();
    let wal_bytes = wal
        .map(|checked| checked.file.metadata().map(|metadata| metadata.len()))
        .transpose()
        .map_err(HeleosError::Io)?
        .unwrap_or(0);
    snapshot_budget_from_lengths(database_bytes, wal_bytes)
}

fn snapshot_budget_from_lengths(
    database_bytes: u64,
    wal_bytes: u64,
) -> Result<SnapshotSourceLengths> {
    let copy_bytes = snapshot_copy_bytes_from_lengths(database_bytes, wal_bytes)?;
    let recovery_allowance = wal_bytes
        .checked_add(SNAPSHOT_RECOVERY_OVERHEAD)
        .ok_or(HeleosError::PolicyDenied)?
        .min(MAX_SNAPSHOT_RECOVERY_ALLOWANCE);
    let maximum_staging_bytes = copy_bytes
        .checked_add(recovery_allowance)
        .ok_or(HeleosError::PolicyDenied)?;
    Ok(SnapshotSourceLengths {
        database_bytes,
        wal_bytes,
        copy_bytes,
        recovery_allowance,
        maximum_staging_bytes,
    })
}

fn recheck_snapshot_source_lengths(
    database: &File,
    wal: Option<&CheckedSourceFile>,
    expected: SnapshotSourceLengths,
) -> Result<()> {
    let actual = snapshot_source_lengths(database, wal)?;
    if actual.database_bytes != expected.database_bytes
        || actual.wal_bytes != expected.wal_bytes
        || actual.copy_bytes != expected.copy_bytes
        || actual.recovery_allowance != expected.recovery_allowance
        || actual.maximum_staging_bytes != expected.maximum_staging_bytes
    {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

fn snapshot_copy_bytes_from_lengths(database_bytes: u64, wal_bytes: u64) -> Result<u64> {
    if database_bytes > MAX_SNAPSHOT_DATABASE_BYTES || wal_bytes > MAX_SNAPSHOT_WAL_BYTES {
        return Err(HeleosError::PolicyDenied);
    }
    let copy_bytes = database_bytes
        .checked_add(wal_bytes)
        .ok_or(HeleosError::PolicyDenied)?;
    if copy_bytes > MAX_SNAPSHOT_COPY_BYTES {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(copy_bytes)
}

fn ensure_snapshot_capacity(staging_path: &Path, lengths: SnapshotSourceLengths) -> Result<()> {
    let total_bytes = fs2::total_space(staging_path).map_err(HeleosError::Io)?;
    let available_bytes = fs2::available_space(staging_path).map_err(HeleosError::Io)?;
    validate_snapshot_capacity(
        lengths.copy_bytes,
        lengths.recovery_allowance,
        total_bytes,
        available_bytes,
    )
}

fn validate_snapshot_capacity(
    copy_bytes: u64,
    recovery_allowance: u64,
    total_bytes: u64,
    available_bytes: u64,
) -> Result<()> {
    let reserve = required_snapshot_reserve(total_bytes);
    let required = copy_bytes
        .checked_add(recovery_allowance)
        .ok_or(HeleosError::PolicyDenied)?
        .checked_add(reserve)
        .ok_or(HeleosError::PolicyDenied)?;
    if available_bytes < required {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

fn copy_snapshot_chunks(
    source: &mut File,
    destination: &mut File,
    expected_bytes: u64,
) -> io::Result<()> {
    let mut buffer = vec![0_u8; SNAPSHOT_COPY_CHUNK_BYTES];
    let mut copied = 0_u64;
    loop {
        let count = source.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        copied = copied
            .checked_add(u64::try_from(count).map_err(io::Error::other)?)
            .ok_or_else(|| io::Error::other("snapshot byte count overflow"))?;
        if copied > expected_bytes {
            return Err(io::Error::other(
                "snapshot source grew beyond its declared length",
            ));
        }
        destination.write_all(&buffer[..count])?;
        #[cfg(test)]
        snapshot_copy_test_barrier_after_first_chunk(copied)?;
    }
    if copied != expected_bytes {
        return Err(io::Error::new(
            io::ErrorKind::UnexpectedEof,
            "snapshot source length changed during copy",
        ));
    }
    Ok(())
}

#[cfg(test)]
fn snapshot_copy_test_barrier_after_first_chunk(copied: u64) -> io::Result<()> {
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::time::Instant;

    static BARRIER_USED: AtomicBool = AtomicBool::new(false);
    let Some(root) = std::env::var_os("HELEOS_TEST_SNAPSHOT_COPY_BARRIER") else {
        return Ok(());
    };
    if copied == 0 || BARRIER_USED.swap(true, Ordering::SeqCst) {
        return Ok(());
    }
    let root = PathBuf::from(root);
    fs::write(root.join("copied"), b"ready")?;
    let deadline = Instant::now() + Duration::from_secs(15);
    while !root.join("continue").is_file() {
        if Instant::now() >= deadline {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "snapshot copy test barrier timed out",
            ));
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    Ok(())
}

fn required_snapshot_reserve(total_bytes: u64) -> u64 {
    let ten_percent = total_bytes / 10 + u64::from(!total_bytes.is_multiple_of(10));
    MINIMUM_FREE_SPACE_RESERVE.max(ten_percent)
}

fn stream_and_sync_snapshot(
    source: &mut File,
    destination: &mut File,
    copy: impl FnOnce(&mut File, &mut File) -> io::Result<()>,
    sync: impl FnOnce(&File) -> io::Result<()>,
) -> io::Result<()> {
    copy(source, destination)?;
    sync(destination)
}

fn harden_snapshot_files(database_path: &Path) -> Result<()> {
    apply_private_permissions(database_path)?;
    for suffix in ["-wal", "-shm"] {
        let sidecar = sidecar_path(database_path, suffix);
        match fs::symlink_metadata(&sidecar) {
            Ok(_) => apply_private_permissions(&sidecar)?,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(HeleosError::Io(error)),
        }
    }
    Ok(())
}

fn validate_recovered_snapshot(
    staging_root: &Path,
    database_path: &Path,
    lengths: SnapshotSourceLengths,
) -> Result<()> {
    let database_name = database_path.file_name().ok_or(HeleosError::PolicyDenied)?;
    let wal_name = sidecar_path(Path::new(database_name), "-wal");
    let shm_name = sidecar_path(Path::new(database_name), "-shm");
    let allowed_names = [
        database_name.to_os_string(),
        wal_name.into_os_string(),
        shm_name.into_os_string(),
    ];
    let mut logical_bytes = 0_u64;
    let mut database_found = false;
    for entry in fs::read_dir(staging_root).map_err(HeleosError::Io)? {
        let entry = entry.map_err(HeleosError::Io)?;
        if !allowed_names.iter().any(|name| name == &entry.file_name()) {
            return Err(HeleosError::PolicyDenied);
        }
        database_found |= entry.file_name() == database_name;
        let metadata = fs::symlink_metadata(entry.path()).map_err(HeleosError::Io)?;
        validate_regular_metadata(&metadata)?;
        verify_private_permissions(&entry.path())?;
        logical_bytes = logical_bytes
            .checked_add(metadata.len())
            .ok_or(HeleosError::PolicyDenied)?;
    }
    if !database_found || logical_bytes > lengths.maximum_staging_bytes {
        return Err(HeleosError::PolicyDenied);
    }
    let total_bytes = fs2::total_space(staging_root).map_err(HeleosError::Io)?;
    let available_bytes = fs2::available_space(staging_root).map_err(HeleosError::Io)?;
    if available_bytes < required_snapshot_reserve(total_bytes) {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

#[derive(Clone, Copy)]
enum ConnectionKind {
    FileWriter,
    FileReader,
    MemoryWriter,
}

fn configure_connection(connection: &Connection, kind: ConnectionKind) -> Result<()> {
    register_jcs_validator(connection)?;
    connection
        .busy_timeout(BUSY_TIMEOUT)
        .map_err(database_error)?;
    connection
        .set_db_config(DbConfig::SQLITE_DBCONFIG_DEFENSIVE, true)
        .map_err(database_error)?;
    connection
        .set_db_config(DbConfig::SQLITE_DBCONFIG_TRUSTED_SCHEMA, false)
        .map_err(database_error)?;
    connection
        .set_db_config(DbConfig::SQLITE_DBCONFIG_DQS_DDL, false)
        .map_err(database_error)?;
    connection
        .set_db_config(DbConfig::SQLITE_DBCONFIG_DQS_DML, false)
        .map_err(database_error)?;
    connection
        .set_db_config(DbConfig::SQLITE_DBCONFIG_ENABLE_ATTACH_CREATE, false)
        .map_err(database_error)?;
    connection
        .set_db_config(DbConfig::SQLITE_DBCONFIG_ENABLE_ATTACH_WRITE, false)
        .map_err(database_error)?;
    connection
        .pragma_update(None, "foreign_keys", "ON")
        .map_err(database_error)?;
    connection
        .pragma_update(None, "trusted_schema", "OFF")
        .map_err(database_error)?;
    connection
        .pragma_update(None, "synchronous", "FULL")
        .map_err(database_error)?;
    connection
        .pragma_update(None, "temp_store", "MEMORY")
        .map_err(database_error)?;

    match kind {
        ConnectionKind::FileWriter => {
            let mode = connection
                .pragma_query_value(Some("main"), "journal_mode", |row| row.get::<_, String>(0))
                .map_err(database_error)?;
            if !mode.eq_ignore_ascii_case("wal") {
                connection
                    .pragma_update(None, "journal_mode", "WAL")
                    .map_err(database_error)?;
            }
            let mode = connection
                .pragma_query_value(Some("main"), "journal_mode", |row| row.get::<_, String>(0))
                .map_err(database_error)?;
            if !mode.eq_ignore_ascii_case("wal") {
                return Err(HeleosError::Database);
            }
        }
        ConnectionKind::FileReader => {
            let mode = connection
                .pragma_query_value(Some("main"), "journal_mode", |row| row.get::<_, String>(0))
                .map_err(database_error)?;
            if !mode.eq_ignore_ascii_case("wal") {
                return Err(HeleosError::Database);
            }
            connection
                .pragma_update(None, "query_only", "ON")
                .map_err(database_error)?;
        }
        ConnectionKind::MemoryWriter => {}
    }
    Ok(())
}

fn register_jcs_validator(connection: &Connection) -> Result<()> {
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
        .map_err(database_error)
}

fn collect_single_column_check(
    connection: &Connection,
    sql: &'static str,
) -> Result<(Vec<String>, bool)> {
    let mut statement = connection.prepare(sql).map_err(database_error)?;
    let rows = statement
        .query_map([], |row| row.get::<_, String>(0))
        .map_err(database_error)?;
    bound_integrity_violations(rows)
}

fn bound_integrity_violations(
    rows: impl IntoIterator<Item = rusqlite::Result<String>>,
) -> Result<(Vec<String>, bool)> {
    let mut violations = Vec::new();
    for row in rows {
        let result = row.map_err(database_error)?;
        if result != "ok" {
            violations.push(result);
        }
    }
    let truncated = violations.len() > INTEGRITY_VIOLATION_LIMIT;
    violations.truncate(INTEGRITY_VIOLATION_LIMIT);
    Ok((violations, truncated))
}

#[derive(Clone, Copy)]
enum PermissionPolicy {
    ApplyAndVerify,
    VerifyOnly,
}

fn check_parent(path: &Path, policy: PermissionPolicy) -> Result<()> {
    let parent = path
        .parent()
        .filter(|value| !value.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let metadata = fs::symlink_metadata(parent).map_err(HeleosError::Io)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(HeleosError::PolicyDenied);
    }
    reject_reparse_point(&metadata)?;
    enforce_permission_policy(parent, policy)
}

fn check_sqlite_sidecars(database_path: &Path, policy: PermissionPolicy) -> Result<()> {
    for suffix in ["-wal", "-shm"] {
        let path = sidecar_path(database_path, suffix);
        match fs::symlink_metadata(&path) {
            Ok(metadata) => {
                validate_regular_metadata(&metadata)?;
                enforce_permission_policy(&path, policy)?;
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(HeleosError::Io(error)),
        }
    }
    Ok(())
}

fn enforce_permission_policy(path: &Path, policy: PermissionPolicy) -> Result<()> {
    match policy {
        PermissionPolicy::ApplyAndVerify => apply_private_permissions(path),
        PermissionPolicy::VerifyOnly => verify_private_permissions(path),
    }
}

fn reject_invalid_existing_path(path: &Path) -> Result<()> {
    match fs::symlink_metadata(path) {
        Ok(metadata) => validate_regular_metadata(&metadata),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(HeleosError::Io(error)),
    }
}

fn open_checked_regular_file(
    path: &Path,
    create_if_missing: bool,
    writable: bool,
    permission_policy: PermissionPolicy,
) -> Result<(File, FileMarker)> {
    let before = match fs::symlink_metadata(path) {
        Ok(metadata) => {
            validate_regular_metadata(&metadata)?;
            Some(FileMarker::from_metadata(&metadata))
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => None,
        Err(error) => return Err(HeleosError::Io(error)),
    };

    let mut options = OpenOptions::new();
    options.read(true).write(writable);
    configure_retained_file_sharing(&mut options);
    if before.is_none() {
        if !create_if_missing {
            return Err(HeleosError::NotFound);
        }
        options.create_new(true);
    }
    let file = options.open(path).map_err(HeleosError::Io)?;
    let identity = FileMarker::from_metadata(&file.metadata().map_err(HeleosError::Io)?);
    if let Some(before) = before
        && before != identity
    {
        return Err(HeleosError::PolicyDenied);
    }
    recheck_file_identity(path, &file, &identity)?;
    enforce_permission_policy(path, permission_policy)?;
    recheck_file_identity(path, &file, &identity)?;
    Ok((file, identity))
}

#[cfg(windows)]
fn configure_retained_file_sharing(options: &mut OpenOptions) {
    use std::os::windows::fs::OpenOptionsExt;

    const FILE_SHARE_READ: u32 = 0x0000_0001;
    const FILE_SHARE_WRITE: u32 = 0x0000_0002;
    options.share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE);
}

#[cfg(not(windows))]
const fn configure_retained_file_sharing(_: &mut OpenOptions) {}

fn recheck_file_identity(path: &Path, file: &File, expected: &FileMarker) -> Result<()> {
    let handle_metadata = file.metadata().map_err(HeleosError::Io)?;
    validate_regular_metadata(&handle_metadata)?;
    let path_metadata = fs::symlink_metadata(path).map_err(HeleosError::Io)?;
    validate_regular_metadata(&path_metadata)?;
    let handle_identity = FileMarker::from_metadata(&handle_metadata);
    let path_identity = FileMarker::from_metadata(&path_metadata);
    if &handle_identity != expected || &path_identity != expected {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

fn validate_regular_metadata(metadata: &fs::Metadata) -> Result<()> {
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(HeleosError::PolicyDenied);
    }
    reject_reparse_point(metadata)
}

#[cfg(windows)]
fn reject_reparse_point(metadata: &fs::Metadata) -> Result<()> {
    use std::os::windows::fs::MetadataExt;

    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;
    if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

#[cfg(not(windows))]
const fn reject_reparse_point(_: &fs::Metadata) -> Result<()> {
    Ok(())
}

#[cfg(unix)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct FileMarker {
    device: u64,
    inode: u64,
}

#[cfg(unix)]
impl FileMarker {
    fn from_metadata(metadata: &fs::Metadata) -> Self {
        use std::os::unix::fs::MetadataExt;

        Self {
            device: metadata.dev(),
            inode: metadata.ino(),
        }
    }
}

#[cfg(windows)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
// This detects some unexpected changes; no-delete sharing, not these fields, prevents replacement.
struct FileMarker {
    attributes: u32,
    creation_time: u64,
}

#[cfg(windows)]
impl FileMarker {
    fn from_metadata(metadata: &fs::Metadata) -> Self {
        use std::os::windows::fs::MetadataExt;

        Self {
            attributes: metadata.file_attributes(),
            creation_time: metadata.creation_time(),
        }
    }
}

#[cfg(not(any(unix, windows)))]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct FileMarker {
    length: u64,
}

#[cfg(not(any(unix, windows)))]
impl FileMarker {
    fn from_metadata(metadata: &fs::Metadata) -> Self {
        Self {
            length: metadata.len(),
        }
    }
}

fn sidecar_path(database_path: &Path, suffix: &str) -> PathBuf {
    let mut value = OsString::from(database_path.as_os_str());
    value.push(suffix);
    PathBuf::from(value)
}

fn lock_is_busy(error: &io::Error) -> bool {
    error.kind() == io::ErrorKind::WouldBlock || error.raw_os_error() == Some(33)
}

fn database_error(_: rusqlite::Error) -> HeleosError {
    HeleosError::Database
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn file_reader_enables_sqlite_query_only() {
        let root =
            std::env::temp_dir().join(format!("heleos-reader-settings-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&root).expect("create reader settings fixture directory");
        let root = fs::canonicalize(root).expect("canonicalize reader settings fixture");
        let database = root.join("foundation.sqlite3");

        let mut writer = Store::open_writer(&database).expect("open fixture writer");
        writer.migrate().expect("migrate fixture database");
        drop(writer);

        let reader = Store::open_read_only(&database).expect("open fixture reader");
        let query_only = reader
            .connection
            .pragma_query_value(None, "query_only", |row| row.get::<_, i64>(0))
            .expect("query reader query_only");
        assert_eq!(query_only, 1);
        assert!(
            reader
                .connection
                .execute("CREATE TABLE reader_must_not_write (id INTEGER)", [])
                .is_err()
        );
        drop(reader);

        fs::remove_dir_all(root).expect("remove reader settings fixture");
    }

    #[test]
    fn snapshot_staging_directory_is_removed_when_reader_drops() {
        let root =
            std::env::temp_dir().join(format!("heleos-reader-cleanup-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&root).expect("create reader cleanup fixture directory");
        let root = fs::canonicalize(root).expect("canonicalize reader cleanup fixture");
        let database = root.join("foundation.sqlite3");

        let mut writer = Store::open_writer(&database).expect("open cleanup fixture writer");
        writer.migrate().expect("migrate cleanup fixture database");
        drop(writer);

        let reader = Store::open_read_only(&database).expect("open cleanup fixture reader");
        let staging = reader
            ._snapshot_directory
            .as_ref()
            .expect("reader retains staging directory")
            .path()
            .to_owned();
        assert!(staging.is_dir());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;

            assert_eq!(
                fs::symlink_metadata(&staging)
                    .expect("staging directory metadata")
                    .permissions()
                    .mode()
                    & 0o777,
                0o700
            );
        }
        drop(reader);
        assert!(!staging.exists());
        drop(Store::open_writer(&database).expect("writer resumes after snapshot cleanup"));

        fs::remove_dir_all(root).expect("remove reader cleanup fixture");
    }

    #[test]
    fn integrity_collector_keeps_exact_limit_without_truncation() {
        let rows = (0..INTEGRITY_VIOLATION_LIMIT)
            .map(|index| Ok::<_, rusqlite::Error>(format!("violation-{index}")));
        let (violations, truncated) =
            bound_integrity_violations(rows).expect("bound exact-limit results");

        assert_eq!(violations.len(), INTEGRITY_VIOLATION_LIMIT);
        assert!(!truncated);
    }

    #[test]
    fn integrity_collector_uses_extra_row_as_explicit_truncation_sentinel() {
        let rows = (0..=INTEGRITY_VIOLATION_LIMIT)
            .map(|index| Ok::<_, rusqlite::Error>(format!("violation-{index}")));
        let (violations, truncated) =
            bound_integrity_violations(rows).expect("bound sentinel results");

        assert_eq!(violations.len(), INTEGRITY_VIOLATION_LIMIT);
        assert!(truncated);
        let report = IntegrityReport {
            integrity_check_violations: violations,
            integrity_check_truncated: true,
            quick_check_violations: Vec::new(),
            quick_check_truncated: false,
            foreign_key_violations: Vec::new(),
            foreign_key_check_truncated: false,
        };
        assert!(!report.is_clean());
    }

    #[test]
    fn snapshot_size_caps_reject_each_oversize_boundary() {
        let exact =
            snapshot_budget_from_lengths(MAX_SNAPSHOT_DATABASE_BYTES, MAX_SNAPSHOT_WAL_BYTES)
                .expect("accept exact snapshot caps");
        assert_eq!(exact.copy_bytes, MAX_SNAPSHOT_COPY_BYTES);
        assert_eq!(exact.recovery_allowance, MAX_SNAPSHOT_RECOVERY_ALLOWANCE);
        assert_eq!(
            exact.maximum_staging_bytes,
            MAX_SNAPSHOT_COPY_BYTES + MAX_SNAPSHOT_RECOVERY_ALLOWANCE
        );
        assert_eq!(
            snapshot_budget_from_lengths(1, 0)
                .expect("budget without WAL")
                .recovery_allowance,
            SNAPSHOT_RECOVERY_OVERHEAD
        );
        assert!(matches!(
            snapshot_copy_bytes_from_lengths(MAX_SNAPSHOT_DATABASE_BYTES + 1, 0),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(matches!(
            snapshot_copy_bytes_from_lengths(0, MAX_SNAPSHOT_WAL_BYTES + 1),
            Err(HeleosError::PolicyDenied)
        ));
    }

    #[test]
    fn insufficient_snapshot_capacity_fails_and_private_staging_cleans_up() {
        let staging = tempfile::Builder::new()
            .prefix("heleos-capacity-failure-")
            .tempdir()
            .expect("create capacity failure staging");
        apply_private_permissions(staging.path()).expect("harden capacity failure staging");
        let staging_path = staging.path().to_owned();

        assert!(matches!(
            validate_snapshot_capacity(
                1,
                SNAPSHOT_RECOVERY_OVERHEAD,
                100 * GIBIBYTE,
                MINIMUM_FREE_SPACE_RESERVE
            ),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(matches!(
            validate_snapshot_capacity(
                1,
                SNAPSHOT_RECOVERY_OVERHEAD,
                300 * GIBIBYTE,
                31 * GIBIBYTE
            ),
            Err(HeleosError::PolicyDenied)
        ));
        drop(staging);
        assert!(!staging_path.exists());
    }

    #[test]
    fn exact_snapshot_capacity_includes_recovery_allowance_and_rounded_reserve() {
        let copy_bytes = 7;
        let allowance = SNAPSHOT_RECOVERY_OVERHEAD;
        let total_bytes = 300 * GIBIBYTE + 1;
        let reserve = required_snapshot_reserve(total_bytes);
        assert_eq!(reserve, 30 * GIBIBYTE + 1);
        let required = copy_bytes + allowance + reserve;

        validate_snapshot_capacity(copy_bytes, allowance, total_bytes, required)
            .expect("accept exact required capacity");
        assert!(matches!(
            validate_snapshot_capacity(copy_bytes, allowance, total_bytes, required - 1),
            Err(HeleosError::PolicyDenied)
        ));
    }

    #[test]
    fn recovered_snapshot_rejects_unexpected_entries_and_growth() {
        let staging = tempfile::Builder::new()
            .prefix("heleos-post-recovery-validation-")
            .tempdir()
            .expect("create post-recovery staging");
        apply_private_permissions(staging.path()).expect("harden post-recovery staging");
        let staging_path = staging.path().to_owned();
        let database = staging.path().join("snapshot.sqlite3");
        fs::write(&database, b"a").expect("write staging database");
        apply_private_permissions(&database).expect("harden staging database");
        let lengths = SnapshotSourceLengths {
            database_bytes: 1,
            wal_bytes: 0,
            copy_bytes: 1,
            recovery_allowance: 0,
            maximum_staging_bytes: 1,
        };
        validate_recovered_snapshot(staging.path(), &database, lengths)
            .expect("accept exact staging size");

        let unexpected = staging.path().join("unexpected");
        fs::write(&unexpected, b"x").expect("write unexpected staging entry");
        assert!(matches!(
            validate_recovered_snapshot(staging.path(), &database, lengths),
            Err(HeleosError::PolicyDenied)
        ));
        fs::remove_file(unexpected).expect("remove unexpected staging entry");

        OpenOptions::new()
            .write(true)
            .open(&database)
            .expect("open staging database for growth")
            .set_len(2)
            .expect("grow staging database");
        assert!(matches!(
            validate_recovered_snapshot(staging.path(), &database, lengths),
            Err(HeleosError::PolicyDenied)
        ));
        drop(staging);
        assert!(!staging_path.exists());
    }

    #[test]
    fn injected_copy_and_sync_failures_clean_private_staging() {
        use std::io::Write;

        for (fail_copy, fail_sync) in [(true, false), (false, true)] {
            let staging = tempfile::Builder::new()
                .prefix("heleos-snapshot-io-failure-")
                .tempdir()
                .expect("create I/O failure staging");
            apply_private_permissions(staging.path()).expect("harden I/O failure staging");
            let staging_path = staging.path().to_owned();
            let source_path = staging.path().join("source");
            let destination_path = staging.path().join("destination");
            fs::write(&source_path, b"snapshot bytes").expect("write I/O failure source");
            let mut source = File::open(&source_path).expect("open I/O failure source");
            let mut destination =
                File::create(&destination_path).expect("create I/O failure destination");

            let result = stream_and_sync_snapshot(
                &mut source,
                &mut destination,
                |source, destination| {
                    if fail_copy {
                        destination.write_all(b"partial")?;
                        return Err(io::Error::other("injected copy failure"));
                    }
                    io::copy(source, destination).map(|_| ())
                },
                |destination| {
                    if fail_sync {
                        return Err(io::Error::other("injected sync failure"));
                    }
                    destination.sync_all()
                },
            );
            assert!(result.is_err());
            drop(destination);
            drop(source);
            drop(staging);
            assert!(!staging_path.exists());
        }
    }

    #[test]
    fn snapshot_copy_barrier_process_helper() {
        use std::time::Instant;

        let Some(database) = std::env::var_os("HELEOS_TEST_SNAPSHOT_DATABASE") else {
            return;
        };
        let barrier = PathBuf::from(
            std::env::var_os("HELEOS_TEST_SNAPSHOT_COPY_BARRIER")
                .expect("snapshot barrier directory is configured"),
        );
        let reader = Store::open_read_only(PathBuf::from(database)).expect("child opens reader");
        fs::write(barrier.join("reader-open"), b"ready").expect("signal reader open");
        let deadline = Instant::now() + Duration::from_secs(15);
        while !barrier.join("release-reader").is_file() {
            assert!(
                Instant::now() < deadline,
                "timed out waiting to release snapshot reader"
            );
            std::thread::sleep(Duration::from_millis(10));
        }
        drop(reader);
        fs::write(barrier.join("reader-released"), b"released").expect("signal reader released");
    }

    #[test]
    fn shared_lock_blocks_writer_mid_copy_and_for_complete_reader_lifetime() {
        use std::process::Command;
        use std::time::Instant;

        fn wait_for_file(path: &Path) {
            let deadline = Instant::now() + Duration::from_secs(15);
            while !path.is_file() {
                assert!(Instant::now() < deadline, "timed out waiting for {path:?}");
                std::thread::sleep(Duration::from_millis(10));
            }
        }

        let source_root = tempfile::Builder::new()
            .prefix("heleos-copy-barrier-source-")
            .tempdir()
            .expect("create source directory");
        apply_private_permissions(source_root.path()).expect("harden source directory");
        let source_path =
            fs::canonicalize(source_root.path()).expect("canonicalize source directory");
        let database = source_path.join("foundation.sqlite3");
        let mut writer = Store::open_writer(&database).expect("open fixture writer");
        writer.migrate().expect("migrate fixture database");
        writer
            .with_immediate_transaction(|transaction| {
                transaction
                    .execute_batch("CREATE TABLE private_snapshot_padding (bytes BLOB NOT NULL);")
                    .map_err(database_error)?;
                transaction
                    .execute(
                        "INSERT INTO private_snapshot_padding (bytes) VALUES (zeroblob(?1))",
                        [i64::try_from(3 * SNAPSHOT_COPY_CHUNK_BYTES)
                            .map_err(|_| HeleosError::PolicyDenied)?],
                    )
                    .map_err(database_error)?;
                Ok(())
            })
            .expect("pad fixture beyond two copy chunks");
        writer
            .connection
            .execute_batch("PRAGMA wal_checkpoint(TRUNCATE);")
            .expect("checkpoint padded fixture");
        drop(writer);
        assert!(
            fs::metadata(&database)
                .expect("padded database metadata")
                .len()
                > u64::try_from(2 * SNAPSHOT_COPY_CHUNK_BYTES).expect("chunk length fits u64")
        );

        let barrier = tempfile::Builder::new()
            .prefix("heleos-copy-barrier-signals-")
            .tempdir()
            .expect("create barrier directory");
        let mut child = Command::new(std::env::current_exe().expect("locate unit test executable"))
            .arg("--exact")
            .arg("store::tests::snapshot_copy_barrier_process_helper")
            .arg("--nocapture")
            .env("HELEOS_TEST_SNAPSHOT_DATABASE", &database)
            .env("HELEOS_TEST_SNAPSHOT_COPY_BARRIER", barrier.path())
            .spawn()
            .expect("spawn snapshot child");

        wait_for_file(&barrier.path().join("copied"));
        assert!(matches!(
            Store::open_writer(&database),
            Err(HeleosError::WriterBusy)
        ));

        fs::write(barrier.path().join("continue"), b"continue").expect("release chunked copy");
        wait_for_file(&barrier.path().join("reader-open"));
        assert!(matches!(
            Store::open_writer(&database),
            Err(HeleosError::WriterBusy)
        ));

        fs::write(barrier.path().join("release-reader"), b"release").expect("release reader");
        wait_for_file(&barrier.path().join("reader-released"));
        assert!(child.wait().expect("wait for snapshot child").success());
        drop(Store::open_writer(&database).expect("writer resumes after reader drop"));
    }
}
