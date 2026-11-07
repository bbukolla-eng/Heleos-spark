use std::ffi::OsStr;
use std::path::{Path, PathBuf};

use cap_fs_ext::MetadataExt;
use cap_std::fs::Dir;

use super::path::{ClassifiedEntry, classify_and_open_entry, open_directory};
use super::{
    EncodedVaultPath, ReconciliationFinding, ReconciliationReport, Vault, VaultInventory,
    VaultVerification, digest_components, is_lower_hex_component,
};
use crate::{HeleosError, Result, Sha256Digest};

pub(super) fn reconcile_locked(
    vault: &Vault,
    inventory: &VaultInventory,
) -> Result<ReconciliationReport> {
    vault.recheck_fixed_layout()?;
    let mut findings = Vec::new();
    collect_fixed_layout(vault, &mut findings)?;
    collect_staging(vault, &mut findings)?;
    let mut observed = std::collections::BTreeMap::new();
    collect_objects(vault, inventory, &mut observed, &mut findings)?;

    for entry in inventory.entries.values() {
        let expected_vault_key = Vault::object_key(entry.digest);
        if entry.vault_key != expected_vault_key {
            findings.push(ReconciliationFinding::InventoryKeyMismatch {
                inventory: entry.clone(),
                expected_vault_key,
            });
        }
        match observed.get(&entry.digest) {
            None => findings.push(ReconciliationFinding::MissingReference {
                inventory: entry.clone(),
            }),
            Some(Some(actual_byte_length)) if *actual_byte_length != entry.expected_byte_length => {
                findings.push(ReconciliationFinding::InventoryLengthMismatch {
                    inventory: entry.clone(),
                    actual_byte_length: *actual_byte_length,
                });
            }
            Some(_) => {}
        }
    }

    findings.sort_by_key(finding_sort_key);
    Ok(ReconciliationReport { findings })
}

fn collect_fixed_layout(vault: &Vault, findings: &mut Vec<ReconciliationFinding>) -> Result<()> {
    visit_fixed_layout_extras(vault, |parent, name, path| {
        let finding = match classify_fixed_layout_extra(parent, name)? {
            FixedLayoutClassification::InvalidLayout => ReconciliationFinding::InvalidLayout {
                path: encode_relative_path(path),
            },
            FixedLayoutClassification::NonRegular => ReconciliationFinding::NonRegularEntry {
                path: encode_relative_path(path),
            },
            FixedLayoutClassification::PermissionViolation => {
                ReconciliationFinding::PermissionViolation {
                    path: encode_relative_path(path),
                }
            }
        };
        findings.push(finding);
        Ok(())
    })
}

pub(super) fn validate_fixed_layout(vault: &Vault) -> Result<()> {
    let mut invalid = false;
    visit_fixed_layout_extras(vault, |parent, name, _| {
        let _ = classify_fixed_layout_extra(parent, name)?;
        invalid = true;
        Ok(())
    })?;
    if invalid {
        Err(HeleosError::PolicyDenied)
    } else {
        Ok(())
    }
}

fn visit_fixed_layout_extras(
    vault: &Vault,
    mut visitor: impl FnMut(&Dir, &OsStr, &Path) -> Result<()>,
) -> Result<()> {
    for entry in vault.root_dir.entries().map_err(HeleosError::Io)? {
        let name = entry.map_err(HeleosError::Io)?.file_name();
        if name != OsStr::new(super::OBJECTS_NAME)
            && name != OsStr::new(super::STAGING_NAME)
            && name != OsStr::new(super::LOCK_NAME)
        {
            visitor(&vault.root_dir, &name, &relative(&[&name]))?;
        }
    }
    for entry in vault.objects_dir.entries().map_err(HeleosError::Io)? {
        let name = entry.map_err(HeleosError::Io)?.file_name();
        if name != OsStr::new(super::SHA256_NAME) {
            visitor(
                &vault.objects_dir,
                &name,
                &relative(&[OsStr::new(super::OBJECTS_NAME), &name]),
            )?;
        }
    }
    Ok(())
}

enum FixedLayoutClassification {
    InvalidLayout,
    NonRegular,
    PermissionViolation,
}

