#![forbid(unsafe_code)]

use std::fs;

use heleos_core::{BackupService, HeleosError, Store};
use heleos_verification::{
    Sandbox,
    hostile_backup::{HostileBackup, Mutation},
};

fn database_bytes(sandbox: &Sandbox) -> Vec<u8> {
    let path = sandbox.root.join("fixture.sqlite3");
    let mut store = Store::open_writer(&path).unwrap();
    store.migrate().unwrap();
    drop(store);
    fs::read(path).unwrap()
}

// The success control prevents an encoder defect from making every rejection green.
#[test]
fn independently_encoded_container_verifies_and_restores() {
    let sandbox = Sandbox::new();
    let database = database_bytes(&sandbox);
    let encoded = HostileBackup::new(database).encode(&sandbox.signer, Mutation::None);
    let path = sandbox.encrypt("independent.age", &encoded);
    let report = BackupService::verify_container(
        fs::File::open(&path).unwrap(),
        &sandbox.identity,
        &sandbox.trusted_signer(),
    )
    .unwrap();
    assert_eq!(report.container.entry_count, 1);
    assert_eq!(report.checked_audit_event_count, 0);
    let destination = sandbox.root.join("restored");
    BackupService::restore(
        fs::File::open(path).unwrap(),
        &sandbox.identity,
        &sandbox.trusted_signer(),
        &destination,
    )
    .unwrap();
    let restored = Store::open_read_only(destination.join("foundation.sqlite3")).unwrap();
    assert!(restored.verify_integrity().unwrap().is_clean());
}

fn assert_rejected(mutation: Mutation) {
    let sandbox = Sandbox::new();
    let encoded = HostileBackup::new(database_bytes(&sandbox)).encode(&sandbox.signer, mutation);
    let path = sandbox.encrypt("hostile.age", &encoded);
    let destination = sandbox.root.join("must-not-exist");
    for restore in [false, true] {
        let file = fs::File::open(&path).unwrap();
        let error = if restore {
            BackupService::restore(
                file,
                &sandbox.identity,
                &sandbox.trusted_signer(),
                &destination,
            )
            .map(|_| ())
            .unwrap_err()
        } else {
            BackupService::verify_container(file, &sandbox.identity, &sandbox.trusted_signer())
                .map(|_| ())
                .unwrap_err()
        };
        assert!(
            matches!(
                error,
                HeleosError::InvalidBackupContainer | HeleosError::BackupIntegrity
            ),
            "{mutation:?}: {error:?}"
        );
        assert!(
            !destination.exists(),
            "{mutation:?} published a destination"
        );
        sandbox.assert_no_backup_staging();
    }
}

macro_rules! rejection {
    ($name:ident, $mutation:ident) => {
        #[test]
        fn $name() {
            assert_rejected(Mutation::$mutation);
        }
    };
}

rejection!(rejects_wrong_magic, Magic);
rejection!(rejects_oversized_manifest_before_allocation, ManifestLength);
rejection!(rejects_oversized_table_before_allocation, TableLength);
rejection!(rejects_authenticated_oversized_database, DatabaseLength);
rejection!(rejects_authenticated_oversized_blob, BlobLength);
rejection!(rejects_authenticated_unknown_entry_kind, UnknownKind);
rejection!(rejects_authenticated_blob_before_database, DatabaseOrder);
rejection!(rejects_authenticated_duplicate_database, DuplicateDatabase);
rejection!(rejects_authenticated_duplicate_blob, DuplicateBlob);
rejection!(rejects_authenticated_unsorted_blobs, BlobOrder);
rejection!(rejects_signature_mutation, Signature);
rejection!(rejects_embedded_signer_substitution, Signer);
rejection!(rejects_entry_table_mutation, TableBytes);
rejection!(rejects_payload_mutation, PayloadBytes);
rejection!(rejects_truncated_payload, TruncatedPayload);
rejection!(rejects_plaintext_trailing_data, TrailingData);
rejection!(rejects_noncanonical_signed_manifest, NoncanonicalManifest);
rejection!(rejects_authenticated_payload_total_mismatch, PayloadTotal);

#[test]
fn rejects_authenticated_future_database_schema() {
    let sandbox = Sandbox::new();
    let path = sandbox.root.join("future.sqlite3");
    let mut store = Store::open_writer(&path).unwrap();
    store.migrate().unwrap();
    drop(store);
    let connection = rusqlite::Connection::open(&path).unwrap();
    connection
        .execute(
            "INSERT INTO schema_migrations(version, sha256) VALUES (?1, ?2)",
            rusqlite::params![999_i64, "a".repeat(64)],
        )
        .unwrap();
    drop(connection);
    let encoded =
        HostileBackup::new(fs::read(path).unwrap()).encode(&sandbox.signer, Mutation::None);
    let encrypted = sandbox.encrypt("future.age", &encoded);
    let destination = sandbox.root.join("future-restore");
    let result = BackupService::restore(
        fs::File::open(encrypted).unwrap(),
        &sandbox.identity,
        &sandbox.trusted_signer(),
        &destination,
    );
    assert!(
        matches!(
            result,
            Err(HeleosError::Integrity
                | HeleosError::Migration
                | HeleosError::InvalidBackupContainer)
        ),
        "{result:?}"
    );
    assert!(!destination.exists());
    sandbox.assert_no_backup_staging();
}
