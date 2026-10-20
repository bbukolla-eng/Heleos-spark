#![forbid(unsafe_code)]

use std::{
    fs,
    io::Read,
    sync::{Arc, Barrier},
};

use heleos_core::{
    BackupService, HeleosError, ReconciliationFinding, Sha256Digest, Store, Vault, VaultInventory,
    VaultInventoryEntry, VaultVerification,
};
use heleos_verification::{Sandbox, WriterProcess, put};

fn project_id() -> heleos_core::ProjectId {
    "00000000-0000-4000-8000-000000000010".parse().unwrap()
}

#[test]
fn sql_payloads_remain_values_and_cannot_drop_foundation_tables() {
    let sandbox = Sandbox::new();
    let mut store = sandbox.store("database.sqlite3");
    let vault = sandbox.vault("vault");
    sandbox.project(&mut store, project_id(), "x'); DROP TABLE projects; --");
    sandbox
        .ingest(
            &mut store,
            &vault,
            project_id(),
            "drawing.pdf",
            b"%PDF-storage-test",
            "x'); DELETE FROM audit_events; --",
        )
        .unwrap();
    let inspection = heleos_core::FoundationReader::new(&store, &vault)
        .inspect_foundation(project_id())
        .unwrap();
    assert_eq!(inspection.counts.documents, 1);
    assert_eq!(inspection.counts.ingest_events, 1);
    assert!(store.verify_integrity().unwrap().is_clean());
    assert!(
        heleos_core::FoundationReader::new(&store, &vault)
            .verify_audit_chain()
            .unwrap()
            .valid
    );
}

#[test]
fn unknown_project_cannot_create_orphan_intake_records() {
    let sandbox = Sandbox::new();
    let mut store = sandbox.store("database.sqlite3");
    let vault = sandbox.vault("vault");
    let error = sandbox
        .ingest(
            &mut store,
            &vault,
            project_id(),
            "drawing.pdf",
            b"%PDF-storage-test",
            "missing-project",
        )
        .unwrap_err();
    assert!(matches!(error, HeleosError::NotFound));
    let audit = heleos_core::FoundationReader::new(&store, &vault)
        .verify_audit_chain()
        .unwrap();
    assert!(audit.valid);
    assert_eq!(audit.checked_event_count, 0);
    assert!(store.verify_integrity().unwrap().is_clean());
}

#[test]
fn public_integrity_check_detects_hostile_foreign_key_damage() {
    let sandbox = Sandbox::new();
    let mut store = sandbox.store("database.sqlite3");
    let vault = sandbox.vault("vault");
    sandbox.project(&mut store, project_id(), "before damage");
    sandbox
        .ingest(
            &mut store,
            &vault,
            project_id(),
            "drawing.pdf",
            b"%PDF-storage-test",
            "first",
        )
        .unwrap();
    let attacker = rusqlite::Connection::open(sandbox.root.join("database.sqlite3")).unwrap();
    attacker
        .execute_batch("PRAGMA foreign_keys=OFF; DELETE FROM projects;")
        .unwrap();
    drop(attacker);
    let report = store.verify_integrity().unwrap();
    assert!(!report.is_clean());
    assert!(!report.foreign_key_violations.is_empty());
}

#[test]
fn audit_chain_matches_independent_jcs_unicode_golden_vector() {
    let sandbox = Sandbox::new();
    let mut store = sandbox.store("database.sqlite3");
    let vault = sandbox.vault("vault");
    sandbox.project(&mut store, project_id(), "A \"duct\" Ω");
    let report = heleos_core::FoundationReader::new(&store, &vault)
        .verify_audit_chain()
        .unwrap();
    assert!(report.valid);
    assert_eq!(report.checked_event_count, 1);
    assert_eq!(report.first_sequence, Some(1));
    assert_eq!(report.last_sequence, Some(1));
    // Hand-authored RFC 8785 event document, hashed with a separate Node SHA-256
    // implementation during fixture design; it includes the zero genesis hash.
    assert_eq!(
        report.head_hash.to_string(),
        "de79cdcfc69ace45b6ae48f0955fd3921ed2983ebe5ebc7e568b3b4e04678d6e"
    );
}

