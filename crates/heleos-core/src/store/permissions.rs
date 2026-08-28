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
        if !allowed.contains(&entry.string_sid) {
            let sid = string_to_sid(&entry.string_sid).map_err(|_| HeleosError::PolicyDenied)?;
            acl.remove(sid.as_ptr() as *mut _, None, None)
                .map_err(|_| HeleosError::PolicyDenied)?;
        } else if entry.entry_type != AceType::AccessAllow {
            let sid = string_to_sid(&entry.string_sid).map_err(|_| HeleosError::PolicyDenied)?;
            acl.remove(sid.as_ptr() as *mut _, Some(entry.entry_type), None)
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
    use windows_acl::acl::{ACL, AceType};
    use windows_acl::helper::{current_user, name_to_sid};

    checked_metadata(path)?;
    let path_text = path.to_str().ok_or(HeleosError::PolicyDenied)?;
    let user_name = current_user().ok_or(HeleosError::PolicyDenied)?;
    let user_sid = name_to_sid(&user_name, None).map_err(|_| HeleosError::PolicyDenied)?;
    let allowed = [sid_string(&user_sid)?, "S-1-5-18".to_owned()];
    let entries = ACL::from_file_path(path_text, false)
        .map_err(|_| HeleosError::PolicyDenied)?
        .all()
        .map_err(|_| HeleosError::PolicyDenied)?;

    for expected_sid in &allowed {
        if !entries.iter().any(|entry| {
            entry.string_sid == *expected_sid && entry.entry_type == AceType::AccessAllow
        }) {
            return Err(HeleosError::PolicyDenied);
        }
    }
    if entries.iter().any(|entry| {
        !allowed.contains(&entry.string_sid) || entry.entry_type != AceType::AccessAllow
    }) {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
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
