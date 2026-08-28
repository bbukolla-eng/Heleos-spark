mod migration;
mod permissions;
mod schema;

use std::ffi::OsString;
use std::fs::{self, File, OpenOptions};
use std::io;
use std::path::{Path, PathBuf};
use std::time::Duration;

use fs2::FileExt;
use rusqlite::config::DbConfig;
use rusqlite::{Connection, OpenFlags, Transaction, TransactionBehavior};

use crate::{HeleosError, Result};

pub use migration::MigrationReport;
pub use permissions::{apply_private_permissions, verify_private_permissions};
pub use schema::{FOUNDATION_SCHEMA_VERSION, IntegrityReport};

const BUSY_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Debug)]
pub struct WriterLock {
    file: File,
    path: PathBuf,
}

impl WriterLock {
    fn acquire(database_path: &Path) -> Result<Self> {
        let path = sidecar_path(database_path, ".writer.lock");
        let (file, identity) = open_checked_regular_file(&path, true, true)?;
        match FileExt::try_lock_exclusive(&file) {
            Ok(()) => {}
            Err(error) if lock_is_busy(&error) => return Err(HeleosError::WriterBusy),
            Err(error) => return Err(HeleosError::Io(error)),
        }
        recheck_file_identity(&path, &file, &identity)?;
        Ok(Self { file, path })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for WriterLock {
    fn drop(&mut self) {
        let _ = FileExt::unlock(&self.file);
    }
}

pub struct Store {
    pub(super) connection: Connection,
    database_identity_handle: Option<File>,
    writer_lock: Option<WriterLock>,
    database_path: Option<PathBuf>,
    pub(super) read_only: bool,
}

impl Store {
    pub fn open_writer(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        harden_parent(path)?;
        reject_invalid_existing_path(path)?;
        let writer_lock = WriterLock::acquire(path)?;
        let (database_file, identity) = open_checked_regular_file(path, true, true)?;

        let flags = OpenFlags::SQLITE_OPEN_READ_WRITE
            | OpenFlags::SQLITE_OPEN_NO_MUTEX
            | OpenFlags::SQLITE_OPEN_NOFOLLOW
            | OpenFlags::SQLITE_OPEN_PRIVATE_CACHE;
        let connection = Connection::open_with_flags(path, flags).map_err(database_error)?;
        recheck_file_identity(path, &database_file, &identity)?;
        configure_connection(&connection, ConnectionKind::FileWriter)?;
        recheck_file_identity(path, &database_file, &identity)?;

        Ok(Self {
            connection,
            database_identity_handle: Some(database_file),
            writer_lock: Some(writer_lock),
            database_path: Some(path.to_owned()),
            read_only: false,
        })
    }

    pub fn open_read_only(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        harden_parent(path)?;
        reject_invalid_existing_path(path)?;
        let (database_file, identity) = open_checked_regular_file(path, false, false)?;
        let flags = OpenFlags::SQLITE_OPEN_READ_ONLY
            | OpenFlags::SQLITE_OPEN_NO_MUTEX
            | OpenFlags::SQLITE_OPEN_NOFOLLOW
            | OpenFlags::SQLITE_OPEN_PRIVATE_CACHE;
        let connection = Connection::open_with_flags(path, flags).map_err(database_error)?;
        recheck_file_identity(path, &database_file, &identity)?;
        configure_connection(&connection, ConnectionKind::FileReader)?;
        recheck_file_identity(path, &database_file, &identity)?;

        Ok(Self {
            connection,
            database_identity_handle: Some(database_file),
            writer_lock: None,
            database_path: Some(path.to_owned()),
            read_only: true,
        })
    }

    pub fn open_in_memory() -> Result<Self> {
        let connection = Connection::open_in_memory().map_err(database_error)?;
        configure_connection(&connection, ConnectionKind::MemoryWriter)?;
        Ok(Self {
            connection,
            database_identity_handle: None,
            writer_lock: None,
            database_path: None,
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
        self.recheck_database_identity()?;
        let transaction = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(database_error)?;
        let value = operation(&transaction)?;
        transaction.commit().map_err(database_error)?;
        self.harden_sqlite_sidecars()?;
        self.recheck_database_identity()?;
        Ok(value)
    }

    pub fn verify_integrity(&self) -> Result<IntegrityReport> {
        self.recheck_database_identity()?;
        let integrity_check_violations =
            collect_single_column_check(&self.connection, "PRAGMA integrity_check")?;
        let quick_check_violations =
            collect_single_column_check(&self.connection, "PRAGMA quick_check")?;

        let mut statement = self
            .connection
            .prepare("PRAGMA foreign_key_check")
            .map_err(database_error)?;
        let foreign_key_violations = statement
            .query_map([], |row| {
                let table: String = row.get(0)?;
                let row_id: Option<i64> = row.get(1)?;
                let parent: String = row.get(2)?;
                let foreign_key_index: i64 = row.get(3)?;
                Ok(format!(
                    "table={table};row_id={row_id:?};parent={parent};foreign_key={foreign_key_index}"
                ))
            })
            .map_err(database_error)?
            .collect::<rusqlite::Result<Vec<_>>>()
            .map_err(database_error)?;

        Ok(IntegrityReport {
            integrity_check_violations,
            quick_check_violations,
            foreign_key_violations,
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
            let identity = FileIdentity::from_metadata(&file.metadata().map_err(HeleosError::Io)?);
            recheck_file_identity(path, file, &identity)?;
        }
        Ok(())
    }
}

#[derive(Clone, Copy)]
enum ConnectionKind {
    FileWriter,
    FileReader,
    MemoryWriter,
}

fn configure_connection(connection: &Connection, kind: ConnectionKind) -> Result<()> {
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

fn collect_single_column_check(connection: &Connection, sql: &'static str) -> Result<Vec<String>> {
    let mut statement = connection.prepare(sql).map_err(database_error)?;
    let rows = statement
        .query_map([], |row| row.get::<_, String>(0))
        .map_err(database_error)?;
    let mut violations = Vec::new();
    for row in rows {
        let result = row.map_err(database_error)?;
        if result != "ok" {
            violations.push(result);
        }
    }
    Ok(violations)
}

fn harden_parent(path: &Path) -> Result<()> {
    let parent = path
        .parent()
        .filter(|value| !value.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let metadata = fs::symlink_metadata(parent).map_err(HeleosError::Io)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(HeleosError::PolicyDenied);
    }
    reject_reparse_point(&metadata)?;
    apply_private_permissions(parent)?;
    verify_private_permissions(parent)
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
) -> Result<(File, FileIdentity)> {
    let before = match fs::symlink_metadata(path) {
        Ok(metadata) => {
            validate_regular_metadata(&metadata)?;
            Some(FileIdentity::from_metadata(&metadata))
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => None,
        Err(error) => return Err(HeleosError::Io(error)),
    };

    let mut options = OpenOptions::new();
    options.read(true).write(writable);
    if before.is_none() {
        if !create_if_missing {
            return Err(HeleosError::NotFound);
        }
        options.create_new(true);
    }
    let file = options.open(path).map_err(HeleosError::Io)?;
    let identity = FileIdentity::from_metadata(&file.metadata().map_err(HeleosError::Io)?);
    if let Some(before) = before
        && before != identity
    {
        return Err(HeleosError::PolicyDenied);
    }
    recheck_file_identity(path, &file, &identity)?;
    apply_private_permissions(path)?;
    verify_private_permissions(path)?;
    recheck_file_identity(path, &file, &identity)?;
    Ok((file, identity))
}

fn recheck_file_identity(path: &Path, file: &File, expected: &FileIdentity) -> Result<()> {
    let handle_metadata = file.metadata().map_err(HeleosError::Io)?;
    validate_regular_metadata(&handle_metadata)?;
    let path_metadata = fs::symlink_metadata(path).map_err(HeleosError::Io)?;
    validate_regular_metadata(&path_metadata)?;
    let handle_identity = FileIdentity::from_metadata(&handle_metadata);
    let path_identity = FileIdentity::from_metadata(&path_metadata);
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
#[derive(Clone, Copy, Eq, PartialEq)]
struct FileIdentity {
    device: u64,
    inode: u64,
}

#[cfg(unix)]
impl FileIdentity {
    fn from_metadata(metadata: &fs::Metadata) -> Self {
        use std::os::unix::fs::MetadataExt;

        Self {
            device: metadata.dev(),
            inode: metadata.ino(),
        }
    }
}

#[cfg(windows)]
#[derive(Clone, Copy, Eq, PartialEq)]
struct FileIdentity {
    attributes: u32,
    creation_time: u64,
}

#[cfg(windows)]
impl FileIdentity {
    fn from_metadata(metadata: &fs::Metadata) -> Self {
        use std::os::windows::fs::MetadataExt;

        Self {
            attributes: metadata.file_attributes(),
            creation_time: metadata.creation_time(),
        }
    }
}

#[cfg(not(any(unix, windows)))]
#[derive(Clone, Copy, Eq, PartialEq)]
struct FileIdentity {
    length: u64,
}

#[cfg(not(any(unix, windows)))]
impl FileIdentity {
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
