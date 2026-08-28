use std::fs::{self, File, OpenOptions};
use std::path::Path;

use crate::{HeleosError, Result};

fn checked_metadata(path: &Path) -> Result<fs::Metadata> {
    let metadata = fs::symlink_metadata(path).map_err(HeleosError::Io)?;
    validate_permission_metadata(&metadata)?;
    Ok(metadata)
}

fn validate_permission_metadata(metadata: &fs::Metadata) -> Result<()> {
    if metadata.file_type().is_symlink() || !(metadata.is_file() || metadata.is_dir()) {
        return Err(HeleosError::PolicyDenied);
    }
    reject_windows_reparse_point(metadata)
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

pub fn apply_private_permissions(path: &Path) -> Result<()> {
    let metadata = checked_metadata(path)?;
    let mut file = open_permission_handle(path, PermissionAccess::Apply, metadata.is_dir())?;
    apply_private_permissions_to_handle(&mut file)
}

pub fn verify_private_permissions(path: &Path) -> Result<()> {
    let metadata = checked_metadata(path)?;
    let file = open_permission_handle(path, PermissionAccess::Verify, metadata.is_dir())?;
    verify_private_permissions_on_handle(&file)
}

#[derive(Clone, Copy)]
enum PermissionAccess {
    Apply,
    Verify,
}

#[cfg(unix)]
fn open_permission_handle(path: &Path, _: PermissionAccess, _: bool) -> Result<File> {
    use std::os::unix::fs::OpenOptionsExt;

    let mut options = OpenOptions::new();
    options
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    options.open(path).map_err(HeleosError::Io)
}

#[cfg(windows)]
fn windows_permission_access_mask(access: PermissionAccess) -> u32 {
    use windows_permissions::constants::AccessRights;

    const FILE_READ_ATTRIBUTES: u32 = 0x0000_0080;

    let mut rights = AccessRights::ReadControl.bits() | FILE_READ_ATTRIBUTES;
    if matches!(access, PermissionAccess::Apply) {
        rights |= AccessRights::WriteDac.bits();
    }
    rights
}

#[cfg(windows)]
fn open_permission_handle(
    path: &Path,
    access: PermissionAccess,
    is_directory: bool,
) -> Result<File> {
    use std::os::windows::fs::OpenOptionsExt;

    const FILE_SHARE_READ: u32 = 0x0000_0001;
    const FILE_SHARE_WRITE: u32 = 0x0000_0002;
    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;

    let flags = FILE_FLAG_OPEN_REPARSE_POINT
        | if is_directory {
            FILE_FLAG_BACKUP_SEMANTICS
        } else {
            0
        };
    let mut options = OpenOptions::new();
    options
        .access_mode(windows_permission_access_mask(access))
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(flags);
    options.open(path).map_err(HeleosError::Io)
}

#[cfg(not(any(unix, windows)))]
fn open_permission_handle(path: &Path, _: PermissionAccess, _: bool) -> Result<File> {
    OpenOptions::new()
        .read(true)
        .open(path)
        .map_err(HeleosError::Io)
}

#[cfg(unix)]
pub(super) fn apply_private_permissions_to_handle(file: &mut File) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;

    let metadata = file.metadata().map_err(HeleosError::Io)?;
    validate_permission_metadata(&metadata)?;
    let expected = if metadata.is_dir() { 0o700 } else { 0o600 };
    if metadata.permissions().mode() & 0o777 != expected {
        file.set_permissions(fs::Permissions::from_mode(expected))
            .map_err(HeleosError::Io)?;
    }
    verify_private_permissions_on_handle(file)
}