#[test]
fn audit_and_intake_rows_refuse_hostile_updates_and_deletes() {
    let sandbox = Sandbox::new();
    let mut store = sandbox.store("database.sqlite3");
    let vault = sandbox.vault("vault");
    sandbox.project(&mut store, project_id(), "append-only");
    sandbox
        .ingest(
            &mut store,
            &vault,
            project_id(),
            "drawing.pdf",
            b"%PDF-storage-test",
            "first",
        )
        .unwrap();
    let before = heleos_core::FoundationReader::new(&store, &vault)
        .inspect_foundation(project_id())
        .unwrap();
    let audit_before = heleos_core::FoundationReader::new(&store, &vault)
        .verify_audit_chain()
        .unwrap();
    let attacker = rusqlite::Connection::open(sandbox.root.join("database.sqlite3")).unwrap();
    attacker
        .execute_batch("PRAGMA ignore_check_constraints=ON;")
        .unwrap();
    for statement in [
        "UPDATE audit_events SET actor='replacement'",
        "DELETE FROM audit_events",
        "UPDATE ingest_events SET source_name='replacement'",
        "DELETE FROM ingest_events",
    ] {
        let error = attacker.execute(statement, []).unwrap_err();
        assert!(
            matches!(error, rusqlite::Error::SqliteFailure(ref failure, _) if failure.extended_code == rusqlite::ffi::SQLITE_CONSTRAINT_TRIGGER),
            "{statement}: {error:?}"
        );
    }
    drop(attacker);
    assert_eq!(
        heleos_core::FoundationReader::new(&store, &vault)
            .inspect_foundation(project_id())
            .unwrap(),
        before
    );
    assert_eq!(
        heleos_core::FoundationReader::new(&store, &vault)
            .verify_audit_chain()
            .unwrap(),
        audit_before
    );
}