fn classify_fixed_layout_extra(parent: &Dir, name: &OsStr) -> Result<FixedLayoutClassification> {
    match classify_and_open_entry(parent, name)? {
        ClassifiedEntry::Regular(_) | ClassifiedEntry::Directory => {
            Ok(FixedLayoutClassification::InvalidLayout)
        }
        ClassifiedEntry::NonRegular => Ok(FixedLayoutClassification::NonRegular),
        ClassifiedEntry::PermissionViolation { .. } => {
            Ok(FixedLayoutClassification::PermissionViolation)
        }
    }
}

fn collect_staging(vault: &Vault, findings: &mut Vec<ReconciliationFinding>) -> Result<()> {
    for entry in vault.staging_dir.entries().map_err(HeleosError::Io)? {
        let entry = entry.map_err(HeleosError::Io)?;
        let name = entry.file_name();
        let relative = relative(&[OsStr::new(super::STAGING_NAME), &name]);
        let encoded = encode_relative_path(&relative);
        findings.push(ReconciliationFinding::StagingPartial {
            path: encoded.clone(),
        });
        match classify_and_open_entry(&vault.staging_dir, &name)? {
            ClassifiedEntry::Regular(file) => {
                let metadata = file.metadata().map_err(HeleosError::Io)?;
                if metadata.nlink() != 1 {
                    findings.push(ReconciliationFinding::UnexpectedLinkCount {
                        path: encoded,
                        link_count: metadata.nlink(),
                    });
                }
            }
            ClassifiedEntry::Directory | ClassifiedEntry::NonRegular => {
                findings.push(ReconciliationFinding::NonRegularEntry { path: encoded });
            }
            ClassifiedEntry::PermissionViolation { .. } => {
                findings.push(ReconciliationFinding::PermissionViolation { path: encoded });
            }
        }
    }
    Ok(())
}

fn collect_objects(
    vault: &Vault,
    inventory: &VaultInventory,
    observed: &mut std::collections::BTreeMap<Sha256Digest, Option<u64>>,
    findings: &mut Vec<ReconciliationFinding>,
) -> Result<()> {
    for first_entry in vault.sha256_dir.entries().map_err(HeleosError::Io)? {
        let first_entry = first_entry.map_err(HeleosError::Io)?;
        let first = first_entry.file_name();
        let first_path = relative(&[
            OsStr::new(super::OBJECTS_NAME),
            OsStr::new(super::SHA256_NAME),
            &first,
        ]);
        if !is_lower_hex_component(&first, 2) {
            findings.push(ReconciliationFinding::InvalidLayout {
                path: encode_relative_path(&first_path),
            });
            continue;
        }
        let first_dir = match classify_directory(&vault.sha256_dir, &first, &first_path, findings)?
        {
            Some(directory) => directory,
            None => continue,
        };
        for second_entry in first_dir.entries().map_err(HeleosError::Io)? {
            let second_entry = second_entry.map_err(HeleosError::Io)?;
            let second = second_entry.file_name();
            let second_path = relative(&[
                OsStr::new(super::OBJECTS_NAME),
                OsStr::new(super::SHA256_NAME),
                &first,
                &second,
            ]);
            if !is_lower_hex_component(&second, 2) {
                findings.push(ReconciliationFinding::InvalidLayout {
                    path: encode_relative_path(&second_path),
                });
                continue;
            }
            let second_dir = match classify_directory(&first_dir, &second, &second_path, findings)?
            {
                Some(directory) => directory,
                None => continue,
            };
            for object_entry in second_dir.entries().map_err(HeleosError::Io)? {
                let object_entry = object_entry.map_err(HeleosError::Io)?;
                let name = object_entry.file_name();
                let object_path = relative(&[
                    OsStr::new(super::OBJECTS_NAME),
                    OsStr::new(super::SHA256_NAME),
                    &first,
                    &second,
                    &name,
                ]);
                let Some(name_text) = name.to_str() else {
                    findings.push(ReconciliationFinding::InvalidLayout {
                        path: encode_relative_path(&object_path),
                    });
                    continue;
                };
                let Ok(digest) = name_text.parse::<Sha256Digest>() else {
                    findings.push(ReconciliationFinding::InvalidLayout {
                        path: encode_relative_path(&object_path),
                    });
                    continue;
                };
                let (expected_first, expected_second, expected_name) = digest_components(digest);
                if first != OsStr::new(&expected_first)
                    || second != OsStr::new(&expected_second)
                    || name != OsStr::new(&expected_name)
                {
                    findings.push(ReconciliationFinding::InvalidLayout {
                        path: encode_relative_path(&object_path),
                    });
                    continue;
                }
                let verification = vault.verify_locked(digest)?;
                let actual_length = verification_length(&verification);
                observed.insert(digest, actual_length);
                match &verification {
                    VaultVerification::Verified { .. } => {
                        if !inventory.entries.contains_key(&digest) {
                            findings
                                .push(ReconciliationFinding::UnreferencedObject { verification });
                        }
                    }
                    VaultVerification::Corrupt { .. } => {
                        findings.push(ReconciliationFinding::CorruptObject { verification });
                    }
                    VaultVerification::NonRegular { .. } => {
                        findings.push(ReconciliationFinding::NonRegularEntry {
                            path: encode_relative_path(&object_path),
                        });
                    }
                    VaultVerification::PermissionViolation { .. } => {
                        findings.push(ReconciliationFinding::PermissionViolation {
                            path: encode_relative_path(&object_path),
                        });
                    }
                    VaultVerification::UnexpectedLinkCount { link_count, .. } => {
                        findings.push(ReconciliationFinding::UnexpectedLinkCount {
                            path: encode_relative_path(&object_path),
                            link_count: *link_count,
                        });
                    }
                    VaultVerification::Missing { .. } => {
                        observed.remove(&digest);
                    }
                }
            }
        }
    }
    Ok(())
}

