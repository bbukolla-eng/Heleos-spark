#![forbid(unsafe_code)]

use std::fs::File;
use std::path::Path;

use heleos_core::backup::BackupService;
use heleos_core::{
    BackupContainerSummary, BackupReceipt, BackupService as RootBackupService,
    BackupVerificationReport, RestoreReceipt, Result, Sha256Digest, Store, Vault,
};

type CreateBackup = fn(
    &mut Store,
    &Vault,
    &Path,
    &age::x25519::Recipient,
    &ed25519_dalek::SigningKey,
) -> Result<BackupReceipt>;
type VerifyBackup = fn(File, &age::x25519::Identity, &[u8; 32]) -> Result<BackupVerificationReport>;
type RestoreBackup = fn(File, &age::x25519::Identity, &[u8; 32], &Path) -> Result<RestoreReceipt>;

fn inspect_container_summary(container: &BackupContainerSummary) {
    let BackupContainerSummary {
        manifest_schema,
        signer_key_sha256,
        entry_table_sha256,
        entry_table_byte_length,
        entry_count,
        decoded_payload_byte_length,
        database_sha256,
        database_byte_length,
    } = container;

    assert_eq!(manifest_schema, "heleos.backup-manifest/v1");
    let _: &Sha256Digest = signer_key_sha256;
    let _: &Sha256Digest = entry_table_sha256;
    let _: &u64 = entry_table_byte_length;
    let _: &u64 = entry_count;
    let _: &u64 = decoded_payload_byte_length;
    let _: &Sha256Digest = database_sha256;
    let _: &u64 = database_byte_length;
}

fn inspect_backup_receipt(receipt: &BackupReceipt) {
    let BackupReceipt {
        schema,
        container,
        ciphertext_byte_length,
    } = receipt;
    assert_eq!(schema, "heleos.backup-receipt/v1");
    inspect_container_summary(container);
    let _: &u64 = ciphertext_byte_length;
}

fn inspect_verification_report(report: &BackupVerificationReport) {
    let BackupVerificationReport {
        schema,
        container,
        ciphertext_byte_length,
        checked_audit_event_count,
        audit_head_hash,
    } = report;
    assert_eq!(schema, "heleos.backup-verification-report/v1");
    inspect_container_summary(container);
    let _: &u64 = ciphertext_byte_length;
    let _: &u64 = checked_audit_event_count;
    let _: &Sha256Digest = audit_head_hash;
}

fn inspect_restore_receipt(receipt: &RestoreReceipt) {
    let RestoreReceipt {
        schema,
        verification,
    } = receipt;
    assert_eq!(schema, "heleos.restore-receipt/v1");
    inspect_verification_report(verification);
}

#[test]
fn public_backup_contract_has_exact_typed_signatures_and_path_free_dtos() {
    let _: Option<RootBackupService> = None;
    let create: CreateBackup = BackupService::create;
    let verify: VerifyBackup = BackupService::verify_container;
    let restore: RestoreBackup = BackupService::restore;

    let _: fn(&BackupReceipt) = inspect_backup_receipt;
    let _: fn(&BackupVerificationReport) = inspect_verification_report;
    let _: fn(&RestoreReceipt) = inspect_restore_receipt;
    let _ = (create, verify, restore);
}