#[test]
fn wal_backup_uses_locked_writer_and_preserves_complete_project_revision_traversal() {
    let sandbox = Sandbox::new();
    let mut store = sandbox.store("source.sqlite3");
    let vault = sandbox.vault("vault");
    let second_project: heleos_core::ProjectId =
        "00000000-0000-4000-8000-000000000020".parse().unwrap();
    sandbox.project(&mut store, project_id(), "first project");
    sandbox.project(&mut store, second_project, "second project");
    let a = sandbox
        .ingest(
            &mut store,
            &vault,
            project_id(),
            "a.pdf",
            b"%PDF-evidence-A",
            "a",
        )
        .unwrap();
    let b = sandbox
        .ingest(
            &mut store,
            &vault,
            project_id(),
            "b.pdf",
            b"%PDF-evidence-B",
            "b",
        )
        .unwrap();
    sandbox
        .ingest(
            &mut store,
            &vault,
            second_project,
            "shared.pdf",
            b"%PDF-evidence-A",
            "shared",
        )
        .unwrap();
    let before: Vec<_> = [project_id(), second_project]
        .into_iter()
        .map(|id| {
            heleos_core::FoundationReader::new(&store, &vault)
                .inspect_foundation(id)
                .unwrap()
        })
        .collect();
    assert_eq!(before[0].revision_ids.len(), 2);
    assert_eq!(before[1].revision_ids.len(), 1);
    assert!(
        fs::metadata(sandbox.root.join("source.sqlite3-wal"))
            .unwrap()
            .len()
            > 32
    );
    let lock_path = store.writer_lock().unwrap().path().to_owned();
    let backup_path = sandbox.root.join("wal.age");
    BackupService::create(
        &mut store,
        &vault,
        &backup_path,
        &sandbox.identity.to_public(),
        &sandbox.signer,
    )
    .unwrap();
    assert_eq!(store.writer_lock().unwrap().path(), lock_path);
    assert!(matches!(
        Store::open_writer(sandbox.root.join("source.sqlite3")),
        Err(HeleosError::WriterBusy)
    ));
    let destination = sandbox.root.join("restored");
    let receipt = BackupService::restore(
        fs::File::open(&backup_path).unwrap(),
        &sandbox.identity,
        &sandbox.trusted_signer(),
        &destination,
    )
    .unwrap();
    assert_eq!(receipt.verification.container.entry_count, 5); // DB + two originals + two manifests.
    let restored = Store::open_read_only(destination.join("foundation.sqlite3")).unwrap();
    let restored_vault = sandbox.open_vault(&destination.join("vault"));
    let reader = heleos_core::FoundationReader::new(&restored, &restored_vault);
    for (id, expected) in [project_id(), second_project].into_iter().zip(before) {
        assert_eq!(reader.inspect_foundation(id).unwrap(), expected);
    }
    let mut inventory = Vec::new();
    for (original_receipt, original_bytes, lineage_count) in [
        (a, b"%PDF-evidence-A".as_slice(), 2),
        (b, b"%PDF-evidence-B".as_slice(), 1),
    ] {
        let evidence = reader
            .evidence_manifest_for_revision(original_receipt.revision_id.unwrap())
            .unwrap();
        assert_eq!(evidence.lineages.len(), lineage_count);
        let mut bytes = Vec::new();
        restored_vault
            .open_verified(evidence.manifest.original.sha256)
            .unwrap()
            .read_to_end(&mut bytes)
            .unwrap();
        assert_eq!(bytes, original_bytes);
        let mut manifest_bytes = Vec::new();
        restored_vault
            .open_verified(evidence.manifest_content_sha256)
            .unwrap()
            .read_to_end(&mut manifest_bytes)
            .unwrap();
        assert_eq!(
            manifest_bytes,
            serde_jcs::to_vec(&evidence.manifest).unwrap()
        );
        assert_eq!(evidence.manifest.pages.len(), 1);
        assert_eq!(
            (
                evidence.manifest.pages[0].width_micropoints,
                evidence.manifest.pages[0].height_micropoints
            ),
            (612_000_000, 792_000_000)
        );
        inventory.extend([
            VaultInventoryEntry {
                digest: evidence.manifest.original.sha256,
                expected_byte_length: evidence.manifest.original.byte_length,
                vault_key: evidence.original_vault_key,
            },
            VaultInventoryEntry {
                digest: evidence.manifest_content_sha256,
                expected_byte_length: evidence.manifest_byte_length,
                vault_key: evidence.manifest_vault_key,
            },
        ]);
    }
    assert!(
        restored_vault
            .reconcile(&VaultInventory::try_from_entries(inventory).unwrap())
            .unwrap()
            .findings
            .is_empty()
    );
    assert!(reader.verify_audit_chain().unwrap().valid);
    assert!(restored.verify_integrity().unwrap().is_clean());
}

#[test]
fn existing_backup_and_restore_destinations_preserve_sentinels() {
    let sandbox = Sandbox::new();
    let mut store = sandbox.store("source.sqlite3");
    let vault = sandbox.vault("vault");
    let backup_path = sandbox.root.join("backup.age");
    BackupService::create(
        &mut store,
        &vault,
        &backup_path,
        &sandbox.identity.to_public(),
        &sandbox.signer,
    )
    .unwrap();
    let before = fs::read(&backup_path).unwrap();
    assert!(matches!(
        BackupService::create(
            &mut store,
            &vault,
            &backup_path,
            &sandbox.identity.to_public(),
            &sandbox.signer
        ),
        Err(HeleosError::PolicyDenied)
    ));
    assert_eq!(fs::read(&backup_path).unwrap(), before);
    let destination = sandbox.root.join("existing");
    fs::create_dir(&destination).unwrap();
    fs::write(destination.join("sentinel"), b"keep me").unwrap();
    assert!(matches!(
        BackupService::restore(
            fs::File::open(backup_path).unwrap(),
            &sandbox.identity,
            &sandbox.trusted_signer(),
            &destination
        ),
        Err(HeleosError::PolicyDenied)
    ));
    assert_eq!(fs::read(destination.join("sentinel")).unwrap(), b"keep me");
    assert_eq!(fs::read_dir(destination).unwrap().count(), 1);
}