fn classify_directory(
    parent: &Dir,
    name: &OsStr,
    relative_path: &Path,
    findings: &mut Vec<ReconciliationFinding>,
) -> Result<Option<Dir>> {
    match open_directory(parent, name, false) {
        Ok(opened) => Ok(Some(opened.dir)),
        Err(HeleosError::PolicyDenied) => {
            classify_failed_directory(parent, name, relative_path, findings)?;
            Ok(None)
        }
        Err(HeleosError::Io(error)) if error.kind() == std::io::ErrorKind::PermissionDenied => {
            classify_failed_directory(parent, name, relative_path, findings)?;
            Ok(None)
        }
        Err(HeleosError::Io(error)) if error.kind() == std::io::ErrorKind::NotADirectory => {
            findings.push(ReconciliationFinding::NonRegularEntry {
                path: encode_relative_path(relative_path),
            });
            Ok(None)
        }
        Err(error) => Err(error),
    }
}

fn classify_failed_directory(
    parent: &Dir,
    name: &OsStr,
    relative_path: &Path,
    findings: &mut Vec<ReconciliationFinding>,
) -> Result<()> {
    let path = encode_relative_path(relative_path);
    match classify_and_open_entry(parent, name)? {
        ClassifiedEntry::Directory | ClassifiedEntry::PermissionViolation { .. } => {
            findings.push(ReconciliationFinding::PermissionViolation { path });
        }
        ClassifiedEntry::Regular(_) | ClassifiedEntry::NonRegular => {
            findings.push(ReconciliationFinding::NonRegularEntry { path });
        }
    }
    Ok(())
}

#[cfg(test)]
fn permission_violation_from_check(result: Result<()>) -> Result<bool> {
    match result {
        Ok(()) => Ok(false),
        Err(HeleosError::PolicyDenied) => Ok(true),
        Err(error) => Err(error),
    }
}

#[cfg(test)]
fn known_nonregular_open_error(error: HeleosError) -> Result<bool> {
    match error {
        HeleosError::PolicyDenied => Ok(true),
        HeleosError::Io(error) if error.kind() == std::io::ErrorKind::NotADirectory => Ok(true),
        error => Err(error),
    }
}

fn verification_length(verification: &VaultVerification) -> Option<u64> {
    match verification {
        VaultVerification::Verified { byte_length, .. }
        | VaultVerification::UnexpectedLinkCount { byte_length, .. } => Some(*byte_length),
        VaultVerification::Corrupt {
            actual_byte_length, ..
        }
        | VaultVerification::PermissionViolation {
            byte_length: actual_byte_length,
            ..
        } => *actual_byte_length,
        VaultVerification::Missing { .. } | VaultVerification::NonRegular { .. } => None,
    }
}

fn relative(components: &[&OsStr]) -> PathBuf {
    let mut path = PathBuf::new();
    for component in components {
        path.push(component);
    }
    path
}

