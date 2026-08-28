use std::fs;
use std::path::Path;

use crate::{HeleosError, Result};

fn checked_metadata(path: &Path) -> Result<fs::Metadata> {
    let metadata = fs::symlink_metadata(path).map_err(HeleosError::Io)?;
    if metadata.file_type().is_symlink() || !(metadata.is_file() || metadata.is_dir()) {
        return Err(HeleosError::PolicyDenied);
    }
    reject_windows_reparse_point(&metadata)?;
    Ok(metadata)
}

#[cfg(windows)]
fn reject_windows_reparse_point(metadata: &fs::Metadata) -> Result<()> {
    use std::os::windows::fs::MetadataExt;

    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;
    if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

#[cfg(not(windows))]
const fn reject_windows_reparse_point(_: &fs::Metadata) -> Result<()> {
    Ok(())
}

#[cfg(unix)]
pub fn apply_private_permissions(path: &Path) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;

    let metadata = checked_metadata(path)?;
    let mode = if metadata.is_dir() { 0o700 } else { 0o600 };
    fs::set_permissions(path, fs::Permissions::from_mode(mode)).map_err(HeleosError::Io)?;
    verify_private_permissions(path)
}

#[cfg(unix)]
pub fn verify_private_permissions(path: &Path) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;

    let metadata = checked_metadata(path)?;
    let expected = if metadata.is_dir() { 0o700 } else { 0o600 };
    if metadata.permissions().mode() & 0o777 != expected {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

#[cfg(windows)]
pub fn apply_private_permissions(path: &Path) -> Result<()> {
    use windows_acl::acl::{ACL, AceType};
    use windows_acl::helper::{current_user, name_to_sid, string_to_sid};

    const FILE_ALL_ACCESS: u32 = 0x001f_01ff;

    checked_metadata(path)?;
    let path_text = path.to_str().ok_or(HeleosError::PolicyDenied)?;
    let user_name = current_user().ok_or(HeleosError::PolicyDenied)?;
    let user_sid = name_to_sid(&user_name, None).map_err(|_| HeleosError::PolicyDenied)?;
    let system_sid = string_to_sid("S-1-5-18").map_err(|_| HeleosError::PolicyDenied)?;
    let allowed = [sid_string(&user_sid)?, "S-1-5-18".to_owned()];

    let mut acl = ACL::from_file_path(path_text, false).map_err(|_| HeleosError::PolicyDenied)?;
    acl.allow(user_sid.as_ptr() as *mut _, false, FILE_ALL_ACCESS)
        .map_err(|_| HeleosError::PolicyDenied)?;
    acl.allow(system_sid.as_ptr() as *mut _, false, FILE_ALL_ACCESS)
        .map_err(|_| HeleosError::PolicyDenied)?;

    for entry in acl.all().map_err(|_| HeleosError::PolicyDenied)? {
        if !allowed.contains(&entry.string_sid)
            || entry.entry_type != AceType::AccessAllow
            || entry.flags != 0
            || entry.mask != FILE_ALL_ACCESS
        {
            let sid = string_to_sid(&entry.string_sid).map_err(|_| HeleosError::PolicyDenied)?;
            acl.remove(
                sid.as_ptr() as *mut _,
                Some(entry.entry_type),
                Some(entry.flags),
            )
            .map_err(|_| HeleosError::PolicyDenied)?;
        }
    }

    acl.allow(user_sid.as_ptr() as *mut _, false, FILE_ALL_ACCESS)
        .map_err(|_| HeleosError::PolicyDenied)?;
    acl.allow(system_sid.as_ptr() as *mut _, false, FILE_ALL_ACCESS)
        .map_err(|_| HeleosError::PolicyDenied)?;
    verify_private_permissions(path)
}

#[cfg(windows)]
pub fn verify_private_permissions(path: &Path) -> Result<()> {
    use std::os::windows::io::AsRawHandle;
    use windows_acl::acl::{ACL, AceType};
    use windows_acl::helper::{current_user, name_to_sid};

    const FILE_ALL_ACCESS: u32 = 0x001f_01ff;

    checked_metadata(path)?;
    let retained_file = open_windows_permission_handle(path)?;
    let user_name = current_user().ok_or(HeleosError::PolicyDenied)?;
    let user_sid = name_to_sid(&user_name, None).map_err(|_| HeleosError::PolicyDenied)?;
    let allowed = [sid_string(&user_sid)?, "S-1-5-18".to_owned()];
    let entries = ACL::from_file_handle(retained_file.as_raw_handle() as *mut _, false)
        .map_err(|_| HeleosError::PolicyDenied)?
        .all()
        .map_err(|_| HeleosError::PolicyDenied)?;

    if entries.len() != allowed.len() {
        return Err(HeleosError::PolicyDenied);
    }
    for expected_sid in &allowed {
        let mut matching = entries
            .iter()
            .filter(|entry| entry.string_sid == *expected_sid);
        let Some(entry) = matching.next() else {
            return Err(HeleosError::PolicyDenied);
        };
        if matching.next().is_some() {
            return Err(HeleosError::PolicyDenied);
        }
        if entry.entry_type != AceType::AccessAllow
            || entry.flags != 0
            || entry.mask != FILE_ALL_ACCESS
        {
            return Err(HeleosError::PolicyDenied);
        }
    }
    verify_windows_dacl_is_protected(&retained_file)?;
    Ok(())
}

#[cfg(windows)]
fn open_windows_permission_handle(path: &Path) -> Result<fs::File> {
    use std::os::windows::fs::OpenOptionsExt;

    const FILE_SHARE_READ: u32 = 0x0000_0001;
    const FILE_SHARE_WRITE: u32 = 0x0000_0002;
    const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;
    let mut options = fs::OpenOptions::new();
    options
        .read(true)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS);
    options.open(path).map_err(HeleosError::Io)
}