#[test]
fn competing_restores_publish_one_complete_destination() {
    let sandbox = Sandbox::new();
    let mut store = sandbox.store("source.sqlite3");
    let vault = sandbox.vault("vault");
    let backup_path = sandbox.root.join("backup.age");
    BackupService::create(
        &mut store,
        &vault,
        &backup_path,
        &sandbox.identity.to_public(),
        &sandbox.signer,
    )
    .unwrap();
    let destination = sandbox.root.join("raced");
    let barrier = Barrier::new(2);
    let trusted_signer = sandbox.trusted_signer();
    let results = std::thread::scope(|scope| {
        let first = scope.spawn(|| {
            barrier.wait();
            BackupService::restore(
                fs::File::open(&backup_path).unwrap(),
                &sandbox.identity,
                &trusted_signer,
                &destination,
            )
        });
        let second = scope.spawn(|| {
            barrier.wait();
            BackupService::restore(
                fs::File::open(&backup_path).unwrap(),
                &sandbox.identity,
                &trusted_signer,
                &destination,
            )
        });
        [first.join().unwrap(), second.join().unwrap()]
    });
    assert_eq!(
        results.iter().filter(|result| result.is_ok()).count(),
        1,
        "{results:?}"
    );
    assert_eq!(
        results
            .iter()
            .filter(|result| matches!(result, Err(HeleosError::PolicyDenied)))
            .count(),
        1,
        "{results:?}"
    );
    assert!(
        Store::open_read_only(destination.join("foundation.sqlite3"))
            .unwrap()
            .verify_integrity()
            .unwrap()
            .is_clean()
    );
    sandbox.assert_no_backup_staging();
}

#[cfg(unix)]
#[test]
fn symlinked_vault_object_and_database_are_refused_without_following_target() {
    let sandbox = Sandbox::new();
    let vault = sandbox.vault("vault");
    let object = put(&vault, b"original");
    let object_path = sandbox.root.join("vault").join(&object.vault_key);
    let moved = sandbox.root.join("outside-object");
    fs::rename(&object_path, &moved).unwrap();
    std::os::unix::fs::symlink(&moved, &object_path).unwrap();
    assert!(vault.open_verified(object.digest).is_err());
    assert!(matches!(
        vault.verify(object.digest).unwrap(),
        VaultVerification::NonRegular { .. }
    ));
    assert_eq!(fs::read(&moved).unwrap(), b"original");
    let store = sandbox.store("real.sqlite3");
    drop(store);
    let database_link = sandbox.root.join("linked.sqlite3");
    std::os::unix::fs::symlink(sandbox.root.join("real.sqlite3"), &database_link).unwrap();
    assert!(matches!(
        Store::open_writer(&database_link),
        Err(HeleosError::PolicyDenied)
    ));
    assert!(matches!(
        Store::open_read_only(&database_link),
        Err(HeleosError::PolicyDenied)
    ));
}

#[cfg(windows)]
#[test]
fn vault_reparse_junction_is_refused_and_target_is_preserved() {
    let sandbox = Sandbox::new();
    let target = sandbox.root.join("target");
    fs::create_dir(&target).unwrap();
    fs::write(target.join("sentinel"), b"preserved").unwrap();
    let junction = sandbox.root.join("junction");
    let status = std::process::Command::new("cmd")
        .args(["/D", "/C", "mklink", "/J"])
        .arg(&junction)
        .arg(&target)
        .status()
        .unwrap();
    assert!(status.success(), "native junction fixture creation failed");
    let result = Vault::open(heleos_core::VaultConfig {
        root: junction,
        open_mode: heleos_core::VaultOpenMode::ExistingOnly,
    });
    assert!(matches!(result, Err(HeleosError::PolicyDenied)));
    assert_eq!(fs::read(target.join("sentinel")).unwrap(), b"preserved");
}

