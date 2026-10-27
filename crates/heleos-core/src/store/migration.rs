use rusqlite::{Transaction, params};

use super::Store;
use super::schema::FOUNDATION_SCHEMA_VERSION;
use crate::{HeleosError, Result, Sha256Digest};

const FOUNDATION_MIGRATION_SQL: &str = include_str!("../../migrations/0001_foundation.sql");

/// Production callers cannot inject migration SQL.
///
/// ```compile_fail
/// #![forbid(unsafe_code)]
/// use heleos_core::Store;
///
/// fn inject(store: &mut Store) {
///     let _ = store.migrate_with_test_migration(2, "SELECT 1");
/// }
/// ```
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MigrationReport {
    pub from_version: i64,
    pub to_version: i64,
    pub applied_versions: Vec<i64>,
}

impl Store {
    pub fn schema_version(&self) -> Result<i64> {
        let table_exists = self
            .connection
            .query_row(
                "SELECT EXISTS(
                    SELECT 1 FROM sqlite_schema
                    WHERE type = 'table' AND name = ?1
                 )",
                ["schema_migrations"],
                |row| row.get::<_, i64>(0),
            )
            .map_err(|_| HeleosError::Database)?;
        if table_exists == 0 {
            return Ok(0);
        }
        self.connection
            .query_row(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations",
                [],
                |row| row.get(0),
            )
            .map_err(|_| HeleosError::Database)
    }

    pub fn migrate(&mut self) -> Result<MigrationReport> {
        if self.read_only {
            return Err(HeleosError::PolicyDenied);
        }
        self.recheck_writer_lock_identity()?;
        self.recheck_database_identity()?;
        let from_version = self.schema_version()?;
        validate_applied_migrations(self, from_version)?;
        if from_version > FOUNDATION_SCHEMA_VERSION {
            return Err(HeleosError::Migration);
        }

        let mut applied_versions = Vec::new();
        if from_version < FOUNDATION_SCHEMA_VERSION {
            self.recheck_writer_lock_identity()?;
            self.recheck_database_identity()?;
            let migration_hash = migration_hash(FOUNDATION_MIGRATION_SQL)?;
            let transaction = self
                .connection
                .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)
                .map_err(|_| HeleosError::Migration)?;
            apply_migration(
                transaction,
                FOUNDATION_SCHEMA_VERSION,
                FOUNDATION_MIGRATION_SQL,
                &migration_hash,
            )?;
            self.recheck_writer_lock_identity()?;
            self.harden_sqlite_sidecars()?;
            self.recheck_database_identity()?;
            applied_versions.push(FOUNDATION_SCHEMA_VERSION);
        }

        Ok(MigrationReport {
            from_version,
            to_version: self.schema_version()?,
            applied_versions,
        })
    }
}

fn apply_migration(
    transaction: Transaction<'_>,
    version: i64,
    sql: &str,
    hash: &str,
) -> Result<()> {
    transaction
        .execute_batch(sql)
        .map_err(|_| HeleosError::Migration)?;
    transaction
        .execute(
            "INSERT INTO schema_migrations (version, sha256) VALUES (?1, ?2)",
            params![version, hash],
        )
        .map_err(|_| HeleosError::Migration)?;
    transaction.commit().map_err(|_| HeleosError::Migration)
}

fn validate_applied_migrations(store: &Store, current_version: i64) -> Result<()> {
    if current_version == 0 {
        return Ok(());
    }
    if current_version > FOUNDATION_SCHEMA_VERSION {
        return Err(HeleosError::Migration);
    }

    let expected_hash = migration_hash(FOUNDATION_MIGRATION_SQL)?;
    let stored_hash = store
        .connection
        .query_row(
            "SELECT sha256 FROM schema_migrations WHERE version = ?1",
            [FOUNDATION_SCHEMA_VERSION],
            |row| row.get::<_, String>(0),
        )
        .map_err(|_| HeleosError::Migration)?;
    if stored_hash != expected_hash {
        return Err(HeleosError::Migration);
    }
    Ok(())
}

fn migration_hash(sql: &str) -> Result<String> {
    Ok(Sha256Digest::hash_reader(sql.as_bytes())?.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_failing_private_second_migration_rolls_back_schema_and_version() {
        let mut store = Store::open_in_memory().expect("open private migration test store");
        store.migrate().expect("apply foundation migration");
        let sql = "CREATE TABLE must_rollback (id TEXT PRIMARY KEY);
                   INSERT INTO table_that_does_not_exist (id) VALUES ('failure');";
        let hash = migration_hash(sql).expect("hash faulty test migration");
        let transaction = store
            .connection
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)
            .expect("begin private migration transaction");

        assert!(matches!(
            apply_migration(transaction, 2, sql, &hash),
            Err(HeleosError::Migration)
        ));
        assert_eq!(store.schema_version().expect("schema version"), 1);
        let exists = store
            .connection
            .query_row(
                "SELECT count(*) FROM sqlite_schema WHERE type = 'table' AND name = ?1",
                ["must_rollback"],
                |row| row.get::<_, i64>(0),
            )
            .expect("query rolled-back table");
        assert_eq!(exists, 0);
    }
}