#[cfg(unix)]
pub(super) fn verify_private_permissions_on_handle(file: &File) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;

    let metadata = file.metadata().map_err(HeleosError::Io)?;
    validate_permission_metadata(&metadata)?;
    let expected = if metadata.is_dir() { 0o700 } else { 0o600 };
    if metadata.permissions().mode() & 0o777 != expected {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

#[cfg(windows)]
pub(super) fn apply_private_permissions_to_handle(file: &mut File) -> Result<()> {
    use windows_permissions::constants::{SeObjectType, SecurityInformation};
    use windows_permissions::wrappers::{
        ConvertStringSecurityDescriptorToSecurityDescriptor, GetSecurityDescriptorDacl,
        SetSecurityInfo,
    };

    let metadata = file.metadata().map_err(HeleosError::Io)?;
    validate_permission_metadata(&metadata)?;
    let allowed = allowed_windows_sids()?;
    let sddl = exact_private_dacl_sddl(&allowed);
    let descriptor = ConvertStringSecurityDescriptorToSecurityDescriptor(&sddl)
        .map_err(|_| HeleosError::PolicyDenied)?;
    let dacl = match GetSecurityDescriptorDacl(descriptor.as_ref())
        .map_err(|_| HeleosError::PolicyDenied)?
    {
        Some(dacl) => dacl,
        None => return Err(HeleosError::PolicyDenied),
    };
    SetSecurityInfo(
        file,
        SeObjectType::SE_FILE_OBJECT,
        SecurityInformation::Dacl | SecurityInformation::ProtectedDacl,
        None,
        None,
        Some(dacl),
        None,
    )
    .map_err(|_| HeleosError::PolicyDenied)?;
    verify_windows_permissions_for_allowed_sids(file, &allowed)
}

#[cfg(windows)]
pub(super) fn verify_private_permissions_on_handle(file: &File) -> Result<()> {
    let metadata = file.metadata().map_err(HeleosError::Io)?;
    validate_permission_metadata(&metadata)?;
    let allowed = allowed_windows_sids()?;
    verify_windows_permissions_for_allowed_sids(file, &allowed)
}

#[cfg(windows)]
fn allowed_windows_sids() -> Result<Vec<String>> {
    let process_sid = stellar_agent_windows_identity::current_user_sid_string()
        .map_err(|_| HeleosError::PolicyDenied)?;
    allowed_windows_sids_for_process_sid(&process_sid)
}

#[cfg(windows)]
fn allowed_windows_sids_for_process_sid(process_sid: &str) -> Result<Vec<String>> {
    const SYSTEM_SID: &str = "S-1-5-18";

    let process_sid = canonicalize_windows_sid(process_sid)?;
    let mut allowed = vec![process_sid];
    if allowed[0] != SYSTEM_SID {
        allowed.push(SYSTEM_SID.to_owned());
    }
    Ok(allowed)
}

#[cfg(windows)]
fn canonicalize_windows_sid(input: &str) -> Result<String> {
    use std::ffi::OsStr;
    use windows_permissions::wrappers::{ConvertSidToStringSid, ConvertStringSidToSid};

    let parsed = ConvertStringSidToSid(input).map_err(|_| HeleosError::PolicyDenied)?;
    let canonical =
        ConvertSidToStringSid(parsed.as_ref()).map_err(|_| HeleosError::PolicyDenied)?;
    if canonical.as_os_str() != OsStr::new(input) {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(input.to_owned())
}

#[cfg(windows)]
fn exact_private_dacl_sddl(allowed: &[String]) -> String {
    let mut sddl = String::from("D:P");
    for sid in allowed {
        sddl.push_str("(A;;FA;;;");
        sddl.push_str(sid);
        sddl.push(')');
    }
    sddl
}

#[cfg(windows)]
fn verify_windows_permissions_for_allowed_sids(file: &File, allowed: &[String]) -> Result<()> {
    use std::os::windows::io::AsRawHandle;
    use windows_acl::acl::{ACL, AceType};

    const FILE_ALL_ACCESS: u32 = 0x001f_01ff;

    verify_windows_dacl_is_protected(file)?;
    let acl = ACL::from_file_handle(file.as_raw_handle() as *mut _, false)
        .map_err(|_| HeleosError::PolicyDenied)?;
    let entries = acl.all().map_err(|_| HeleosError::PolicyDenied)?;
    if entries.len() != allowed.len() {
        return Err(HeleosError::PolicyDenied);
    }
    for expected_sid in allowed {
        let mut matching = entries
            .iter()
            .filter(|entry| entry.string_sid == *expected_sid);
        let Some(entry) = matching.next() else {
            return Err(HeleosError::PolicyDenied);
        };
        if matching.next().is_some()
            || entry.entry_type != AceType::AccessAllow
            || entry.flags != 0
            || entry.mask != FILE_ALL_ACCESS
        {
            return Err(HeleosError::PolicyDenied);
        }
    }
    Ok(())
}

#[cfg(windows)]
fn verify_windows_dacl_is_protected(file: &File) -> Result<()> {
    use windows_permissions::constants::{SeObjectType, SecurityInformation};
    use windows_permissions::wrappers::{
        ConvertSecurityDescriptorToStringSecurityDescriptor, GetSecurityInfo,
    };

    let descriptor = GetSecurityInfo(
        file,
        SeObjectType::SE_FILE_OBJECT,
        SecurityInformation::Dacl,
    )
    .map_err(|_| HeleosError::PolicyDenied)?;
    let sddl =
        ConvertSecurityDescriptorToStringSecurityDescriptor(&descriptor, SecurityInformation::Dacl)
            .map_err(|_| HeleosError::PolicyDenied)?;
    let sddl = sddl.to_str().ok_or(HeleosError::PolicyDenied)?;
    if !windows_dacl_policy_accepts_sddl(true, sddl) {
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
            if protected {
                return false;
            }
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
fn windows_dacl_policy_accepts_sddl(dacl_present: bool, sddl: &str) -> bool {
    dacl_present && sddl_dacl_has_protected_control_flag(sddl)
}

#[cfg(not(any(unix, windows)))]
pub(super) fn apply_private_permissions_to_handle(file: &mut File) -> Result<()> {
    let metadata = file.metadata().map_err(HeleosError::Io)?;
    validate_permission_metadata(&metadata)?;
    Err(HeleosError::PolicyDenied)
}

#[cfg(not(any(unix, windows)))]
pub(super) fn verify_private_permissions_on_handle(file: &File) -> Result<()> {
    let metadata = file.metadata().map_err(HeleosError::Io)?;
    validate_permission_metadata(&metadata)?;
    Err(HeleosError::PolicyDenied)
}

#[cfg(all(test, windows))]
mod windows_tests {
    use super::*;
    use windows_permissions::constants::{SeObjectType, SecurityInformation};
    use windows_permissions::wrappers::{
        ConvertStringSecurityDescriptorToSecurityDescriptor, GetSecurityDescriptorDacl,
        SetSecurityInfo,
    };

    #[test]
    fn standalone_permission_handles_request_exact_metadata_and_policy_access() {
        assert_eq!(
            windows_permission_access_mask(PermissionAccess::Verify),
            0x0002_0080
        );
        assert_eq!(
            windows_permission_access_mask(PermissionAccess::Apply),
            0x0006_0080
        );
    }

    fn fixture_file(label: &str) -> (tempfile::TempDir, File) {
        let root = tempfile::Builder::new()
            .prefix(label)
            .tempdir()
            .expect("create Windows permission fixture directory");
        apply_private_permissions(root.path())
            .expect("harden Windows permission fixture directory");
        let path = root.path().join("fixture");
        fs::write(&path, b"fixture").expect("write Windows permission fixture");
        let file = open_permission_handle(&path, PermissionAccess::Apply, false)
            .expect("open Windows permission fixture handle");
        (root, file)
    }

    fn install_nonnull_fixture_dacl(file: &mut File, sddl: &str, protected: bool) {
        let descriptor = ConvertStringSecurityDescriptorToSecurityDescriptor(sddl)
            .expect("parse hostile fixture DACL SDDL");
        let dacl = match GetSecurityDescriptorDacl(descriptor.as_ref())
            .expect("extract known non-null fixture DACL")
        {
            Some(dacl) => dacl,
            None => panic!("fixture SDDL did not declare a DACL"),
        };
        let control = if protected {
            SecurityInformation::ProtectedDacl
        } else {
            SecurityInformation::UnprotectedDacl
        };
        SetSecurityInfo(
            file,
            SeObjectType::SE_FILE_OBJECT,
            SecurityInformation::Dacl | control,
            None,
            None,
            Some(dacl),
            None,
        )
        .expect("install hostile fixture DACL");
    }

    fn install_null_fixture_dacl(file: &mut File) {
        SetSecurityInfo(
            file,
            SeObjectType::SE_FILE_OBJECT,
            SecurityInformation::Dacl | SecurityInformation::ProtectedDacl,
            None,
            None,
            None,
            None,
        )
        .expect("install hostile null fixture DACL");
    }

    fn append_allow_aces(sddl: &mut String, allowed: &[String]) {
        for sid in allowed {
            sddl.push_str("(A;;FA;;;");
            sddl.push_str(sid);
            sddl.push(')');
        }
    }

    #[test]
    fn canonical_sid_round_trip_accepts_domain_service_system_and_cloud_authorities() {
        let accepted = [
            "S-1-5-21-1000-2000-3000-1001",
            "S-1-5-80-1-2-3-4-5",
            "S-1-5-18",
            "S-1-12-1-111-222-333-444",
        ];
        for sid in accepted {
            let canonical = canonicalize_windows_sid(sid).expect("accept canonical Windows SID");
            assert_eq!(canonical, sid);
        }
    }

    #[test]
    fn canonical_sid_round_trip_rejects_malformed_noncanonical_and_injected_values() {
        let rejected = [
            "",
            "not-a-sid",
            "s-1-5-18",
            "S-1-5-018",
            "S-1-5-18 ",
            "S-1-5-18)(A;;FA;;;S-1-1-0",
            "BA",
        ];
        for sid in rejected {
            assert!(matches!(
                canonicalize_windows_sid(sid),
                Err(HeleosError::PolicyDenied)
            ));
        }
    }

    #[test]
    fn exact_private_sddl_is_process_first_and_deduplicates_system() {
        let domain = allowed_windows_sids_for_process_sid("S-1-5-21-1-2-3-4")
            .expect("build domain SID policy");
        assert_eq!(
            exact_private_dacl_sddl(&domain),
            "D:P(A;;FA;;;S-1-5-21-1-2-3-4)(A;;FA;;;S-1-5-18)"
        );
        let system =
            allowed_windows_sids_for_process_sid("S-1-5-18").expect("build SYSTEM SID policy");
        assert_eq!(system, ["S-1-5-18"]);
        assert_eq!(exact_private_dacl_sddl(&system), "D:P(A;;FA;;;S-1-5-18)");
    }

    #[test]
    fn permission_hardening_and_readback_use_file_and_directory_handles() {
        let root = tempfile::Builder::new()
            .prefix("heleos-windows-directory-dacl-")
            .tempdir()
            .expect("create Windows directory DACL fixture");
        apply_private_permissions(root.path()).expect("harden directory by handle");
        verify_private_permissions(root.path()).expect("verify directory by handle");

        let path = root.path().join("fixture");
        fs::write(&path, b"fixture").expect("write Windows file DACL fixture");
        apply_private_permissions(&path).expect("harden file by handle");
        verify_private_permissions(&path).expect("verify file by handle");
    }

    #[test]
    fn hardening_replaces_a_hostile_prior_dacl_with_the_fresh_exact_policy() {
        let (_root, mut file) = fixture_file("heleos-windows-hostile-prior-");
        install_nonnull_fixture_dacl(&mut file, "D:(A;;FA;;;S-1-1-0)", false);

        apply_private_permissions_to_handle(&mut file).expect("replace hostile prior DACL");
        verify_private_permissions_on_handle(&file).expect("verify fresh exact DACL");
    }

    #[test]
    fn unprotected_inherited_wrong_mask_duplicate_and_extra_aces_fail_closed() {
        let allowed = allowed_windows_sids().expect("resolve expected Windows SID policy");
        let exact = exact_private_dacl_sddl(&allowed);
        let first_sid = &allowed[0];
        let mut inherited = String::from("D:P");
        inherited.push_str("(A;ID;FA;;;");
        inherited.push_str(first_sid);
        inherited.push(')');
        for sid in allowed.iter().skip(1) {
            inherited.push_str("(A;;FA;;;");
            inherited.push_str(sid);
            inherited.push(')');
        }
        let mut wrong_mask = String::from("D:P");
        wrong_mask.push_str("(A;;FR;;;");
        wrong_mask.push_str(first_sid);
        wrong_mask.push(')');
        for sid in allowed.iter().skip(1) {
            wrong_mask.push_str("(A;;FA;;;");
            wrong_mask.push_str(sid);
            wrong_mask.push(')');
        }
        let mut duplicate = exact.clone();
        duplicate.push_str("(A;;FA;;;");
        duplicate.push_str(first_sid);
        duplicate.push(')');
        let mut extra = exact.clone();
        extra.push_str("(A;;FA;;;S-1-1-0)");

        for (sddl, protected) in [
            (exact.as_str(), false),
            (inherited.as_str(), true),
            (wrong_mask.as_str(), true),
            (duplicate.as_str(), true),
            (extra.as_str(), true),
        ] {
            let (_root, mut file) = fixture_file("heleos-windows-structural-negative-");
            install_nonnull_fixture_dacl(&mut file, sddl, protected);
            assert!(matches!(
                verify_private_permissions_on_handle(&file),
                Err(HeleosError::PolicyDenied)
            ));
        }
    }

    #[test]
    fn deny_object_and_callback_aces_fail_closed_as_extra_types() {
        let allowed = allowed_windows_sids().expect("resolve expected Windows SID policy");
        for hostile_ace in [
            "(D;;FA;;;S-1-1-0)",
            "(OA;;FA;c434c045-9b91-4504-a2a0-aea9e781ec69;;S-1-1-0)",
            "(XA;;FA;;;S-1-1-0;(TRUE))",
        ] {
            let mut sddl = String::from("D:P");
            append_allow_aces(&mut sddl, &allowed);
            sddl.push_str(hostile_ace);
            let (_root, mut file) = fixture_file("heleos-windows-type-negative-");
            install_nonnull_fixture_dacl(&mut file, &sddl, true);
            assert!(matches!(
                verify_private_permissions_on_handle(&file),
                Err(HeleosError::PolicyDenied)
            ));
        }
    }

    #[test]
    fn null_empty_and_absent_dacls_fail_closed_without_descriptor_debug_paths() {
        let (_null_root, mut null_file) = fixture_file("heleos-windows-null-dacl-");
        install_null_fixture_dacl(&mut null_file);
        assert!(matches!(
            verify_private_permissions_on_handle(&null_file),
            Err(HeleosError::PolicyDenied)
        ));

        let (_empty_root, mut empty_file) = fixture_file("heleos-windows-empty-dacl-");
        install_nonnull_fixture_dacl(&mut empty_file, "D:", true);
        assert!(matches!(
            verify_private_permissions_on_handle(&empty_file),
            Err(HeleosError::PolicyDenied)
        ));

        let exact = exact_private_dacl_sddl(
            &allowed_windows_sids().expect("resolve expected Windows SID policy"),
        );
        assert!(!windows_dacl_policy_accepts_sddl(false, &exact));
        assert!(!windows_dacl_policy_accepts_sddl(true, "D:"));
        assert!(!windows_dacl_policy_accepts_sddl(true, "D:P"));
    }
}