#[test]
fn empty_store_migrates_once_and_reopens_without_losing_schema() {
    let sandbox = Sandbox::new();
    let path = sandbox.root.join("database.sqlite3");
    let mut store = Store::open_writer(&path).unwrap();
    assert_eq!(store.schema_version().unwrap(), 0);
    let report = store.migrate().unwrap();
    assert_eq!((report.from_version, report.to_version), (0, 1));
    assert_eq!(report.applied_versions, [1]);
    assert!(store.verify_integrity().unwrap().is_clean());
    drop(store);
    let mut reopened = Store::open_writer(&path).unwrap();
    assert_eq!(reopened.schema_version().unwrap(), 1);
    assert!(reopened.migrate().unwrap().applied_versions.is_empty());
    drop(reopened);
    let reader = Store::open_read_only(&path).unwrap();
    assert!(reader.verify_integrity().unwrap().is_clean());
}

#[test]
fn independently_prepared_future_schema_is_not_migrated() {
    let sandbox = Sandbox::new();
    let path = sandbox.root.join("future.sqlite3");
    let mut store = Store::open_writer(&path).unwrap();
    store.migrate().unwrap();
    drop(store);
    let connection = rusqlite::Connection::open(&path).unwrap();
    connection
        .execute(
            "INSERT INTO schema_migrations VALUES (?1, ?2)",
            rusqlite::params![999, "f".repeat(64)],
        )
        .unwrap();
    drop(connection);
    let mut store = Store::open_writer(&path).unwrap();
    assert!(matches!(store.migrate(), Err(HeleosError::Migration)));
    assert_eq!(store.schema_version().unwrap(), 999);
}

#[test]
fn second_process_cannot_write_until_first_process_releases_store() {
    let sandbox = Sandbox::new();
    let path = sandbox.root.join("locked.sqlite3");
    let holder = WriterProcess::start(env!("CARGO_BIN_EXE_hold-writer"), &path);
    assert!(matches!(
        Store::open_writer(&path),
        Err(HeleosError::WriterBusy)
    ));
    holder.release();
    assert!(
        Store::open_writer(&path)
            .unwrap()
            .verify_integrity()
            .unwrap()
            .is_clean()
    );
}

#[test]
fn duplicate_vault_publication_preserves_original_bytes_and_identity() {
    let sandbox = Sandbox::new();
    let vault = sandbox.vault("vault");
    let first = put(&vault, b"stable evidence");
    let second = put(&vault, b"stable evidence");
    assert!(first.newly_published);
    assert!(!second.newly_published);
    assert_eq!(first.digest, second.digest);
    let mut bytes = Vec::new();
    vault
        .open_verified(first.digest)
        .unwrap()
        .read_to_end(&mut bytes)
        .unwrap();
    assert_eq!(bytes, b"stable evidence");
}

#[test]
fn concurrent_vault_publication_has_exactly_one_winner() {
    let sandbox = Sandbox::new();
    let vault = Arc::new(sandbox.vault("vault"));
    let barrier = Arc::new(Barrier::new(4));
    let threads: Vec<_> = (0..4)
        .map(|_| {
            let vault = Arc::clone(&vault);
            let barrier = Arc::clone(&barrier);
            std::thread::spawn(move || {
                barrier.wait();
                put(&vault, b"concurrent immutable evidence")
            })
        })
        .collect();
    let objects: Vec<_> = threads
        .into_iter()
        .map(|thread| thread.join().unwrap())
        .collect();
    assert_eq!(
        objects
            .iter()
            .filter(|object| object.newly_published)
            .count(),
        1
    );
    assert!(
        objects
            .iter()
            .all(|object| object.digest == objects[0].digest)
    );
    assert!(matches!(
        vault.verify(objects[0].digest).unwrap(),
        VaultVerification::Verified { .. }
    ));
}

