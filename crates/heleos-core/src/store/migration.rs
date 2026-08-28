use rusqlite::{Transaction, params};

use super::Store;
use super::schema::FOUNDATION_SCHEMA_VERSION;
use crate::{HeleosError, Result, Sha256Digest};

const FOUNDATION_MIGRATION_SQL: &str = include_str!("../../migrations/0001_foundation.sql");

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
        let from_version = self.schema_version()?;
        validate_applied_migrations(self, from_version)?;
        if from_version > FOUNDATION_SCHEMA_VERSION {
            return Err(HeleosError::Migration);
        }

        let mut applied_versions = Vec::new();
        if from_version < FOUNDATION_SCHEMA_VERSION {
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

    #[doc(hidden)]
    #[cfg(debug_assertions)]
    pub fn migrate_with_test_migration(&mut self, version: i64, sql: &'static str) -> Result<()> {
        if self.read_only || version != self.schema_version()? + 1 {
            return Err(HeleosError::Migration);
        }
        let hash = migration_hash(sql)?;
        let transaction = self
            .connection
            .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)
            .map_err(|_| HeleosError::Migration)?;
        apply_migration(transaction, version, sql, &hash).map(|_| ())
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