#[cfg(windows)]
fn verify_windows_dacl_is_protected(file: &fs::File) -> Result<()> {
    use std::panic::{AssertUnwindSafe, catch_unwind};
    use windows_permissions::constants::{SeObjectType, SecurityInformation};
    use windows_permissions::wrappers::{
        ConvertSecurityDescriptorToStringSecurityDescriptor, GetSecurityInfo,
    };

    let descriptor = catch_unwind(AssertUnwindSafe(|| {
        GetSecurityInfo(
            file,
            SeObjectType::SE_FILE_OBJECT,
            SecurityInformation::Dacl,
        )
    }))
    .map_err(|_| HeleosError::PolicyDenied)?
    .map_err(|_| HeleosError::PolicyDenied)?;
    let sddl = catch_unwind(AssertUnwindSafe(|| {
        ConvertSecurityDescriptorToStringSecurityDescriptor(&descriptor, SecurityInformation::Dacl)
    }))
    .map_err(|_| HeleosError::PolicyDenied)?
    .map_err(|_| HeleosError::PolicyDenied)?;
    let sddl = sddl.to_str().ok_or(HeleosError::PolicyDenied)?;
    if !sddl_dacl_has_protected_control_flag(sddl) {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

#[cfg(windows)]
fn sddl_dacl_has_protected_control_flag(sddl: &str) -> bool {
    let Some(dacl) = sddl.strip_prefix("D:") else {
        return false;
    };
    let Some((mut flags, aces)) = dacl.split_once('(') else {
        return false;
    };
    if aces.is_empty() {
        return false;
    }
    let mut protected = false;
    while !flags.is_empty() {
        if let Some(rest) = flags.strip_prefix('P') {
            protected = true;
            flags = rest;
        } else if let Some(rest) = flags.strip_prefix("AR") {
            flags = rest;
        } else if let Some(rest) = flags.strip_prefix("AI") {
            flags = rest;
        } else {
            return false;
        }
    }
    protected
}

#[cfg(windows)]
fn sid_string(sid: &[u8]) -> Result<String> {
    use windows_acl::helper::sid_to_string;

    sid_to_string(sid.as_ptr() as *mut _).map_err(|_| HeleosError::PolicyDenied)
}

#[cfg(not(any(unix, windows)))]
pub fn apply_private_permissions(path: &Path) -> Result<()> {
    checked_metadata(path)?;
    Err(HeleosError::PolicyDenied)
}

#[cfg(not(any(unix, windows)))]
pub fn verify_private_permissions(path: &Path) -> Result<()> {
    checked_metadata(path)?;
    Err(HeleosError::PolicyDenied)
}