#[test]
fn changed_vault_bytes_cannot_be_read_as_verified_evidence() {
    let sandbox = Sandbox::new();
    let vault = sandbox.vault("vault");
    let object = put(&vault, b"authentic evidence");
    fs::write(
        sandbox.root.join("vault").join(&object.vault_key),
        b"corrupted evidence",
    )
    .unwrap();
    assert!(matches!(
        vault.verify(object.digest).unwrap(),
        VaultVerification::Corrupt { .. }
    ));
    assert!(matches!(
        vault.open_verified(object.digest),
        Err(HeleosError::Integrity)
    ));
}

#[test]
fn vault_hardlinks_are_rejected_without_modifying_either_name() {
    let sandbox = Sandbox::new();
    let vault = sandbox.vault("vault");
    let object = put(&vault, b"single owner evidence");
    let original = sandbox.root.join("vault").join(&object.vault_key);
    let alias = sandbox.root.join("alias");
    fs::hard_link(&original, &alias).unwrap();
    assert!(matches!(
        vault.verify(object.digest).unwrap(),
        VaultVerification::UnexpectedLinkCount { link_count: 2, .. }
    ));
    assert!(vault.open_verified(object.digest).is_err());
    assert_eq!(fs::read(original).unwrap(), b"single owner evidence");
    assert_eq!(fs::read(alias).unwrap(), b"single owner evidence");
}

#[test]
fn reconciliation_reports_missing_orphan_and_partial_without_deleting_them() {
    let sandbox = Sandbox::new();
    let vault = sandbox.vault("vault");
    let orphan = put(&vault, b"unreferenced object");
    let missing = Sha256Digest::from_bytes([0x73; 32]);
    let inventory = VaultInventory::try_from_entries([VaultInventoryEntry {
        digest: missing,
        expected_byte_length: 3,
        vault_key: Vault::object_key(missing),
    }])
    .unwrap();
    let partial = sandbox.root.join("vault/.staging/interrupted.partial");
    fs::write(&partial, b"partial bytes").unwrap();
    heleos_core::apply_private_permissions(&partial).unwrap();
    let report = vault.reconcile(&inventory).unwrap();
    assert_eq!(report.findings.len(), 3);
    assert!(report.findings.iter().any(|finding| matches!(finding, ReconciliationFinding::MissingReference { inventory } if inventory.digest == missing)));
    assert!(report.findings.iter().any(|finding| matches!(finding, ReconciliationFinding::UnreferencedObject { verification: VaultVerification::Verified { digest, .. } } if *digest == orphan.digest)));
    assert!(
        report
            .findings
            .iter()
            .any(|finding| matches!(finding, ReconciliationFinding::StagingPartial { .. }))
    );
    assert_eq!(fs::read(partial).unwrap(), b"partial bytes");
    assert!(vault.open_verified(orphan.digest).is_ok());
}

#[test]
fn empty_store_backup_stays_encrypted_and_restores_a_clean_tree() {
    let sandbox = Sandbox::new();
    let mut store = sandbox.store("source.sqlite3");
    let vault = sandbox.vault("vault");
    let path = sandbox.root.join("backup.age");
    BackupService::create(
        &mut store,
        &vault,
        &path,
        &sandbox.identity.to_public(),
        &sandbox.signer,
    )
    .unwrap();
    assert!(
        fs::read(&path)
            .unwrap()
            .starts_with(b"age-encryption.org/v1\n")
    );
    let wrong = age::x25519::Identity::generate();
    assert!(matches!(
        BackupService::verify_container(
            fs::File::open(&path).unwrap(),
            &wrong,
            &sandbox.trusted_signer()
        ),
        Err(HeleosError::BackupDecryption)
    ));
    let destination = sandbox.root.join("restore");
    BackupService::restore(
        fs::File::open(&path).unwrap(),
        &sandbox.identity,
        &sandbox.trusted_signer(),
        &destination,
    )
    .unwrap();
    let restored = Store::open_read_only(destination.join("foundation.sqlite3")).unwrap();
    assert!(restored.verify_integrity().unwrap().is_clean());
    let vault = sandbox.open_vault(&destination.join("vault"));
    assert!(
        vault
            .reconcile(&VaultInventory::try_from_entries([]).unwrap())
            .unwrap()
            .findings
            .is_empty()
    );
}