pub(super) fn encode_relative_path(path: &Path) -> EncodedVaultPath {
    if let Some(text) = path.to_str() {
        return EncodedVaultPath {
            encoding: "utf8".to_owned(),
            value: encode_utf8_relative_path(text),
        };
    }
    #[cfg(unix)]
    {
        use std::os::unix::ffi::OsStrExt;

        return EncodedVaultPath {
            encoding: "unix_bytes_hex".to_owned(),
            value: hex_bytes(path.as_os_str().as_bytes()),
        };
    }
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;

        let mut value = String::new();
        for unit in path.as_os_str().encode_wide() {
            use std::fmt::Write as _;
            let _ = write!(value, "{unit:04x}");
        }
        return EncodedVaultPath {
            encoding: "windows_utf16_units_hex".to_owned(),
            value,
        };
    }
    #[allow(unreachable_code)]
    EncodedVaultPath {
        encoding: "utf8".to_owned(),
        value: path.to_string_lossy().into_owned(),
    }
}

fn encode_utf8_relative_path(path: &str) -> String {
    #[cfg(windows)]
    {
        path.replace('\\', "/")
    }
    #[cfg(not(windows))]
    {
        path.to_owned()
    }
}

#[cfg(unix)]
fn hex_bytes(bytes: &[u8]) -> String {
    let mut value = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        use std::fmt::Write as _;
        let _ = write!(value, "{byte:02x}");
    }
    value
}

fn finding_sort_key(finding: &ReconciliationFinding) -> (u8, String, String, String, u64) {
    match finding {
        ReconciliationFinding::StagingPartial { path } => path_key(0, path, 0),
        ReconciliationFinding::UnreferencedObject { verification } => {
            verification_key(1, verification)
        }
        ReconciliationFinding::CorruptObject { verification } => verification_key(2, verification),
        ReconciliationFinding::NonRegularEntry { path } => path_key(3, path, 0),
        ReconciliationFinding::PermissionViolation { path } => path_key(4, path, 0),
        ReconciliationFinding::UnexpectedLinkCount { path, .. } => path_key(5, path, 0),
        ReconciliationFinding::InvalidLayout { path } => path_key(6, path, 0),
        ReconciliationFinding::MissingReference { inventory } => (
            7,
            inventory.digest.to_string(),
            String::new(),
            String::new(),
            inventory.expected_byte_length,
        ),
        ReconciliationFinding::InventoryKeyMismatch { inventory, .. } => (
            8,
            inventory.digest.to_string(),
            String::new(),
            String::new(),
            inventory.expected_byte_length,
        ),
        ReconciliationFinding::InventoryLengthMismatch { inventory, .. } => (
            9,
            inventory.digest.to_string(),
            String::new(),
            String::new(),
            inventory.expected_byte_length,
        ),
    }
}

fn path_key(
    ordinal: u8,
    path: &EncodedVaultPath,
    length: u64,
) -> (u8, String, String, String, u64) {
    (
        ordinal,
        String::new(),
        path.encoding.clone(),
        path.value.clone(),
        length,
    )
}

fn verification_key(
    ordinal: u8,
    verification: &VaultVerification,
) -> (u8, String, String, String, u64) {
    let (digest, length) = match verification {
        VaultVerification::Verified {
            digest,
            byte_length,
            ..
        }
        | VaultVerification::UnexpectedLinkCount {
            digest,
            byte_length,
            ..
        } => (*digest, *byte_length),
        VaultVerification::Missing { digest, .. }
        | VaultVerification::NonRegular { digest, .. }
        | VaultVerification::PermissionViolation { digest, .. } => (*digest, 0),
        VaultVerification::Corrupt {
            expected_digest,
            actual_byte_length,
            ..
        } => (*expected_digest, actual_byte_length.unwrap_or(0)),
    };
    (
        ordinal,
        digest.to_string(),
        String::new(),
        String::new(),
        length,
    )
}

#[cfg(test)]
pub(super) fn encode_relative_os_path_for_test(path: &Path) -> EncodedVaultPath {
    encode_relative_path(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unexpected_permission_and_open_io_are_propagated_not_reported_as_findings() {
        assert!(matches!(
            permission_violation_from_check(Err(HeleosError::Io(std::io::Error::other(
                "injected permission clone failure",
            )))),
            Err(HeleosError::Io(_))
        ));
        assert!(matches!(
            known_nonregular_open_error(HeleosError::Io(std::io::Error::other(
                "injected entry open failure",
            ))),
            Err(HeleosError::Io(_))
        ));
    }
}
