pub(crate) mod ingest_repository;
mod migration;
mod permissions;
mod schema;

use std::ffi::{OsStr, OsString};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Component, Path, PathBuf};
use std::time::Duration;

use cap_fs_ext::{FollowSymlinks, OpenOptionsFollowExt, OpenOptionsMaybeDirExt};
use cap_std::fs::{Dir as CapDir, DirBuilder as CapDirBuilder, OpenOptions as CapOpenOptions};
use fs2::FileExt;
use rusqlite::config::DbConfig;
use rusqlite::functions::FunctionFlags;
use rusqlite::{Connection, OpenFlags, Transaction, TransactionBehavior};

use crate::{HeleosError, Result};

pub use migration::MigrationReport;
pub use permissions::{apply_private_permissions, verify_private_permissions};
pub(crate) use permissions::{
    apply_private_permissions_to_handle, verify_private_permissions_on_handle,
};
pub use schema::{FOUNDATION_SCHEMA_VERSION, INTEGRITY_VIOLATION_LIMIT, IntegrityReport};

const BUSY_TIMEOUT: Duration = Duration::from_secs(5);
const INTEGRITY_CHECK_SQL: &str = "PRAGMA integrity_check(129)";
const QUICK_CHECK_SQL: &str = "PRAGMA quick_check(129)";
const GIBIBYTE: u64 = 1024 * 1024 * 1024;
const MAX_SNAPSHOT_DATABASE_BYTES: u64 = 16 * GIBIBYTE;
const MAX_SNAPSHOT_WAL_BYTES: u64 = 16 * GIBIBYTE;
const MAX_SNAPSHOT_COPY_BYTES: u64 = 32 * GIBIBYTE;
const MAX_SNAPSHOT_RECOVERY_ALLOWANCE: u64 = 17 * GIBIBYTE;
const SNAPSHOT_RECOVERY_OVERHEAD: u64 = GIBIBYTE;
const MINIMUM_FREE_SPACE_RESERVE: u64 = 20 * GIBIBYTE;
const SNAPSHOT_COPY_CHUNK_BYTES: usize = 1024 * 1024;

#[derive(Debug)]
pub struct WriterLock {
    file: File,
    path: PathBuf,
    identity: FileMarker,
}

impl WriterLock {
    fn acquire(database_path: &Path) -> Result<Self> {
        let path = sidecar_path(database_path, ".writer.lock");
        let (mut file, identity) = open_checked_regular_file_without_permissions(
            &path,
            true,
            true,
            PermissionPolicy::ApplyAndVerify,
        )?;
        match FileExt::try_lock_exclusive(&file) {
            Ok(()) => {}
            Err(error) if lock_is_busy(&error) => return Err(HeleosError::WriterBusy),
            Err(error) => return Err(HeleosError::Io(error)),
        }
        enforce_permission_policy_on_handle(&mut file, PermissionPolicy::ApplyAndVerify)?;
        recheck_file_identity(&path, &file, &identity)?;
        Ok(Self {
            file,
            path,
            identity,
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    fn recheck_identity(&self) -> Result<()> {
        recheck_file_identity(&self.path, &self.file, &self.identity)
    }
}

impl Drop for WriterLock {
    fn drop(&mut self) {
        let _ = FileExt::unlock(&self.file);
    }
}

#[derive(Debug)]
struct ReaderLock {
    file: File,
    path: PathBuf,
    identity: FileMarker,
}

impl ReaderLock {
    fn acquire(database_path: &Path) -> Result<Self> {
        let path = sidecar_path(database_path, ".writer.lock");
        let (mut file, identity) = open_checked_regular_file_without_permissions(
            &path,
            false,
            false,
            PermissionPolicy::VerifyOnly,
        )?;
        match FileExt::try_lock_shared(&file) {
            Ok(()) => {}
            Err(error) if lock_is_busy(&error) => return Err(HeleosError::WriterBusy),
            Err(error) => return Err(HeleosError::Io(error)),
        }
        enforce_permission_policy_on_handle(&mut file, PermissionPolicy::VerifyOnly)?;
        recheck_file_identity(&path, &file, &identity)?;
        Ok(Self {
            file,
            path,
            identity,
        })
    }

    fn recheck_identity(&self) -> Result<()> {
        recheck_file_identity(&self.path, &self.file, &self.identity)
    }
}

impl Drop for ReaderLock {
    fn drop(&mut self) {
        let _ = FileExt::unlock(&self.file);
    }
}

pub(crate) struct ReadOnlySnapshotParent {
    path: PathBuf,
    retained: File,
    marker: FileMarker,
    policy_marker: SnapshotParentPolicyMarker,
}

impl ReadOnlySnapshotParent {
    pub(crate) fn retain_default() -> Result<Self> {
        Self::retain_path(&default_snapshot_parent_path())
    }

    fn retain_path(path: &Path) -> Result<Self> {
        let path = fs::canonicalize(path).map_err(HeleosError::Io)?;
        let (retained, marker, policy_marker) = open_retained_snapshot_parent(&path)?;
        let parent = Self {
            path,
            retained,
            marker,
            policy_marker,
        };
        parent.recheck()?;
        Ok(parent)
    }

    #[cfg(test)]
    fn retain_path_for_test(path: &Path) -> Result<Self> {
        Self::retain_path(path)
    }

    pub(crate) fn path(&self) -> &Path {
        &self.path
    }

    pub(crate) fn retained_file(&self) -> &File {
        &self.retained
    }

    pub(crate) fn recheck(&self) -> Result<()> {
        self.recheck_retained_policy()?;
        self.recheck_ambient_binding()
    }

    fn recheck_retained_policy(&self) -> Result<()> {
        recheck_snapshot_parent_retained(&self.retained, &self.marker, &self.policy_marker)
    }

    fn recheck_ambient_binding(&self) -> Result<()> {
        recheck_snapshot_parent_ambient(&self.path, &self.marker, &self.policy_marker)
    }
}

#[cfg(unix)]
#[derive(Clone, Debug, Eq, PartialEq)]
struct SnapshotParentPolicyMarker {
    effective_uid: u32,
    owner_uid: u32,
    mode: u32,
}

#[cfg(unix)]
fn snapshot_unix_parent_policy_accepts(owner_uid: u32, mode: u32, effective_uid: u32) -> bool {
    (owner_uid == effective_uid || owner_uid == 0) && (mode & 0o022 == 0 || mode & 0o1000 != 0)
}

#[cfg(unix)]
fn snapshot_parent_policy_marker(
    file: &File,
    effective_uid: u32,
) -> Result<SnapshotParentPolicyMarker> {
    use std::os::unix::fs::MetadataExt;

    let metadata =
        snapshot_open_operation(SnapshotOpenOperation::UnixParentPolicyMetadata, || {
            file.metadata().map_err(HeleosError::Io)
        })?;
    validate_directory_metadata(&metadata)?;
    let owner_uid = snapshot_open_operation(SnapshotOpenOperation::UnixParentUidRead, || {
        Ok(metadata.uid())
    })?;
    let mode = snapshot_open_operation(SnapshotOpenOperation::UnixParentModeRead, || {
        Ok(metadata.mode() & 0o7777)
    })?;
    let marker = SnapshotParentPolicyMarker {
        effective_uid,
        owner_uid,
        mode,
    };
    snapshot_open_operation(SnapshotOpenOperation::UnixParentPredicate, || {
        if snapshot_unix_parent_policy_accepts(marker.owner_uid, marker.mode, marker.effective_uid)
        {
            Ok(())
        } else {
            Err(HeleosError::PolicyDenied)
        }
    })?;
    Ok(marker)
}

#[cfg(unix)]
fn capture_snapshot_parent_policy(file: &File) -> Result<SnapshotParentPolicyMarker> {
    let effective_uid = rustix::process::geteuid().as_raw();
    snapshot_parent_policy_marker(file, effective_uid)
}

#[cfg(unix)]
fn recheck_snapshot_parent_policy(
    file: &File,
    expected: &SnapshotParentPolicyMarker,
) -> Result<()> {
    let actual = snapshot_parent_policy_marker(file, expected.effective_uid)?;
    snapshot_open_operation(SnapshotOpenOperation::UnixParentMarkerComparison, || {
        if &actual == expected {
            Ok(())
        } else {
            Err(HeleosError::PolicyDenied)
        }
    })
}

#[cfg(any(windows, test))]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum ParsedSafeAceKind {
    Allow,
    Deny,
}

#[cfg(any(windows, test))]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ParsedSafeAce {
    kind: ParsedSafeAceKind,
    flags: u8,
}

#[cfg(any(windows, test))]
fn parse_snapshot_windows_acl_flags(input: &[u8]) -> Option<()> {
    let mut cursor = 0_usize;
    let mut seen = 0_u8;
    while cursor < input.len() {
        let (flag, consumed) = match input[cursor] {
            b'P' => (0x01, 1),
            b'A' if input.get(cursor + 1) == Some(&b'R') => (0x02, 2),
            b'A' if input.get(cursor + 1) == Some(&b'I') => (0x04, 2),
            _ => return None,
        };
        if seen & flag != 0 {
            return None;
        }
        seen |= flag;
        cursor = cursor.checked_add(consumed)?;
    }
    Some(())
}

#[cfg(any(windows, test))]
fn parse_snapshot_windows_ace_flags(input: &[u8]) -> Option<u8> {
    let mut cursor = 0_usize;
    let mut flags = 0_u8;
    while cursor < input.len() {
        let value = match (input.get(cursor), input.get(cursor + 1)) {
            (Some(b'C'), Some(b'I')) => 0x02,
            (Some(b'O'), Some(b'I')) => 0x01,
            (Some(b'N'), Some(b'P')) => 0x04,
            (Some(b'I'), Some(b'O')) => 0x08,
            (Some(b'I'), Some(b'D')) => 0x10,
            _ => return None,
        };
        if flags & value != 0 {
            return None;
        }
        flags |= value;
        cursor = cursor.checked_add(2)?;
    }
    Some(flags)
}

#[cfg(any(windows, test))]
fn parse_snapshot_windows_dacl_sddl(sddl: &str) -> Result<Vec<ParsedSafeAce>> {
    const MAX_SDDL_BYTES: usize = 1_048_576;
    const MAX_ACE_RECORDS: usize = 65_535;

    let bytes = sddl.as_bytes();
    if !(2..=MAX_SDDL_BYTES).contains(&bytes.len())
        || !bytes.is_ascii()
        || !bytes.starts_with(b"D:")
    {
        return Err(HeleosError::PolicyDenied);
    }

    let mut cursor = 2_usize;
    let flags_end = bytes[cursor..]
        .iter()
        .position(|byte| *byte == b'(')
        .and_then(|offset| cursor.checked_add(offset))
        .unwrap_or(bytes.len());
    parse_snapshot_windows_acl_flags(&bytes[cursor..flags_end]).ok_or(HeleosError::PolicyDenied)?;
    cursor = flags_end;

    let mut records = Vec::new();
    while cursor < bytes.len() {
        if bytes[cursor] != b'(' {
            return Err(HeleosError::PolicyDenied);
        }
        let body_start = cursor.checked_add(1).ok_or(HeleosError::PolicyDenied)?;
        let mut body_end = body_start;
        while body_end < bytes.len() && bytes[body_end] != b')' {
            if bytes[body_end] == b'(' {
                return Err(HeleosError::PolicyDenied);
            }
            body_end = body_end.checked_add(1).ok_or(HeleosError::PolicyDenied)?;
        }
        if body_end == bytes.len() {
            return Err(HeleosError::PolicyDenied);
        }

        let fields = bytes[body_start..body_end].split(|byte| *byte == b';');
        let fields: Vec<&[u8]> = fields.collect();
        let [
            ace_type,
            ace_flags,
            rights,
            object_guid,
            inherited_object_guid,
            sid,
        ] = fields.as_slice()
        else {
            return Err(HeleosError::PolicyDenied);
        };
        let kind = match *ace_type {
            b"A" => ParsedSafeAceKind::Allow,
            b"D" => ParsedSafeAceKind::Deny,
            _ => return Err(HeleosError::PolicyDenied),
        };
        let flags = parse_snapshot_windows_ace_flags(ace_flags).ok_or(HeleosError::PolicyDenied)?;
        if !rights.iter().all(u8::is_ascii_alphanumeric)
            || !object_guid.is_empty()
            || !inherited_object_guid.is_empty()
            || sid.is_empty()
            || !sid
                .iter()
                .all(|byte| byte.is_ascii_uppercase() || byte.is_ascii_digit() || *byte == b'-')
        {
            return Err(HeleosError::PolicyDenied);
        }
        if records.len() == MAX_ACE_RECORDS {
            return Err(HeleosError::PolicyDenied);
        }
        records.push(ParsedSafeAce { kind, flags });
        cursor = body_end.checked_add(1).ok_or(HeleosError::PolicyDenied)?;
    }
    Ok(records)
}

#[cfg(windows)]
#[derive(Clone, Debug, Eq, PartialEq)]
enum SnapshotWindowsAceMarker {
    Allow {
        flags: u8,
        mask: u32,
        trustee_sid: String,
    },
    Deny,
}

#[cfg(windows)]
#[derive(Clone, Debug, Eq, PartialEq)]
struct SnapshotParentPolicyMarker {
    process_sid: String,
    owner_sid: String,
    dacl_sddl: String,
    acl_revision: u8,
    aces: Vec<SnapshotWindowsAceMarker>,
}

#[cfg(windows)]
fn canonical_snapshot_windows_sid(input: &str) -> Result<String> {
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
fn snapshot_windows_parent_owner_accepts(owner_sid: &str, process_sid: &str) -> bool {
    const SYSTEM: &str = "S-1-5-18";
    const ADMINISTRATORS: &str = "S-1-5-32-544";

    owner_sid == process_sid || owner_sid == SYSTEM || owner_sid == ADMINISTRATORS
}

#[cfg(windows)]
fn snapshot_windows_inherited_child_policy_accepts(
    process_sid: &str,
    aces: &[SnapshotWindowsAceMarker],
) -> bool {
    const SYSTEM: &str = "S-1-5-18";
    const ADMINISTRATORS: &str = "S-1-5-32-544";
    const CREATOR_OWNER: &str = "S-1-3-0";
    const INHERIT_OBJECT: u8 = 0x01;
    const INHERIT_CONTAINER: u8 = 0x02;
    const INHERIT_ONLY: u8 = 0x08;
    const PARENT_DANGEROUS: u32 = 0x100d_0040;
    const INHERITED_DANGEROUS: u32 = 0x500d_0156;

    aces.iter().all(|ace| {
        let SnapshotWindowsAceMarker::Allow {
            flags,
            mask,
            trustee_sid,
        } = ace
        else {
            return true;
        };
        let inheritable = flags & (INHERIT_CONTAINER | INHERIT_OBJECT) != 0;
        let inherit_only = flags & INHERIT_ONLY != 0;
        let base_trusted =
            trustee_sid == process_sid || trustee_sid == SYSTEM || trustee_sid == ADMINISTRATORS;
        let creator_owner_narrow = trustee_sid == CREATOR_OWNER && inherit_only && inheritable;
        if base_trusted || creator_owner_narrow {
            return true;
        }
        let dangerous_on_parent = !inherit_only && mask & PARENT_DANGEROUS != 0;
        let dangerous_on_child = inheritable && mask & INHERITED_DANGEROUS != 0;
        !dangerous_on_parent && !dangerous_on_child
    })
}

#[cfg(windows)]
fn snapshot_windows_parent_policy_accepts(
    owner_sid: &str,
    process_sid: &str,
    aces: &[SnapshotWindowsAceMarker],
) -> bool {
    snapshot_windows_parent_owner_accepts(owner_sid, process_sid)
        && snapshot_windows_inherited_child_policy_accepts(process_sid, aces)
}

#[cfg(windows)]
fn snapshot_windows_parent_marker(
    file: &File,
    process_sid: String,
) -> Result<SnapshotParentPolicyMarker> {
    use windows_permissions::constants::{
        AccessRights, AceType, AclRevision, SeObjectType, SecurityInformation,
    };
    use windows_permissions::wrappers::{
        ConvertSecurityDescriptorToStringSecurityDescriptor, ConvertSidToStringSid, GetAce,
        GetAclInformationSize, GetSecurityDescriptorDacl, GetSecurityDescriptorOwner,
        GetSecurityInfo, IsValidAcl,
    };

    // One owned descriptor supplies every fact in this snapshot. A recheck obtains one new
    // descriptor and compares the resulting owned marker; nothing borrowed escapes this scope.
    let descriptor =
        snapshot_open_operation(SnapshotOpenOperation::WindowsGetSecurityInfo, || {
            let descriptor = GetSecurityInfo(
                file,
                SeObjectType::SE_FILE_OBJECT,
                SecurityInformation::Owner | SecurityInformation::Dacl,
            )
            .map_err(|_| HeleosError::PolicyDenied)?;
            Ok(descriptor)
        })?;
    let borrowed_owner =
        snapshot_open_operation(SnapshotOpenOperation::WindowsOwnerLookup, || {
            GetSecurityDescriptorOwner(descriptor.as_ref())
                .map_err(|_| HeleosError::PolicyDenied)?
                .ok_or(HeleosError::PolicyDenied)
        })?;
    let owner_sid =
        snapshot_open_operation(SnapshotOpenOperation::WindowsOwnerSidConversion, || {
            ConvertSidToStringSid(borrowed_owner).map_err(|_| HeleosError::PolicyDenied)
        })?;
    let owner_sid = snapshot_open_operation(SnapshotOpenOperation::WindowsOwnerUnicode, || {
        owner_sid
            .to_str()
            .map(str::to_owned)
            .ok_or(HeleosError::PolicyDenied)
    })?;
    let owner_sid =
        snapshot_open_operation(SnapshotOpenOperation::WindowsOwnerCanonicalization, || {
            canonical_snapshot_windows_sid(&owner_sid)
        })?;

    let dacl_sddl =
        snapshot_open_operation(SnapshotOpenOperation::WindowsDaclSddlConversion, || {
            ConvertSecurityDescriptorToStringSecurityDescriptor(
                descriptor.as_ref(),
                SecurityInformation::Dacl,
            )
            .map_err(|_| HeleosError::PolicyDenied)
        })?;
    let dacl_sddl = snapshot_open_operation(SnapshotOpenOperation::WindowsDaclUnicode, || {
        dacl_sddl
            .to_str()
            .map(str::to_owned)
            .ok_or(HeleosError::PolicyDenied)
    })?;
    if dacl_sddl.contains("NO_ACCESS_CONTROL") {
        return Err(HeleosError::PolicyDenied);
    }
    let parsed =
        snapshot_open_operation(SnapshotOpenOperation::WindowsDaclBoundsAndParser, || {
            parse_snapshot_windows_dacl_sddl(&dacl_sddl)
        })?;

    let acl = snapshot_open_operation(SnapshotOpenOperation::WindowsDaclExtraction, || {
        GetSecurityDescriptorDacl(descriptor.as_ref())
            .map_err(|_| HeleosError::PolicyDenied)?
            .ok_or(HeleosError::PolicyDenied)
    })?;
    snapshot_open_operation(SnapshotOpenOperation::WindowsAclValidity, || {
        if IsValidAcl(acl) {
            Ok(())
        } else {
            Err(HeleosError::PolicyDenied)
        }
    })?;
    snapshot_open_operation(SnapshotOpenOperation::WindowsAclRevision, || {
        if acl.revision_level() == AclRevision::ACL_REVISION {
            Ok(())
        } else {
            Err(HeleosError::PolicyDenied)
        }
    })?;
    let acl_revision = AclRevision::ACL_REVISION as u8;
    snapshot_open_operation(SnapshotOpenOperation::WindowsAclSizeAndCount, || {
        let information = GetAclInformationSize(acl).map_err(|_| HeleosError::PolicyDenied)?;
        if usize::try_from(information.AceCount).map_err(|_| HeleosError::PolicyDenied)?
            == parsed.len()
        {
            Ok(())
        } else {
            Err(HeleosError::PolicyDenied)
        }
    })?;
    snapshot_open_operation(SnapshotOpenOperation::WindowsAccessRightsMask, || {
        if AccessRights::All.bits() == u32::MAX {
            Ok(())
        } else {
            Err(HeleosError::PolicyDenied)
        }
    })?;

    let mut aces = Vec::with_capacity(parsed.len());
    for (index, parsed_ace) in parsed.into_iter().enumerate() {
        let index = u32::try_from(index).map_err(|_| HeleosError::PolicyDenied)?;
        let ace = snapshot_open_operation(SnapshotOpenOperation::WindowsIndexedGetAce, || {
            let ace = GetAce(acl, index).map_err(|_| HeleosError::PolicyDenied)?;
            Ok(ace)
        })?;
        let ace_type = snapshot_open_operation(SnapshotOpenOperation::WindowsAceType, || {
            let ace_type = ace.ace_type();
            Ok(ace_type)
        })?;
        let expected_type = match parsed_ace.kind {
            ParsedSafeAceKind::Allow => AceType::ACCESS_ALLOWED_ACE_TYPE,
            ParsedSafeAceKind::Deny => AceType::ACCESS_DENIED_ACE_TYPE,
        };
        if ace_type != expected_type {
            return Err(HeleosError::PolicyDenied);
        }
        match parsed_ace.kind {
            ParsedSafeAceKind::Deny => aces.push(SnapshotWindowsAceMarker::Deny),
            ParsedSafeAceKind::Allow => {
                let flags =
                    snapshot_open_operation(SnapshotOpenOperation::WindowsAllowFlags, || {
                        let flags = ace.flags().bits();
                        if flags == parsed_ace.flags {
                            Ok(flags)
                        } else {
                            Err(HeleosError::PolicyDenied)
                        }
                    })?;
                let mask =
                    snapshot_open_operation(SnapshotOpenOperation::WindowsAllowMask, || {
                        let mask = ace.mask().bits();
                        Ok(mask)
                    })?;
                let trustee_sid = snapshot_open_operation(
                    SnapshotOpenOperation::WindowsAllowSidConversion,
                    || {
                        let borrowed_sid = ace.sid().ok_or(HeleosError::PolicyDenied)?;
                        ConvertSidToStringSid(borrowed_sid).map_err(|_| HeleosError::PolicyDenied)
                    },
                )?;
                let trustee_sid =
                    snapshot_open_operation(SnapshotOpenOperation::WindowsAllowSidUnicode, || {
                        trustee_sid
                            .to_str()
                            .map(str::to_owned)
                            .ok_or(HeleosError::PolicyDenied)
                    })?;
                let trustee_sid = snapshot_open_operation(
                    SnapshotOpenOperation::WindowsAllowSidCanonicalization,
                    || canonical_snapshot_windows_sid(&trustee_sid),
                )?;
                aces.push(SnapshotWindowsAceMarker::Allow {
                    flags,
                    mask,
                    trustee_sid,
                });
            }
        }
    }

    snapshot_open_operation(
        SnapshotOpenOperation::WindowsParentEffectivePredicate,
        || {
            if snapshot_windows_parent_owner_accepts(&owner_sid, &process_sid) {
                Ok(())
            } else {
                Err(HeleosError::PolicyDenied)
            }
        },
    )?;
    snapshot_open_operation(
        SnapshotOpenOperation::WindowsInheritedChildPredicate,
        || {
            if snapshot_windows_parent_policy_accepts(&owner_sid, &process_sid, &aces) {
                Ok(())
            } else {
                Err(HeleosError::PolicyDenied)
            }
        },
    )?;
    Ok(SnapshotParentPolicyMarker {
        process_sid,
        owner_sid,
        dacl_sddl,
        acl_revision,
        aces,
    })
}

#[cfg(windows)]
fn capture_snapshot_parent_policy(file: &File) -> Result<SnapshotParentPolicyMarker> {
    let process_sid = stellar_agent_windows_identity::current_user_sid_string()
        .map_err(|_| HeleosError::PolicyDenied)
        .and_then(|value| canonical_snapshot_windows_sid(&value))?;
    snapshot_windows_parent_marker(file, process_sid)
}

#[cfg(windows)]
fn recheck_snapshot_parent_policy(
    file: &File,
    expected: &SnapshotParentPolicyMarker,
) -> Result<()> {
    let actual = snapshot_windows_parent_marker(file, expected.process_sid.clone())?;
    snapshot_open_operation(SnapshotOpenOperation::WindowsMarkerComparison, || {
        if &actual == expected {
            Ok(())
        } else {
            Err(HeleosError::PolicyDenied)
        }
    })
}

#[cfg(not(any(unix, windows)))]
#[derive(Clone, Debug, Eq, PartialEq)]
struct SnapshotParentPolicyMarker;

#[cfg(not(any(unix, windows)))]
fn capture_snapshot_parent_policy(_: &File) -> Result<SnapshotParentPolicyMarker> {
    Err(HeleosError::PolicyDenied)
}

#[cfg(not(any(unix, windows)))]
fn recheck_snapshot_parent_policy(_: &File, _: &SnapshotParentPolicyMarker) -> Result<()> {
    Err(HeleosError::PolicyDenied)
}

fn default_snapshot_parent_path() -> PathBuf {
    #[cfg(test)]
    SNAPSHOT_SELECTOR_CALLS.with(|calls| calls.set(calls.get() + 1));
    tempfile::env::temp_dir()
}

#[cfg(test)]
std::thread_local! {
    static SNAPSHOT_SELECTOR_CALLS: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
}

#[cfg(test)]
fn reset_snapshot_selector_calls_for_test() {
    SNAPSHOT_SELECTOR_CALLS.with(|calls| calls.set(0));
}

#[cfg(test)]
fn snapshot_selector_calls_for_test() -> usize {
    SNAPSHOT_SELECTOR_CALLS.with(std::cell::Cell::get)
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SnapshotCapacityMode {
    StoreManaged,
    CallerAdmitted,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SnapshotChildEmptyPhase {
    BeforeHardening,
    AfterHardening,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SnapshotEndpointBarrier {
    BeforeSqliteOpen,
    AfterSqliteOpen,
    AfterConfigurationAndRecovery,
    BeforeStoreReturn,
}

impl SnapshotEndpointBarrier {
    #[cfg(test)]
    const ALL: [Self; 4] = [
        Self::BeforeSqliteOpen,
        Self::AfterSqliteOpen,
        Self::AfterConfigurationAndRecovery,
        Self::BeforeStoreReturn,
    ];

    #[cfg(test)]
    const fn index(self) -> usize {
        match self {
            Self::BeforeSqliteOpen => 0,
            Self::AfterSqliteOpen => 1,
            Self::AfterConfigurationAndRecovery => 2,
            Self::BeforeStoreReturn => 3,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SnapshotOpenOperation {
    ParentRetainedMetadata,
    ParentRetainedType,
    ParentRetainedReparse,
    ParentRetainedMarker,
    ParentRetainedPolicy,
    ParentAmbientMetadata,
    ParentAmbientType,
    ParentAmbientReparse,
    ParentAmbientMarker,
    ParentAmbientOpen,
    ParentAmbientRebound,
    #[cfg(unix)]
    UnixParentPolicyMetadata,
    #[cfg(unix)]
    UnixParentUidRead,
    #[cfg(unix)]
    UnixParentModeRead,
    #[cfg(unix)]
    UnixParentMarkerComparison,
    #[cfg(unix)]
    UnixParentPredicate,
    #[cfg(windows)]
    WindowsGetSecurityInfo,
    #[cfg(windows)]
    WindowsOwnerLookup,
    #[cfg(windows)]
    WindowsOwnerSidConversion,
    #[cfg(windows)]
    WindowsOwnerUnicode,
    #[cfg(windows)]
    WindowsOwnerCanonicalization,
    #[cfg(windows)]
    WindowsDaclSddlConversion,
    #[cfg(windows)]
    WindowsDaclUnicode,
    #[cfg(windows)]
    WindowsDaclBoundsAndParser,
    #[cfg(windows)]
    WindowsDaclExtraction,
    #[cfg(windows)]
    WindowsAclValidity,
    #[cfg(windows)]
    WindowsAclRevision,
    #[cfg(windows)]
    WindowsAclSizeAndCount,
    #[cfg(windows)]
    WindowsAccessRightsMask,
    #[cfg(windows)]
    WindowsIndexedGetAce,
    #[cfg(windows)]
    WindowsAceType,
    #[cfg(windows)]
    WindowsAllowFlags,
    #[cfg(windows)]
    WindowsAllowMask,
    #[cfg(windows)]
    WindowsAllowSidConversion,
    #[cfg(windows)]
    WindowsAllowSidUnicode,
    #[cfg(windows)]
    WindowsAllowSidCanonicalization,
    #[cfg(windows)]
    WindowsParentEffectivePredicate,
    #[cfg(windows)]
    WindowsInheritedChildPredicate,
    #[cfg(windows)]
    WindowsMarkerComparison,
    IntoPartsTransfer,
    ProvisionalFirstBinding,
    ProvisionalChildOpen,
    ProvisionalChildMetadata,
    ProvisionalChildType,
    ProvisionalChildReparse,
    ProvisionalChildMarker,
    ProvisionalChildCapabilityClone,
    ProvisionalChildIterator,
    ProvisionalChildFirstEntry,
    ProvisionalChildEmptyDecision,
    DisabledTempPathDrop,
    ProvisionalSecondBinding,
    ChildOpen,
    ChildRetainedOpen,
    ChildInitialMetadata,
    ChildType,
    ChildReparse,
    ChildMarkerCapture,
    ChildCapabilityClone,
    ChildHardening,
    ChildEmptyBeforeIterator,
    ChildEmptyBeforeFirstEntry,
    ChildEmptyBeforeDecision,
    ChildPrivateApply,
    ChildPrivateReadback,
    ChildEmptyAfterIterator,
    ChildEmptyAfterFirstEntry,
    ChildEmptyAfterDecision,
    ChildRelativeBinding,
    ChildReady,
    DatabaseSourceClone,
    DatabaseSourceSeek,
    DatabaseCreate,
    DatabaseInitialMetadata,
    DatabaseType,
    DatabaseReparse,
    DatabaseZeroLength,
    DatabasePrivateApply,
    DatabasePrivateReadback,
    DatabaseSecondZeroLength,
    DatabaseMarker,
    DatabaseHandleClone,
    DatabaseSourceRead,
    DatabaseByteAccounting,
    DatabaseDestinationWrite,
    DatabaseFinalByteCount,
    DatabaseCopyBytes,
    DatabaseFlush,
    DatabaseSync,
    DatabasePostWritePrivacy,
    DatabaseRelativeBinding,
    DatabaseAmbientBinding,
    WalCreate,
    WalInitialMetadata,
    WalType,
    WalReparse,
    WalZeroLength,
    WalPrivateApply,
    WalPrivateReadback,
    WalSecondZeroLength,
    WalMarker,
    WalSourceClone,
    WalSourceSeek,
    WalSourceRead,
    WalByteAccounting,
    WalDestinationWrite,
    WalFinalByteCount,
    WalFlush,
    WalSync,
    WalPostWritePrivacy,
    WalRelativeBinding,
    WalAmbientBinding,
    ShmCreate,
    ShmInitialMetadata,
    ShmType,
    ShmReparse,
    ShmZeroLength,
    ShmPrivateApply,
    ShmPrivateReadback,
    ShmSecondZeroLength,
    ShmMarker,
    ShmFlush,
    ShmSync,
    ShmRelativeBinding,
    ShmAmbientBinding,
    PreCapacityTotalQuery,
    PreCapacityAvailableQuery,
    PreCapacityArithmetic,
    PostCapacityTotalQuery,
    PostCapacityAvailableQuery,
    PostCapacityReserveDecision,
    DatabaseCopy,
    SidecarPreparation,
    SourcePreLengths,
    SourcePreDatabaseIdentity,
    SourcePreWalIdentity,
    SourcePreShmIdentity,
    SourcePreReaderLock,
    SourcePreParent,
    SourcePreSidecarPolicy,
    SourcePreOpenRechecks,
    BeforeSqliteBarrier,
    SqliteOpen,
    AfterSqliteBarrier,
    RegisterJcsScalar,
    RegisterUuidScalar,
    RegisterValidTextScalar,
    BusyTimeout,
    DefensiveDbConfig,
    TrustedSchemaDbConfig,
    DqsDdlDbConfig,
    DqsDmlDbConfig,
    AttachCreateDbConfig,
    AttachWriteDbConfig,
    ForeignKeysPragma,
    TrustedSchemaPragma,
    SynchronousPragma,
    TempStorePragma,
    JournalModeRecoveryQuery,
    QueryOnlyPragma,
    ConfigureSourceDatabaseIdentity,
    ConfigureAndRecover,
    AfterRecoveryBarrier,
    SourcePostLengths,
    SourcePostDatabaseIdentity,
    SourcePostWalIdentity,
    SourcePostShmIdentity,
    SourcePostReaderLock,
    SourcePostParent,
    SourcePostSidecarPolicy,
    SourcePostRecoveryRechecks,
    RecoveredPrivacyIterator,
    RecoveredPrivacyEntryRead,
    RecoveredPrivacyAllowedName,
    RecoveredPrivacyOpen,
    RecoveredPrivacyMetadata,
    RecoveredPrivacyType,
    RecoveredPrivacyReparse,
    RecoveredPrivacyPolicy,
    RecoveredPrivacyMarker,
    RecoveredPrivacyFinalBinding,
    RecoveredPrivacy,
    RecoveredLayoutIterator,
    RecoveredLayoutEntryRead,
    RecoveredLayoutAllowedName,
    RecoveredLayoutOpen,
    RecoveredLayoutMetadata,
    RecoveredLayoutType,
    RecoveredLayoutReparse,
    RecoveredLayoutPrivacy,
    RecoveredLayoutGrowthAdd,
    RecoveredLayoutDecision,
    RecoveredLayoutFinalBinding,
    RecoveredLayoutGrowthAndCapacity,
    FinalBarrier,
    FieldOwnership,
    StoreAssembly,
    Return,
}

#[derive(Clone, Copy)]
struct SnapshotArtifactOperations {
    create: SnapshotOpenOperation,
    initial_metadata: SnapshotOpenOperation,
    type_check: SnapshotOpenOperation,
    reparse: SnapshotOpenOperation,
    zero_length: SnapshotOpenOperation,
    private_apply: SnapshotOpenOperation,
    private_readback: SnapshotOpenOperation,
    second_zero_length: SnapshotOpenOperation,
    marker: SnapshotOpenOperation,
    flush: SnapshotOpenOperation,
    sync: SnapshotOpenOperation,
    relative_binding: SnapshotOpenOperation,
    ambient_binding: SnapshotOpenOperation,
}

#[derive(Clone, Copy)]
struct SnapshotCopyOperations {
    source_clone: SnapshotOpenOperation,
    source_seek: SnapshotOpenOperation,
    source_read: SnapshotOpenOperation,
    byte_accounting: SnapshotOpenOperation,
    destination_write: SnapshotOpenOperation,
    final_byte_count: SnapshotOpenOperation,
    post_write_privacy: SnapshotOpenOperation,
    relative_binding: SnapshotOpenOperation,
    ambient_binding: SnapshotOpenOperation,
}

fn snapshot_copy_operations(name: &OsStr) -> Result<SnapshotCopyOperations> {
    if name == OsStr::new("snapshot.sqlite3") {
        Ok(SnapshotCopyOperations {
            source_clone: SnapshotOpenOperation::DatabaseSourceClone,
            source_seek: SnapshotOpenOperation::DatabaseSourceSeek,
            source_read: SnapshotOpenOperation::DatabaseSourceRead,
            byte_accounting: SnapshotOpenOperation::DatabaseByteAccounting,
            destination_write: SnapshotOpenOperation::DatabaseDestinationWrite,
            final_byte_count: SnapshotOpenOperation::DatabaseFinalByteCount,
            post_write_privacy: SnapshotOpenOperation::DatabasePostWritePrivacy,
            relative_binding: SnapshotOpenOperation::DatabaseRelativeBinding,
            ambient_binding: SnapshotOpenOperation::DatabaseAmbientBinding,
        })
    } else if name == OsStr::new("snapshot.sqlite3-wal") {
        Ok(SnapshotCopyOperations {
            source_clone: SnapshotOpenOperation::WalSourceClone,
            source_seek: SnapshotOpenOperation::WalSourceSeek,
            source_read: SnapshotOpenOperation::WalSourceRead,
            byte_accounting: SnapshotOpenOperation::WalByteAccounting,
            destination_write: SnapshotOpenOperation::WalDestinationWrite,
            final_byte_count: SnapshotOpenOperation::WalFinalByteCount,
            post_write_privacy: SnapshotOpenOperation::WalPostWritePrivacy,
            relative_binding: SnapshotOpenOperation::WalRelativeBinding,
            ambient_binding: SnapshotOpenOperation::WalAmbientBinding,
        })
    } else {
        Err(HeleosError::PolicyDenied)
    }
}

fn snapshot_artifact_operations(name: &OsStr) -> Result<SnapshotArtifactOperations> {
    if name == OsStr::new("snapshot.sqlite3") {
        Ok(SnapshotArtifactOperations {
            create: SnapshotOpenOperation::DatabaseCreate,
            initial_metadata: SnapshotOpenOperation::DatabaseInitialMetadata,
            type_check: SnapshotOpenOperation::DatabaseType,
            reparse: SnapshotOpenOperation::DatabaseReparse,
            zero_length: SnapshotOpenOperation::DatabaseZeroLength,
            private_apply: SnapshotOpenOperation::DatabasePrivateApply,
            private_readback: SnapshotOpenOperation::DatabasePrivateReadback,
            second_zero_length: SnapshotOpenOperation::DatabaseSecondZeroLength,
            marker: SnapshotOpenOperation::DatabaseMarker,
            flush: SnapshotOpenOperation::DatabaseFlush,
            sync: SnapshotOpenOperation::DatabaseSync,
            relative_binding: SnapshotOpenOperation::DatabaseRelativeBinding,
            ambient_binding: SnapshotOpenOperation::DatabaseAmbientBinding,
        })
    } else if name == OsStr::new("snapshot.sqlite3-wal") {
        Ok(SnapshotArtifactOperations {
            create: SnapshotOpenOperation::WalCreate,
            initial_metadata: SnapshotOpenOperation::WalInitialMetadata,
            type_check: SnapshotOpenOperation::WalType,
            reparse: SnapshotOpenOperation::WalReparse,
            zero_length: SnapshotOpenOperation::WalZeroLength,
            private_apply: SnapshotOpenOperation::WalPrivateApply,
            private_readback: SnapshotOpenOperation::WalPrivateReadback,
            second_zero_length: SnapshotOpenOperation::WalSecondZeroLength,
            marker: SnapshotOpenOperation::WalMarker,
            flush: SnapshotOpenOperation::WalFlush,
            sync: SnapshotOpenOperation::WalSync,
            relative_binding: SnapshotOpenOperation::WalRelativeBinding,
            ambient_binding: SnapshotOpenOperation::WalAmbientBinding,
        })
    } else if name == OsStr::new("snapshot.sqlite3-shm") {
        Ok(SnapshotArtifactOperations {
            create: SnapshotOpenOperation::ShmCreate,
            initial_metadata: SnapshotOpenOperation::ShmInitialMetadata,
            type_check: SnapshotOpenOperation::ShmType,
            reparse: SnapshotOpenOperation::ShmReparse,
            zero_length: SnapshotOpenOperation::ShmZeroLength,
            private_apply: SnapshotOpenOperation::ShmPrivateApply,
            private_readback: SnapshotOpenOperation::ShmPrivateReadback,
            second_zero_length: SnapshotOpenOperation::ShmSecondZeroLength,
            marker: SnapshotOpenOperation::ShmMarker,
            flush: SnapshotOpenOperation::ShmFlush,
            sync: SnapshotOpenOperation::ShmSync,
            relative_binding: SnapshotOpenOperation::ShmRelativeBinding,
            ambient_binding: SnapshotOpenOperation::ShmAmbientBinding,
        })
    } else {
        Err(HeleosError::PolicyDenied)
    }
}

#[cfg(test)]
impl SnapshotOpenOperation {
    const ALL: &'static [Self] = &[
        Self::ParentRetainedMetadata,
        Self::ParentRetainedType,
        Self::ParentRetainedReparse,
        Self::ParentRetainedMarker,
        Self::ParentRetainedPolicy,
        Self::ParentAmbientMetadata,
        Self::ParentAmbientType,
        Self::ParentAmbientReparse,
        Self::ParentAmbientMarker,
        Self::ParentAmbientOpen,
        Self::ParentAmbientRebound,
        #[cfg(unix)]
        Self::UnixParentPolicyMetadata,
        #[cfg(unix)]
        Self::UnixParentUidRead,
        #[cfg(unix)]
        Self::UnixParentModeRead,
        #[cfg(unix)]
        Self::UnixParentMarkerComparison,
        #[cfg(unix)]
        Self::UnixParentPredicate,
        #[cfg(windows)]
        Self::WindowsGetSecurityInfo,
        #[cfg(windows)]
        Self::WindowsOwnerLookup,
        #[cfg(windows)]
        Self::WindowsOwnerSidConversion,
        #[cfg(windows)]
        Self::WindowsOwnerUnicode,
        #[cfg(windows)]
        Self::WindowsOwnerCanonicalization,
        #[cfg(windows)]
        Self::WindowsDaclSddlConversion,
        #[cfg(windows)]
        Self::WindowsDaclUnicode,
        #[cfg(windows)]
        Self::WindowsDaclBoundsAndParser,
        #[cfg(windows)]
        Self::WindowsDaclExtraction,
        #[cfg(windows)]
        Self::WindowsAclValidity,
        #[cfg(windows)]
        Self::WindowsAclRevision,
        #[cfg(windows)]
        Self::WindowsAclSizeAndCount,
        #[cfg(windows)]
        Self::WindowsAccessRightsMask,
        #[cfg(windows)]
        Self::WindowsIndexedGetAce,
        #[cfg(windows)]
        Self::WindowsAceType,
        #[cfg(windows)]
        Self::WindowsAllowFlags,
        #[cfg(windows)]
        Self::WindowsAllowMask,
        #[cfg(windows)]
        Self::WindowsAllowSidConversion,
        #[cfg(windows)]
        Self::WindowsAllowSidUnicode,
        #[cfg(windows)]
        Self::WindowsAllowSidCanonicalization,
        #[cfg(windows)]
        Self::WindowsParentEffectivePredicate,
        #[cfg(windows)]
        Self::WindowsInheritedChildPredicate,
        #[cfg(windows)]
        Self::WindowsMarkerComparison,
        Self::IntoPartsTransfer,
        Self::ProvisionalFirstBinding,
        Self::ProvisionalChildOpen,
        Self::ProvisionalChildMetadata,
        Self::ProvisionalChildType,
        Self::ProvisionalChildReparse,
        Self::ProvisionalChildMarker,
        Self::ProvisionalChildCapabilityClone,
        Self::ProvisionalChildIterator,
        Self::ProvisionalChildFirstEntry,
        Self::ProvisionalChildEmptyDecision,
        Self::DisabledTempPathDrop,
        Self::ProvisionalSecondBinding,
        Self::ChildOpen,
        Self::ChildRetainedOpen,
        Self::ChildInitialMetadata,
        Self::ChildType,
        Self::ChildReparse,
        Self::ChildMarkerCapture,
        Self::ChildCapabilityClone,
        Self::ChildHardening,
        Self::ChildEmptyBeforeIterator,
        Self::ChildEmptyBeforeFirstEntry,
        Self::ChildEmptyBeforeDecision,
        Self::ChildPrivateApply,
        Self::ChildPrivateReadback,
        Self::ChildEmptyAfterIterator,
        Self::ChildEmptyAfterFirstEntry,
        Self::ChildEmptyAfterDecision,
        Self::ChildRelativeBinding,
        Self::ChildReady,
        Self::DatabaseSourceClone,
        Self::DatabaseSourceSeek,
        Self::DatabaseCreate,
        Self::DatabaseInitialMetadata,
        Self::DatabaseType,
        Self::DatabaseReparse,
        Self::DatabaseZeroLength,
        Self::DatabasePrivateApply,
        Self::DatabasePrivateReadback,
        Self::DatabaseSecondZeroLength,
        Self::DatabaseMarker,
        Self::DatabaseHandleClone,
        Self::DatabaseSourceRead,
        Self::DatabaseByteAccounting,
        Self::DatabaseDestinationWrite,
        Self::DatabaseFinalByteCount,
        Self::DatabaseCopyBytes,
        Self::DatabaseFlush,
        Self::DatabaseSync,
        Self::DatabasePostWritePrivacy,
        Self::DatabaseRelativeBinding,
        Self::DatabaseAmbientBinding,
        Self::WalCreate,
        Self::WalInitialMetadata,
        Self::WalType,
        Self::WalReparse,
        Self::WalZeroLength,
        Self::WalPrivateApply,
        Self::WalPrivateReadback,
        Self::WalSecondZeroLength,
        Self::WalMarker,
        Self::WalSourceClone,
        Self::WalSourceSeek,
        Self::WalSourceRead,
        Self::WalByteAccounting,
        Self::WalDestinationWrite,
        Self::WalFinalByteCount,
        Self::WalFlush,
        Self::WalSync,
        Self::WalPostWritePrivacy,
        Self::WalRelativeBinding,
        Self::WalAmbientBinding,
        Self::ShmCreate,
        Self::ShmInitialMetadata,
        Self::ShmType,
        Self::ShmReparse,
        Self::ShmZeroLength,
        Self::ShmPrivateApply,
        Self::ShmPrivateReadback,
        Self::ShmSecondZeroLength,
        Self::ShmMarker,
        Self::ShmFlush,
        Self::ShmSync,
        Self::ShmRelativeBinding,
        Self::ShmAmbientBinding,
        Self::DatabaseCopy,
        Self::SidecarPreparation,
        Self::SourcePreLengths,
        Self::SourcePreDatabaseIdentity,
        Self::SourcePreWalIdentity,
        Self::SourcePreShmIdentity,
        Self::SourcePreReaderLock,
        Self::SourcePreParent,
        Self::SourcePreSidecarPolicy,
        Self::SourcePreOpenRechecks,
        Self::BeforeSqliteBarrier,
        Self::SqliteOpen,
        Self::AfterSqliteBarrier,
        Self::RegisterJcsScalar,
        Self::RegisterUuidScalar,
        Self::RegisterValidTextScalar,
        Self::BusyTimeout,
        Self::DefensiveDbConfig,
        Self::TrustedSchemaDbConfig,
        Self::DqsDdlDbConfig,
        Self::DqsDmlDbConfig,
        Self::AttachCreateDbConfig,
        Self::AttachWriteDbConfig,
        Self::ForeignKeysPragma,
        Self::TrustedSchemaPragma,
        Self::SynchronousPragma,
        Self::TempStorePragma,
        Self::JournalModeRecoveryQuery,
        Self::QueryOnlyPragma,
        Self::ConfigureSourceDatabaseIdentity,
        Self::ConfigureAndRecover,
        Self::AfterRecoveryBarrier,
        Self::SourcePostLengths,
        Self::SourcePostDatabaseIdentity,
        Self::SourcePostWalIdentity,
        Self::SourcePostShmIdentity,
        Self::SourcePostReaderLock,
        Self::SourcePostParent,
        Self::SourcePostSidecarPolicy,
        Self::SourcePostRecoveryRechecks,
        Self::RecoveredPrivacyIterator,
        Self::RecoveredPrivacyEntryRead,
        Self::RecoveredPrivacyAllowedName,
        Self::RecoveredPrivacyOpen,
        Self::RecoveredPrivacyMetadata,
        Self::RecoveredPrivacyType,
        Self::RecoveredPrivacyReparse,
        Self::RecoveredPrivacyPolicy,
        Self::RecoveredPrivacyMarker,
        Self::RecoveredPrivacyFinalBinding,
        Self::RecoveredPrivacy,
        Self::RecoveredLayoutIterator,
        Self::RecoveredLayoutEntryRead,
        Self::RecoveredLayoutAllowedName,
        Self::RecoveredLayoutOpen,
        Self::RecoveredLayoutMetadata,
        Self::RecoveredLayoutType,
        Self::RecoveredLayoutReparse,
        Self::RecoveredLayoutPrivacy,
        Self::RecoveredLayoutGrowthAdd,
        Self::RecoveredLayoutDecision,
        Self::RecoveredLayoutFinalBinding,
        Self::RecoveredLayoutGrowthAndCapacity,
        Self::FinalBarrier,
        Self::FieldOwnership,
        Self::StoreAssembly,
        Self::Return,
    ];
}

#[cfg(test)]
const SNAPSHOT_CAPACITY_OPERATIONS: [SnapshotOpenOperation; 6] = [
    SnapshotOpenOperation::PreCapacityTotalQuery,
    SnapshotOpenOperation::PreCapacityAvailableQuery,
    SnapshotOpenOperation::PreCapacityArithmetic,
    SnapshotOpenOperation::PostCapacityTotalQuery,
    SnapshotOpenOperation::PostCapacityAvailableQuery,
    SnapshotOpenOperation::PostCapacityReserveDecision,
];

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SnapshotInjectedPrimaryKind {
    Io,
    Database,
    PolicyDenied,
}

#[cfg(test)]
impl SnapshotInjectedPrimaryKind {
    const ALL: [Self; 3] = [Self::Io, Self::Database, Self::PolicyDenied];

    fn error(self) -> HeleosError {
        match self {
            Self::Io => HeleosError::Io(io::Error::other("injected snapshot operation failure")),
            Self::Database => HeleosError::Database,
            Self::PolicyDenied => HeleosError::PolicyDenied,
        }
    }
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SnapshotPrimaryOutcomeForTest {
    Success,
    Io,
    Database,
    PolicyDenied,
}

#[cfg(test)]
impl SnapshotPrimaryOutcomeForTest {
    const ALL: [Self; 4] = [Self::Success, Self::Io, Self::Database, Self::PolicyDenied];

    const fn injected(self) -> Option<SnapshotInjectedPrimaryKind> {
        match self {
            Self::Success => None,
            Self::Io => Some(SnapshotInjectedPrimaryKind::Io),
            Self::Database => Some(SnapshotInjectedPrimaryKind::Database),
            Self::PolicyDenied => Some(SnapshotInjectedPrimaryKind::PolicyDenied),
        }
    }

    const fn error(self) -> Option<SnapshotInjectedPrimaryKind> {
        self.injected()
    }
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SnapshotCleanupFaultForTest {
    Io,
    PolicyDenied,
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SnapshotCleanupNamespaceFaultForTest {
    RemoveBeforeProductionRemove,
    InstallDecoyAfterRemove,
}

#[cfg(test)]
impl SnapshotCleanupFaultForTest {
    fn error(self) -> HeleosError {
        match self {
            Self::Io => HeleosError::Io(io::Error::other("injected cleanup I/O failure")),
            Self::PolicyDenied => HeleosError::PolicyDenied,
        }
    }
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SnapshotCleanupOutcomeForTest {
    Success,
    Io,
    PolicyDenied,
}

#[cfg(test)]
impl SnapshotCleanupOutcomeForTest {
    const ALL: [Self; 3] = [Self::Success, Self::Io, Self::PolicyDenied];

    const fn fault(self) -> Option<SnapshotCleanupFaultForTest> {
        match self {
            Self::Success => None,
            Self::Io => Some(SnapshotCleanupFaultForTest::Io),
            Self::PolicyDenied => Some(SnapshotCleanupFaultForTest::PolicyDenied),
        }
    }

    const fn error(self) -> Option<SnapshotInjectedPrimaryKind> {
        match self {
            Self::Success => None,
            Self::Io => Some(SnapshotInjectedPrimaryKind::Io),
            Self::PolicyDenied => Some(SnapshotInjectedPrimaryKind::PolicyDenied),
        }
    }
}

#[cfg(test)]
#[derive(Clone, Debug)]
struct SnapshotOpenOperationFault {
    target: SnapshotOpenOperation,
    primary: SnapshotInjectedPrimaryKind,
    remaining_matches: usize,
    fired: bool,
    trace: Vec<SnapshotOpenOperation>,
}

#[cfg(test)]
std::thread_local! {
    static SNAPSHOT_OPEN_OPERATION_FAULT: std::cell::RefCell<Option<SnapshotOpenOperationFault>> =
        const { std::cell::RefCell::new(None) };
}

#[cfg(test)]
std::thread_local! {
    static SNAPSHOT_CLEANUP_NAMESPACE_FAULT:
        std::cell::RefCell<Option<SnapshotCleanupNamespaceFaultForTest>> =
        const { std::cell::RefCell::new(None) };
}

#[cfg(test)]
fn arm_snapshot_cleanup_namespace_fault_for_test(fault: SnapshotCleanupNamespaceFaultForTest) {
    SNAPSHOT_CLEANUP_NAMESPACE_FAULT.with(|current| *current.borrow_mut() = Some(fault));
}

#[cfg(test)]
fn take_snapshot_cleanup_namespace_fault_for_test(
    expected: SnapshotCleanupNamespaceFaultForTest,
) -> bool {
    SNAPSHOT_CLEANUP_NAMESPACE_FAULT.with(|fault| {
        let mut fault = fault.borrow_mut();
        if fault.as_ref() == Some(&expected) {
            fault.take();
            true
        } else {
            false
        }
    })
}

#[cfg(test)]
std::thread_local! {
    static SNAPSHOT_CLEANUP_FAULT: std::cell::RefCell<Option<SnapshotCleanupFaultForTest>> =
        const { std::cell::RefCell::new(None) };
}

#[cfg(test)]
fn arm_snapshot_cleanup_fault_for_test(fault: SnapshotCleanupFaultForTest) {
    SNAPSHOT_CLEANUP_FAULT.with(|current| *current.borrow_mut() = Some(fault));
}

#[cfg(test)]
fn take_snapshot_cleanup_fault_for_test() -> Option<HeleosError> {
    SNAPSHOT_CLEANUP_FAULT.with(|fault| fault.borrow_mut().take().map(|fault| fault.error()))
}

#[cfg(test)]
fn clear_snapshot_cleanup_fault_for_test() {
    SNAPSHOT_CLEANUP_FAULT.with(|fault| *fault.borrow_mut() = None);
}

#[cfg(test)]
fn set_snapshot_open_operation_fault_for_test(
    target: SnapshotOpenOperation,
    primary: SnapshotInjectedPrimaryKind,
) {
    set_snapshot_open_operation_fault_after_matches_for_test(target, primary, 0);
}

#[cfg(test)]
fn set_snapshot_open_operation_fault_after_matches_for_test(
    target: SnapshotOpenOperation,
    primary: SnapshotInjectedPrimaryKind,
    remaining_matches: usize,
) {
    SNAPSHOT_OPEN_OPERATION_FAULT.with(|fault| {
        *fault.borrow_mut() = Some(SnapshotOpenOperationFault {
            target,
            primary,
            remaining_matches,
            fired: false,
            trace: Vec::new(),
        });
    });
}

#[cfg(test)]
fn snapshot_open_operation_fault_fired_for_test() -> bool {
    SNAPSHOT_OPEN_OPERATION_FAULT.with(|fault| {
        fault
            .borrow()
            .as_ref()
            .is_some_and(|fault| fault.fired && fault.trace.contains(&fault.target))
    })
}

#[cfg(test)]
fn snapshot_open_operation_trace_for_test() -> Vec<SnapshotOpenOperation> {
    SNAPSHOT_OPEN_OPERATION_FAULT.with(|fault| {
        fault
            .borrow()
            .as_ref()
            .map(|fault| fault.trace.clone())
            .unwrap_or_default()
    })
}

#[cfg(test)]
fn clear_snapshot_open_operation_fault_for_test() {
    SNAPSHOT_OPEN_OPERATION_FAULT.with(|fault| *fault.borrow_mut() = None);
}

fn snapshot_open_operation<T>(
    step: SnapshotOpenOperation,
    operation: impl FnOnce() -> Result<T>,
) -> Result<T> {
    let output = operation()?;
    #[cfg(not(test))]
    let _ = step;
    #[cfg(test)]
    let injected = SNAPSHOT_OPEN_OPERATION_FAULT.with(|fault| {
        let mut fault = fault.borrow_mut();
        let Some(fault) = fault.as_mut() else {
            return None;
        };
        fault.trace.push(step);
        if !fault.fired && fault.target == step {
            if fault.remaining_matches != 0 {
                fault.remaining_matches -= 1;
                return None;
            }
            fault.fired = true;
            Some(fault.primary)
        } else {
            None
        }
    });
    #[cfg(test)]
    if let Some(injected) = injected {
        return Err(injected.error());
    }
    Ok(output)
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SnapshotEndpointAxis {
    Parent,
    Child,
    Database,
}

#[cfg(test)]
impl SnapshotEndpointAxis {
    const ALL: [Self; 3] = [Self::Parent, Self::Child, Self::Database];
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SnapshotEndpointMutation {
    Rebound,
    #[cfg(windows)]
    MarkerMismatch,
    #[cfg(unix)]
    Missing,
    #[cfg(unix)]
    WrongType,
    #[cfg(unix)]
    SymlinkLoop,
    #[cfg(unix)]
    PrivatePolicy,
}

#[cfg(all(test, unix))]
impl SnapshotEndpointMutation {
    const ALL: [Self; 5] = [
        Self::Rebound,
        Self::Missing,
        Self::WrongType,
        Self::SymlinkLoop,
        Self::PrivatePolicy,
    ];
}

#[cfg(test)]
#[derive(Clone, Debug)]
struct SnapshotEndpointFault {
    barrier: SnapshotEndpointBarrier,
    axis: SnapshotEndpointAxis,
    mutation: SnapshotEndpointMutation,
    applied: bool,
    original: Option<PathBuf>,
    moved: Option<PathBuf>,
}

#[cfg(test)]
std::thread_local! {
    static SNAPSHOT_ENDPOINT_BARRIER_COUNTS: std::cell::RefCell<[usize; 4]> = const {
        std::cell::RefCell::new([0; 4])
    };
    static SNAPSHOT_ENDPOINT_FAULT: std::cell::RefCell<Option<SnapshotEndpointFault>> = const {
        std::cell::RefCell::new(None)
    };
}

#[cfg(test)]
fn reset_snapshot_endpoint_barriers_for_test() {
    SNAPSHOT_ENDPOINT_BARRIER_COUNTS.with(|counts| *counts.borrow_mut() = [0; 4]);
}

#[cfg(test)]
fn snapshot_endpoint_barriers_for_test() -> [usize; 4] {
    SNAPSHOT_ENDPOINT_BARRIER_COUNTS.with(|counts| *counts.borrow())
}

#[cfg(test)]
fn set_snapshot_endpoint_fault_for_test(
    barrier: SnapshotEndpointBarrier,
    axis: SnapshotEndpointAxis,
    mutation: SnapshotEndpointMutation,
) {
    reset_snapshot_endpoint_barriers_for_test();
    SNAPSHOT_ENDPOINT_FAULT.with(|fault| {
        *fault.borrow_mut() = Some(SnapshotEndpointFault {
            barrier,
            axis,
            mutation,
            applied: false,
            original: None,
            moved: None,
        });
    });
}

#[cfg(test)]
fn clear_snapshot_endpoint_fault_for_test() {
    SNAPSHOT_ENDPOINT_FAULT.with(|fault| *fault.borrow_mut() = None);
}

#[cfg(all(test, windows))]
fn snapshot_endpoint_expected_marker_for_test(
    axis: SnapshotEndpointAxis,
    expected: FileMarker,
) -> FileMarker {
    let mismatch = SNAPSHOT_ENDPOINT_FAULT.with(|fault| {
        fault.borrow().as_ref().is_some_and(|fault| {
            fault.applied
                && fault.axis == axis
                && fault.mutation == SnapshotEndpointMutation::MarkerMismatch
        })
    });
    if mismatch {
        FileMarker {
            attributes: expected.attributes,
            creation_time: expected.creation_time ^ 1,
        }
    } else {
        expected
    }
}

enum SnapshotParentSelection<'a> {
    StoreManaged,
    CallerAdmitted(&'a ReadOnlySnapshotParent),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SnapshotCleanupState {
    Owned,
    Cleaned,
    Uncertain,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SnapshotCreateControl {
    Production,
    #[cfg(test)]
    AfterChildOpenIo,
    #[cfg(test)]
    ImmediatelyAfterChildCreateIo,
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SnapshotCreateTestFault {
    AfterChildOpenIo,
    ImmediatelyAfterChildCreateIo,
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SnapshotCleanupEvent {
    ConnectionClosed,
    ScratchUsersReleased,
    DatabaseHandleDropped,
    ChildHandlesDropped,
    AmbientParentChecked,
    RelativeBindingsValidated,
    ValidationHandlesDropped,
    FinalRetainedCheckpoint,
    EntryRemoved,
    RelativeAbsenceVerified,
    AmbientAbsenceResolved,
    SourceHandlesAndReaderLockDropped,
}

#[cfg(test)]
type SnapshotCleanupEvents = std::rc::Rc<std::cell::RefCell<Vec<SnapshotCleanupEvent>>>;

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SnapshotFirstByteEvent {
    ChildEmptyBeforeHardening,
    ChildEmptyAfterHardening,
    DatabasePrivateAndEmpty,
    WalPrivateAndEmpty,
    ShmPrivateAndEmpty,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SnapshotFirstByteCheckpoint {
    DatabaseCreation,
    FirstDatabaseWrite,
    FirstWalWrite,
    FirstShmWrite,
}

#[cfg(test)]
impl SnapshotFirstByteCheckpoint {
    const ALL: [Self; 4] = [
        Self::DatabaseCreation,
        Self::FirstDatabaseWrite,
        Self::FirstWalWrite,
        Self::FirstShmWrite,
    ];
}

#[cfg(test)]
#[derive(Clone, Debug, Eq, PartialEq)]
struct SnapshotFirstByteWitness {
    checkpoint: SnapshotFirstByteCheckpoint,
    child_empty: bool,
    length: Option<u64>,
    private_verified: bool,
    unix_mode: Option<u32>,
}

#[cfg(test)]
std::thread_local! {
    static SNAPSHOT_FIRST_BYTE_EVENTS: std::cell::RefCell<Vec<SnapshotFirstByteEvent>> = const {
        std::cell::RefCell::new(Vec::new())
    };
    static SNAPSHOT_FIRST_BYTE_WITNESSES:
        std::cell::RefCell<Vec<SnapshotFirstByteWitness>> = const {
        std::cell::RefCell::new(Vec::new())
    };
    static SNAPSHOT_FIRST_BYTE_CHECKPOINT_FAULT:
        std::cell::RefCell<Option<SnapshotFirstByteCheckpoint>> = const {
        std::cell::RefCell::new(None)
    };
}

#[cfg(test)]
fn reset_snapshot_first_byte_events_for_test() {
    SNAPSHOT_FIRST_BYTE_EVENTS.with(|events| events.borrow_mut().clear());
}

#[cfg(test)]
fn snapshot_first_byte_events_for_test() -> Vec<SnapshotFirstByteEvent> {
    SNAPSHOT_FIRST_BYTE_EVENTS.with(|events| events.borrow().clone())
}

#[cfg(test)]
fn reset_snapshot_first_byte_witnesses_for_test() {
    SNAPSHOT_FIRST_BYTE_WITNESSES.with(|witnesses| witnesses.borrow_mut().clear());
    SNAPSHOT_FIRST_BYTE_CHECKPOINT_FAULT.with(|fault| *fault.borrow_mut() = None);
}

#[cfg(test)]
fn snapshot_first_byte_witnesses_for_test() -> Vec<SnapshotFirstByteWitness> {
    SNAPSHOT_FIRST_BYTE_WITNESSES.with(|witnesses| witnesses.borrow().clone())
}

#[cfg(test)]
fn set_snapshot_first_byte_checkpoint_fault_for_test(checkpoint: SnapshotFirstByteCheckpoint) {
    SNAPSHOT_FIRST_BYTE_CHECKPOINT_FAULT.with(|fault| *fault.borrow_mut() = Some(checkpoint));
}

#[cfg(test)]
fn clear_snapshot_first_byte_checkpoint_fault_for_test() {
    SNAPSHOT_FIRST_BYTE_CHECKPOINT_FAULT.with(|fault| *fault.borrow_mut() = None);
}

fn record_snapshot_first_byte_witness(
    checkpoint: SnapshotFirstByteCheckpoint,
    file: &File,
    child_empty: bool,
    length: Option<u64>,
) -> Result<()> {
    verify_private_permissions_on_handle(file)?;
    #[cfg(not(test))]
    let _ = (checkpoint, child_empty, length);
    #[cfg(test)]
    {
        #[cfg(unix)]
        let unix_mode = {
            use std::os::unix::fs::PermissionsExt;
            Some(
                file.metadata()
                    .map_err(HeleosError::Io)?
                    .permissions()
                    .mode()
                    & 0o777,
            )
        };
        #[cfg(not(unix))]
        let unix_mode = None;
        SNAPSHOT_FIRST_BYTE_WITNESSES.with(|witnesses| {
            witnesses.borrow_mut().push(SnapshotFirstByteWitness {
                checkpoint,
                child_empty,
                length,
                private_verified: true,
                unix_mode,
            });
        });
        let fail = SNAPSHOT_FIRST_BYTE_CHECKPOINT_FAULT.with(|fault| {
            if fault.borrow().as_ref() == Some(&checkpoint) {
                fault.borrow_mut().take();
                true
            } else {
                false
            }
        });
        if fail {
            return Err(HeleosError::PolicyDenied);
        }
    }
    Ok(())
}

#[cfg(test)]
fn record_snapshot_first_byte_event(event: SnapshotFirstByteEvent) {
    SNAPSHOT_FIRST_BYTE_EVENTS.with(|events| events.borrow_mut().push(event));
}

#[cfg(test)]
fn record_snapshot_private_empty_file_event(name: &OsStr) -> Result<()> {
    let event = if name == OsStr::new("snapshot.sqlite3") {
        SnapshotFirstByteEvent::DatabasePrivateAndEmpty
    } else if name == OsStr::new("snapshot.sqlite3-wal") {
        SnapshotFirstByteEvent::WalPrivateAndEmpty
    } else if name == OsStr::new("snapshot.sqlite3-shm") {
        SnapshotFirstByteEvent::ShmPrivateAndEmpty
    } else {
        return Err(HeleosError::PolicyDenied);
    };
    record_snapshot_first_byte_event(event);
    Ok(())
}

#[cfg(test)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SnapshotTempPathEvent {
    VerifiedBeforeDrop,
    VerifiedAfterDrop,
}

#[cfg(test)]
std::thread_local! {
    static SNAPSHOT_TEMP_PATH_EVENTS: std::cell::RefCell<Vec<SnapshotTempPathEvent>> = const {
        std::cell::RefCell::new(Vec::new())
    };
}

#[cfg(test)]
fn reset_snapshot_temp_path_events_for_test() {
    SNAPSHOT_TEMP_PATH_EVENTS.with(|events| events.borrow_mut().clear());
}

#[cfg(test)]
fn snapshot_temp_path_events_for_test() -> Vec<SnapshotTempPathEvent> {
    SNAPSHOT_TEMP_PATH_EVENTS.with(|events| events.borrow().clone())
}

#[cfg(test)]
fn record_snapshot_temp_path_event(event: SnapshotTempPathEvent) {
    SNAPSHOT_TEMP_PATH_EVENTS.with(|events| events.borrow_mut().push(event));
}

#[cfg(test)]
#[derive(Clone, Debug)]
struct SnapshotGeneratedCollision {
    name: OsString,
    marker: FileMarker,
}

#[cfg(test)]
#[derive(Clone, Debug)]
enum SnapshotGeneratedCollisionState {
    Inactive,
    Armed,
    Installed(SnapshotGeneratedCollision),
}

#[cfg(test)]
std::thread_local! {
    static SNAPSHOT_GENERATED_COLLISION: std::cell::RefCell<SnapshotGeneratedCollisionState> =
        const { std::cell::RefCell::new(SnapshotGeneratedCollisionState::Inactive) };
}

#[cfg(test)]
fn arm_generated_candidate_collision_for_test() {
    SNAPSHOT_GENERATED_COLLISION.with(|state| {
        *state.borrow_mut() = SnapshotGeneratedCollisionState::Armed;
    });
}

#[cfg(test)]
fn take_generated_candidate_collision_for_test() -> Option<SnapshotGeneratedCollision> {
    SNAPSHOT_GENERATED_COLLISION.with(|state| {
        let current = std::mem::replace(
            &mut *state.borrow_mut(),
            SnapshotGeneratedCollisionState::Inactive,
        );
        match current {
            SnapshotGeneratedCollisionState::Installed(collision) => Some(collision),
            SnapshotGeneratedCollisionState::Inactive | SnapshotGeneratedCollisionState::Armed => {
                None
            }
        }
    })
}

#[cfg(test)]
fn install_first_generated_candidate_collision_for_test(
    parent_path: &Path,
    parent: &CapDir,
    name: &OsStr,
) -> io::Result<()> {
    let armed = SNAPSHOT_GENERATED_COLLISION.with(|state| {
        let mut state = state.borrow_mut();
        if matches!(*state, SnapshotGeneratedCollisionState::Armed) {
            *state = SnapshotGeneratedCollisionState::Inactive;
            true
        } else {
            false
        }
    });
    if !armed {
        return Ok(());
    }

    create_private_snapshot_directory(parent, name)?;
    let child = parent.open_dir(name)?;
    let sentinel = child
        .open_with("sentinel", &snapshot_file_create_options())?
        .into_std();
    let mut sentinel = sentinel;
    apply_private_permissions_to_handle(&mut sentinel)
        .map_err(|error| io::Error::other(error.to_string()))?;
    verify_private_permissions_on_handle(&sentinel)
        .map_err(|error| io::Error::other(error.to_string()))?;
    sentinel.write_all(b"do-not-touch")?;
    sentinel.sync_all()?;
    let path = parent_path.join(name);
    let marker = FileMarker::from_metadata(&fs::symlink_metadata(&path)?);
    SNAPSHOT_GENERATED_COLLISION.with(|state| {
        *state.borrow_mut() =
            SnapshotGeneratedCollisionState::Installed(SnapshotGeneratedCollision {
                name: name.to_os_string(),
                marker,
            });
    });
    Ok(())
}

#[cfg(test)]
std::thread_local! {
    static SNAPSHOT_PROVISIONAL_CLEANUP_FAULT: std::cell::RefCell<(bool, usize)> =
        const { std::cell::RefCell::new((false, 0)) };
}

#[cfg(test)]
fn arm_provisional_cleanup_io_once_for_test() {
    SNAPSHOT_PROVISIONAL_CLEANUP_FAULT.with(|fault| *fault.borrow_mut() = (true, 0));
}

#[cfg(test)]
fn provisional_cleanup_attempts_for_test() -> usize {
    SNAPSHOT_PROVISIONAL_CLEANUP_FAULT.with(|fault| fault.borrow().1)
}

#[cfg(test)]
fn take_provisional_cleanup_io_for_test() -> bool {
    SNAPSHOT_PROVISIONAL_CLEANUP_FAULT.with(|fault| {
        let mut fault = fault.borrow_mut();
        fault.1 += 1;
        std::mem::take(&mut fault.0)
    })
}

struct ProvisionalSnapshotChild {
    parent: Option<CapDir>,
    parent_file: Option<File>,
    parent_marker: FileMarker,
    parent_policy_marker: SnapshotParentPolicyMarker,
    name: OsString,
    child_marker: Option<FileMarker>,
    hardened: bool,
    armed: bool,
}

impl ProvisionalSnapshotChild {
    fn new(
        parent: CapDir,
        parent_file: File,
        parent_marker: FileMarker,
        parent_policy_marker: SnapshotParentPolicyMarker,
        name: OsString,
    ) -> Self {
        Self {
            parent: Some(parent),
            parent_file: Some(parent_file),
            parent_marker,
            parent_policy_marker,
            name,
            child_marker: None,
            hardened: false,
            armed: true,
        }
    }

    fn record_child_marker(&mut self, marker: FileMarker) -> Result<()> {
        if self.child_marker.is_some_and(|expected| expected != marker) {
            return Err(HeleosError::PolicyDenied);
        }
        self.child_marker = Some(marker);
        Ok(())
    }

    fn record_hardened(&mut self) {
        self.hardened = true;
    }

    fn disarm(mut self) {
        self.armed = false;
    }

    fn cleanup_with_precedence(mut self, primary: HeleosError) -> HeleosError {
        match self.cleanup() {
            Ok(()) => primary,
            Err(cleanup) => cleanup,
        }
    }

    fn recheck_created_child(&mut self) -> Result<()> {
        ensure_one_normal_component(&self.name)?;
        let parent_file = self.parent_file.as_ref().ok_or(HeleosError::PolicyDenied)?;
        recheck_snapshot_parent_retained(
            parent_file,
            &self.parent_marker,
            &self.parent_policy_marker,
        )?;
        let parent = self.parent.as_ref().ok_or(HeleosError::PolicyDenied)?;
        let file = snapshot_open_operation(SnapshotOpenOperation::ProvisionalChildOpen, || {
            parent
                .open_with(
                    &self.name,
                    &snapshot_directory_open_options(PermissionPolicy::VerifyOnly),
                )
                .map_err(map_snapshot_binding_error)
                .map(cap_std::fs::File::into_std)
        })?;
        let metadata =
            snapshot_open_operation(SnapshotOpenOperation::ProvisionalChildMetadata, || {
                file.metadata().map_err(HeleosError::Io)
            })?;
        snapshot_open_operation(SnapshotOpenOperation::ProvisionalChildType, || {
            validate_directory_type_metadata(&metadata)
        })?;
        snapshot_open_operation(SnapshotOpenOperation::ProvisionalChildReparse, || {
            reject_reparse_point(&metadata)
        })?;
        snapshot_open_operation(SnapshotOpenOperation::ProvisionalChildMarker, || {
            self.record_child_marker(FileMarker::from_metadata(&metadata))
        })?;
        let child = snapshot_open_operation(
            SnapshotOpenOperation::ProvisionalChildCapabilityClone,
            || {
                Ok(CapDir::from_std_file(
                    file.try_clone().map_err(HeleosError::Io)?,
                ))
            },
        )?;
        let mut entries =
            snapshot_open_operation(SnapshotOpenOperation::ProvisionalChildIterator, || {
                child.entries().map_err(HeleosError::Io)
            })?;
        let first =
            snapshot_open_operation(SnapshotOpenOperation::ProvisionalChildFirstEntry, || {
                entries.next().transpose().map_err(HeleosError::Io)
            })?;
        snapshot_open_operation(SnapshotOpenOperation::ProvisionalChildEmptyDecision, || {
            if first.is_some() {
                Err(HeleosError::PolicyDenied)
            } else {
                Ok(())
            }
        })
    }

    fn cleanup(&mut self) -> Result<()> {
        if !self.armed {
            return Err(HeleosError::PolicyDenied);
        }
        // Cleanup is a single authority attempt. Once any fallible validation or removal starts,
        // Drop must not make a second decision against a potentially changed namespace.
        self.armed = false;
        #[cfg(test)]
        if take_provisional_cleanup_io_for_test() {
            return Err(HeleosError::Io(io::Error::other(
                "injected provisional cleanup failure",
            )));
        }
        ensure_one_normal_component(&self.name)?;
        let parent_file = self.parent_file.as_ref().ok_or(HeleosError::PolicyDenied)?;
        recheck_snapshot_parent_retained(
            parent_file,
            &self.parent_marker,
            &self.parent_policy_marker,
        )?;
        let parent = self.parent.as_ref().ok_or(HeleosError::PolicyDenied)?;
        let file = parent
            .open_with(
                &self.name,
                &snapshot_directory_open_options(PermissionPolicy::VerifyOnly),
            )
            .map_err(map_snapshot_binding_error)?
            .into_std();
        let metadata = file.metadata().map_err(HeleosError::Io)?;
        validate_directory_metadata(&metadata)?;
        if let Some(expected) = self.child_marker
            && FileMarker::from_metadata(&metadata) != expected
        {
            return Err(HeleosError::PolicyDenied);
        }
        if self.hardened {
            verify_private_permissions_on_handle(&file)?;
        }
        let child = CapDir::from_std_file(file.try_clone().map_err(HeleosError::Io)?);
        if child
            .entries()
            .map_err(HeleosError::Io)?
            .next()
            .transpose()
            .map_err(HeleosError::Io)?
            .is_some()
        {
            return Err(HeleosError::PolicyDenied);
        }
        drop(child);
        drop(file);
        parent.remove_dir_all(&self.name).map_err(|error| {
            if error.kind() == io::ErrorKind::NotFound {
                HeleosError::PolicyDenied
            } else {
                HeleosError::Io(error)
            }
        })?;
        match parent.symlink_metadata(&self.name) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Ok(_) => return Err(HeleosError::PolicyDenied),
            Err(error) => return Err(HeleosError::Io(error)),
        }
        Ok(())
    }
}

impl Drop for ProvisionalSnapshotChild {
    fn drop(&mut self) {
        if self.armed {
            let _ = self.cleanup();
        }
    }
}

#[derive(Debug)]
struct SnapshotLexicalCandidateError;

impl std::fmt::Display for SnapshotLexicalCandidateError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("snapshot candidate is not one normal component")
    }
}

impl std::error::Error for SnapshotLexicalCandidateError {}

#[derive(Debug)]
struct SnapshotCandidatePolicyError;

impl std::fmt::Display for SnapshotCandidatePolicyError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("retained snapshot parent policy changed during creation")
    }
}

impl std::error::Error for SnapshotCandidatePolicyError {}

fn snapshot_lexical_candidate_error() -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, SnapshotLexicalCandidateError)
}

fn map_snapshot_name_generation_error(error: io::Error) -> HeleosError {
    let is_policy = error.get_ref().is_some_and(|source| {
        source
            .downcast_ref::<SnapshotLexicalCandidateError>()
            .is_some()
            || source
                .downcast_ref::<SnapshotCandidatePolicyError>()
                .is_some()
    });
    if is_policy {
        HeleosError::PolicyDenied
    } else {
        HeleosError::Io(error)
    }
}

struct ReadSnapshotDirectory {
    parent_path: PathBuf,
    parent_file: File,
    parent_marker: FileMarker,
    parent_policy_marker: SnapshotParentPolicyMarker,
    parent: CapDir,
    child_name: OsString,
    child_path: PathBuf,
    child_file: Option<File>,
    child_directory: Option<CapDir>,
    child_marker: Option<FileMarker>,
    database_path: PathBuf,
    database_file: Option<File>,
    database_marker: Option<FileMarker>,
    artifact_markers: Vec<SnapshotArtifactMarker>,
    state: SnapshotCleanupState,
    #[cfg(test)]
    cleanup_events: Option<SnapshotCleanupEvents>,
    #[cfg(test)]
    cleanup_io_fault: bool,
    #[cfg(test)]
    cleanup_remove_io_fault: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
struct SnapshotArtifactMarker {
    name: OsString,
    marker: FileMarker,
    private: bool,
}

impl ReadSnapshotDirectory {
    fn create(parent: &ReadOnlySnapshotParent) -> Result<Self> {
        Self::create_impl(parent, SnapshotCreateControl::Production)
    }

    #[cfg(test)]
    fn create_with_test_fault(
        parent: &ReadOnlySnapshotParent,
        fault: SnapshotCreateTestFault,
    ) -> Result<Self> {
        let control = match fault {
            SnapshotCreateTestFault::AfterChildOpenIo => SnapshotCreateControl::AfterChildOpenIo,
            SnapshotCreateTestFault::ImmediatelyAfterChildCreateIo => {
                SnapshotCreateControl::ImmediatelyAfterChildCreateIo
            }
        };
        Self::create_impl(parent, control)
    }

    fn create_impl(
        parent: &ReadOnlySnapshotParent,
        control: SnapshotCreateControl,
    ) -> Result<Self> {
        #[cfg(not(test))]
        let _ = control;
        parent.recheck()?;
        let creation_parent = CapDir::from_std_file(
            parent
                .retained_file()
                .try_clone()
                .map_err(HeleosError::Io)?,
        );
        let cleanup_parent = CapDir::from_std_file(
            parent
                .retained_file()
                .try_clone()
                .map_err(HeleosError::Io)?,
        );
        let cleanup_file = parent
            .retained_file()
            .try_clone()
            .map_err(HeleosError::Io)?;
        let mut cleanup_parent = Some(cleanup_parent);
        let mut cleanup_file = Some(cleanup_file);
        let mut builder = tempfile::Builder::new();
        builder
            .prefix("heleos-read-snapshot-")
            .suffix("")
            .rand_bytes(32)
            .disable_cleanup(true);
        let generated = builder
            .make_in(parent.path(), |candidate| {
                let relative = candidate
                    .strip_prefix(parent.path())
                    .map_err(|_| snapshot_lexical_candidate_error())?;
                let mut components = relative.components();
                let name = match (components.next(), components.next()) {
                    (Some(Component::Normal(name)), None) => name.to_os_string(),
                    _ => return Err(snapshot_lexical_candidate_error()),
                };
                recheck_snapshot_parent_retained(
                    cleanup_file
                        .as_ref()
                        .expect("make_in stops after one successful creation"),
                    &parent.marker,
                    &parent.policy_marker,
                )
                .map_err(snapshot_candidate_parent_error)?;
                #[cfg(test)]
                install_first_generated_candidate_collision_for_test(
                    parent.path(),
                    &creation_parent,
                    &name,
                )?;
                create_private_snapshot_directory(&creation_parent, &name)?;
                Ok(ProvisionalSnapshotChild::new(
                    cleanup_parent
                        .take()
                        .expect("make_in stops after one successful creation"),
                    cleanup_file
                        .take()
                        .expect("make_in stops after one successful creation"),
                    parent.marker,
                    parent.policy_marker.clone(),
                    name,
                ))
            })
            .map_err(map_snapshot_name_generation_error)?;
        let (mut provisional, disabled_temp_path) = generated.into_parts();
        if let Err(primary) =
            snapshot_open_operation(SnapshotOpenOperation::IntoPartsTransfer, || Ok(()))
        {
            return Err(provisional.cleanup_with_precedence(primary));
        }
        #[cfg(test)]
        if control == SnapshotCreateControl::ImmediatelyAfterChildCreateIo {
            return Err(
                provisional.cleanup_with_precedence(HeleosError::Io(io::Error::other(
                    "injected failure immediately after snapshot child create",
                ))),
            );
        }
        if let Err(primary) =
            snapshot_open_operation(SnapshotOpenOperation::ProvisionalFirstBinding, || {
                provisional.recheck_created_child()
            })
        {
            return Err(provisional.cleanup_with_precedence(primary));
        }
        #[cfg(test)]
        record_snapshot_temp_path_event(SnapshotTempPathEvent::VerifiedBeforeDrop);
        if let Err(primary) =
            snapshot_open_operation(SnapshotOpenOperation::DisabledTempPathDrop, || {
                drop(disabled_temp_path);
                Ok(())
            })
        {
            return Err(provisional.cleanup_with_precedence(primary));
        }
        if let Err(primary) =
            snapshot_open_operation(SnapshotOpenOperation::ProvisionalSecondBinding, || {
                provisional.recheck_created_child()
            })
        {
            return Err(provisional.cleanup_with_precedence(primary));
        }
        #[cfg(test)]
        record_snapshot_temp_path_event(SnapshotTempPathEvent::VerifiedAfterDrop);
        Self::finish_provisional(parent, provisional, control)
    }

    fn finish_provisional(
        parent: &ReadOnlySnapshotParent,
        mut provisional: ProvisionalSnapshotChild,
        control: SnapshotCreateControl,
    ) -> Result<Self> {
        let assembly = Self::assemble_cleanup_capable(parent, &mut provisional, control);
        match assembly {
            Ok(scratch) => {
                // Every cleanup-capable field is installed and no fallible action follows this
                // ownership handoff.
                provisional.disarm();
                Ok(scratch)
            }
            Err(primary) => Err(provisional.cleanup_with_precedence(primary)),
        }
    }

    fn assemble_cleanup_capable(
        parent: &ReadOnlySnapshotParent,
        provisional: &mut ProvisionalSnapshotChild,
        control: SnapshotCreateControl,
    ) -> Result<Self> {
        #[cfg(not(test))]
        let _ = control;
        let owned_parent_file = parent
            .retained_file()
            .try_clone()
            .map_err(HeleosError::Io)?;
        let parent_capability = CapDir::from_std_file(
            parent
                .retained_file()
                .try_clone()
                .map_err(HeleosError::Io)?,
        );
        let child_name = provisional.name.clone();
        let child_path = parent.path().join(&child_name);
        let database_path = child_path.join("snapshot.sqlite3");
        let mut scratch = Self {
            parent_path: parent.path().to_owned(),
            parent_file: owned_parent_file,
            parent_marker: parent.marker,
            parent_policy_marker: parent.policy_marker.clone(),
            parent: parent_capability,
            child_name,
            child_path,
            child_file: None,
            child_directory: None,
            child_marker: None,
            database_path,
            database_file: None,
            database_marker: None,
            artifact_markers: Vec::new(),
            state: SnapshotCleanupState::Uncertain,
            #[cfg(test)]
            cleanup_events: None,
            #[cfg(test)]
            cleanup_io_fault: false,
            #[cfg(test)]
            cleanup_remove_io_fault: false,
        };
        snapshot_open_operation(SnapshotOpenOperation::ChildOpen, || {
            scratch.install_child_cleanup_capability(provisional)
        })?;
        snapshot_open_operation(SnapshotOpenOperation::ChildHardening, || {
            scratch.finish_child_hardening(provisional)
        })?;
        #[cfg(test)]
        if control == SnapshotCreateControl::AfterChildOpenIo {
            return Err(HeleosError::Io(io::Error::other(
                "injected post-create snapshot failure",
            )));
        }
        snapshot_open_operation(SnapshotOpenOperation::ChildReady, || {
            scratch.recheck_parent_and_child()
        })?;
        scratch.state = SnapshotCleanupState::Owned;
        Ok(scratch)
    }

    fn install_child_cleanup_capability(
        &mut self,
        provisional: &mut ProvisionalSnapshotChild,
    ) -> Result<()> {
        let options = snapshot_directory_open_options(PermissionPolicy::ApplyAndVerify);
        let cap_file = snapshot_open_operation(SnapshotOpenOperation::ChildRetainedOpen, || {
            self.parent
                .open_with(&self.child_name, &options)
                .map_err(map_snapshot_binding_error)
        })?;
        let child_file = cap_file.into_std();
        let metadata =
            snapshot_open_operation(SnapshotOpenOperation::ChildInitialMetadata, || {
                child_file.metadata().map_err(HeleosError::Io)
            })?;
        snapshot_open_operation(SnapshotOpenOperation::ChildType, || {
            validate_directory_type_metadata(&metadata)
        })?;
        snapshot_open_operation(SnapshotOpenOperation::ChildReparse, || {
            reject_reparse_point(&metadata)
        })?;
        snapshot_open_operation(SnapshotOpenOperation::ChildMarkerCapture, || {
            let marker = FileMarker::from_metadata(&metadata);
            provisional.record_child_marker(marker)?;
            self.child_marker = Some(marker);
            Ok(())
        })?;
        self.child_file = Some(child_file);
        let child_directory =
            snapshot_open_operation(SnapshotOpenOperation::ChildCapabilityClone, || {
                Ok(CapDir::from_std_file(
                    self.child_file
                        .as_ref()
                        .ok_or(HeleosError::PolicyDenied)?
                        .try_clone()
                        .map_err(HeleosError::Io)?,
                ))
            })?;
        self.child_directory = Some(child_directory);
        Ok(())
    }

    fn finish_child_hardening(&mut self, provisional: &mut ProvisionalSnapshotChild) -> Result<()> {
        self.require_child_empty(SnapshotChildEmptyPhase::BeforeHardening)?;
        #[cfg(test)]
        record_snapshot_first_byte_event(SnapshotFirstByteEvent::ChildEmptyBeforeHardening);
        snapshot_open_operation(SnapshotOpenOperation::ChildPrivateApply, || {
            apply_private_permissions_to_handle(
                self.child_file.as_mut().ok_or(HeleosError::PolicyDenied)?,
            )?;
            provisional.record_hardened();
            Ok(())
        })?;
        snapshot_open_operation(SnapshotOpenOperation::ChildPrivateReadback, || {
            verify_private_permissions_on_handle(
                self.child_file.as_ref().ok_or(HeleosError::PolicyDenied)?,
            )
        })?;
        self.require_child_empty(SnapshotChildEmptyPhase::AfterHardening)?;
        #[cfg(test)]
        record_snapshot_first_byte_event(SnapshotFirstByteEvent::ChildEmptyAfterHardening);
        snapshot_open_operation(SnapshotOpenOperation::ChildRelativeBinding, || {
            self.recheck_parent_and_child()
        })?;
        self.require_child_empty(SnapshotChildEmptyPhase::AfterHardening)
    }

    fn require_child_empty(&self, phase: SnapshotChildEmptyPhase) -> Result<()> {
        let (iterator_step, first_entry_step, decision_step) = match phase {
            SnapshotChildEmptyPhase::BeforeHardening => (
                SnapshotOpenOperation::ChildEmptyBeforeIterator,
                SnapshotOpenOperation::ChildEmptyBeforeFirstEntry,
                SnapshotOpenOperation::ChildEmptyBeforeDecision,
            ),
            SnapshotChildEmptyPhase::AfterHardening => (
                SnapshotOpenOperation::ChildEmptyAfterIterator,
                SnapshotOpenOperation::ChildEmptyAfterFirstEntry,
                SnapshotOpenOperation::ChildEmptyAfterDecision,
            ),
        };
        let mut entries = snapshot_open_operation(iterator_step, || {
            self.child_directory()?.entries().map_err(HeleosError::Io)
        })?;
        let first = snapshot_open_operation(first_entry_step, || {
            entries.next().transpose().map_err(HeleosError::Io)
        })?;
        snapshot_open_operation(decision_step, || {
            if first.is_some() {
                Err(HeleosError::PolicyDenied)
            } else {
                Ok(())
            }
        })
    }

    fn checkpoint_before_database_creation(&self) -> Result<()> {
        self.require_child_empty(SnapshotChildEmptyPhase::AfterHardening)?;
        let child_file = self.child_file.as_ref().ok_or(HeleosError::PolicyDenied)?;
        record_snapshot_first_byte_witness(
            SnapshotFirstByteCheckpoint::DatabaseCreation,
            child_file,
            true,
            None,
        )
    }

    fn checkpoint_file_before_first_byte(
        &self,
        checkpoint: SnapshotFirstByteCheckpoint,
        file: &File,
    ) -> Result<()> {
        let metadata = file.metadata().map_err(HeleosError::Io)?;
        validate_regular_metadata(&metadata)?;
        if metadata.len() != 0 {
            return Err(HeleosError::PolicyDenied);
        }
        record_snapshot_first_byte_witness(checkpoint, file, false, Some(metadata.len()))
    }

    fn checkpoint_named_file_before_first_byte(
        &self,
        name: &OsStr,
        checkpoint: SnapshotFirstByteCheckpoint,
    ) -> Result<()> {
        ensure_allowed_snapshot_name(name)?;
        let file = self
            .child_directory()?
            .open_with(name, &snapshot_file_binding_options())
            .map_err(map_snapshot_binding_error)?
            .into_std();
        self.checkpoint_file_before_first_byte(checkpoint, &file)
    }

    fn path(&self) -> &Path {
        &self.child_path
    }

    #[cfg(test)]
    fn child_name(&self) -> &OsStr {
        &self.child_name
    }

    fn database_path(&self) -> &Path {
        &self.database_path
    }

    fn child_directory(&self) -> Result<&CapDir> {
        self.child_directory
            .as_ref()
            .ok_or(HeleosError::PolicyDenied)
    }

    fn copy_database(&mut self, source: &File, expected_bytes: u64) -> Result<()> {
        let (file, marker) =
            self.copy_file(source, OsStr::new("snapshot.sqlite3"), expected_bytes)?;
        self.database_file = Some(file);
        self.database_marker = Some(marker);
        self.recheck_all()
    }

    fn copy_wal(&mut self, source: &File, expected_bytes: u64) -> Result<()> {
        let (file, marker) =
            self.copy_file(source, OsStr::new("snapshot.sqlite3-wal"), expected_bytes)?;
        self.recheck_relative_regular_file(OsStr::new("snapshot.sqlite3-wal"), &file, &marker)?;
        drop(file);
        Ok(())
    }

    fn prepare_sqlite_sidecars(&mut self, wal_already_exists: bool) -> Result<()> {
        if !wal_already_exists {
            self.create_empty_private_file(OsStr::new("snapshot.sqlite3-wal"))?;
        }
        self.create_empty_private_file(OsStr::new("snapshot.sqlite3-shm"))
    }

    fn create_empty_private_file(&mut self, name: &OsStr) -> Result<()> {
        ensure_allowed_snapshot_name(name)?;
        let operations = snapshot_artifact_operations(name)?;
        let mut file = snapshot_open_operation(operations.create, || {
            let file = self
                .child_directory()?
                .open_with(name, &snapshot_file_create_options())
                .map_err(HeleosError::Io)?
                .into_std();
            let metadata = file.metadata().map_err(HeleosError::Io)?;
            validate_regular_metadata(&metadata)?;
            if metadata.len() != 0 {
                return Err(HeleosError::PolicyDenied);
            }
            self.record_artifact_marker(name, FileMarker::from_metadata(&metadata))?;
            Ok(file)
        })?;
        let metadata = snapshot_open_operation(operations.initial_metadata, || {
            file.metadata().map_err(HeleosError::Io)
        })?;
        snapshot_open_operation(operations.type_check, || {
            validate_regular_type_metadata(&metadata)
        })?;
        snapshot_open_operation(operations.reparse, || reject_reparse_point(&metadata))?;
        snapshot_open_operation(operations.zero_length, || {
            if metadata.len() == 0 {
                Ok(())
            } else {
                Err(HeleosError::PolicyDenied)
            }
        })?;
        snapshot_open_operation(operations.private_apply, || {
            apply_private_permissions_to_handle(&mut file)?;
            self.record_artifact_private(name)
        })?;
        snapshot_open_operation(operations.private_readback, || {
            verify_private_permissions_on_handle(&file)
        })?;
        snapshot_open_operation(operations.second_zero_length, || {
            if file.metadata().map_err(HeleosError::Io)?.len() == 0 {
                Ok(())
            } else {
                Err(HeleosError::PolicyDenied)
            }
        })?;
        let marker = snapshot_open_operation(operations.marker, || {
            let marker = FileMarker::from_metadata(&file.metadata().map_err(HeleosError::Io)?);
            self.record_artifact_marker(name, marker)?;
            Ok(marker)
        })?;
        #[cfg(test)]
        record_snapshot_private_empty_file_event(name)?;
        snapshot_open_operation(operations.flush, || file.flush().map_err(HeleosError::Io))?;
        snapshot_open_operation(operations.sync, || file.sync_all().map_err(HeleosError::Io))?;
        snapshot_open_operation(operations.relative_binding, || {
            self.recheck_relative_regular_file(name, &file, &marker)
        })?;
        snapshot_open_operation(operations.ambient_binding, || {
            recheck_snapshot_file_ambient(&self.child_path.join(name), &file, &marker)
        })
    }

    fn copy_file(
        &mut self,
        source: &File,
        name: &OsStr,
        expected_bytes: u64,
    ) -> Result<(File, FileMarker)> {
        ensure_one_normal_component(name)?;
        let operations = snapshot_artifact_operations(name)?;
        let copy_operations = snapshot_copy_operations(name)?;
        let is_database = name == OsStr::new("snapshot.sqlite3");
        if name == OsStr::new("snapshot.sqlite3") {
            self.checkpoint_before_database_creation()?;
        }
        let mut source = snapshot_open_operation(copy_operations.source_clone, || {
            source.try_clone().map_err(HeleosError::Io)
        })?;
        snapshot_open_operation(copy_operations.source_seek, || {
            source
                .seek(SeekFrom::Start(0))
                .map(|_| ())
                .map_err(HeleosError::Io)
        })?;
        let options = snapshot_file_create_options();
        let mut destination = snapshot_open_operation(operations.create, || {
            let destination = self
                .child_directory()?
                .open_with(name, &options)
                .map_err(HeleosError::Io)?
                .into_std();
            let metadata = destination.metadata().map_err(HeleosError::Io)?;
            validate_regular_metadata(&metadata)?;
            if metadata.len() != 0 {
                return Err(HeleosError::PolicyDenied);
            }
            self.record_artifact_marker(name, FileMarker::from_metadata(&metadata))?;
            Ok(destination)
        })?;
        // The empty file is private and verified before the first database or WAL byte.
        let metadata = snapshot_open_operation(operations.initial_metadata, || {
            destination.metadata().map_err(HeleosError::Io)
        })?;
        snapshot_open_operation(operations.type_check, || {
            validate_regular_type_metadata(&metadata)
        })?;
        snapshot_open_operation(operations.reparse, || reject_reparse_point(&metadata))?;
        snapshot_open_operation(operations.zero_length, || {
            if metadata.len() == 0 {
                Ok(())
            } else {
                Err(HeleosError::PolicyDenied)
            }
        })?;
        snapshot_open_operation(operations.private_apply, || {
            apply_private_permissions_to_handle(&mut destination)?;
            self.record_artifact_private(name)
        })?;
        snapshot_open_operation(operations.private_readback, || {
            verify_private_permissions_on_handle(&destination)
        })?;
        snapshot_open_operation(operations.second_zero_length, || {
            if destination.metadata().map_err(HeleosError::Io)?.len() == 0 {
                Ok(())
            } else {
                Err(HeleosError::PolicyDenied)
            }
        })?;
        let marker = snapshot_open_operation(operations.marker, || {
            let marker =
                FileMarker::from_metadata(&destination.metadata().map_err(HeleosError::Io)?);
            self.record_artifact_marker(name, marker)?;
            Ok(marker)
        })?;
        if is_database {
            snapshot_open_operation(SnapshotOpenOperation::DatabaseHandleClone, || {
                self.database_file = Some(destination.try_clone().map_err(HeleosError::Io)?);
                self.database_marker = Some(marker);
                Ok(())
            })?;
        }
        #[cfg(test)]
        record_snapshot_private_empty_file_event(name)?;
        if name == OsStr::new("snapshot.sqlite3") {
            self.checkpoint_file_before_first_byte(
                SnapshotFirstByteCheckpoint::FirstDatabaseWrite,
                &destination,
            )?;
        } else if name == OsStr::new("snapshot.sqlite3-wal") {
            self.checkpoint_file_before_first_byte(
                SnapshotFirstByteCheckpoint::FirstWalWrite,
                &destination,
            )?;
        }
        if is_database {
            snapshot_open_operation(SnapshotOpenOperation::DatabaseCopyBytes, || {
                copy_snapshot_chunks(
                    &mut source,
                    &mut destination,
                    expected_bytes,
                    copy_operations,
                )
            })?;
        } else {
            copy_snapshot_chunks(
                &mut source,
                &mut destination,
                expected_bytes,
                copy_operations,
            )?;
        }
        snapshot_open_operation(operations.flush, || {
            destination.flush().map_err(HeleosError::Io)
        })?;
        snapshot_open_operation(operations.sync, || {
            destination.sync_all().map_err(HeleosError::Io)
        })?;
        snapshot_open_operation(copy_operations.post_write_privacy, || {
            verify_private_permissions_on_handle(&destination)
        })?;
        let marker = FileMarker::from_metadata(&destination.metadata().map_err(HeleosError::Io)?);
        self.record_artifact_marker(name, marker)?;
        snapshot_open_operation(copy_operations.relative_binding, || {
            self.recheck_relative_regular_file(name, &destination, &marker)?;
            Ok(())
        })?;
        snapshot_open_operation(copy_operations.ambient_binding, || {
            recheck_snapshot_file_ambient(&self.child_path.join(name), &destination, &marker)
        })?;
        Ok((destination, marker))
    }

    fn record_artifact_marker(&mut self, name: &OsStr, marker: FileMarker) -> Result<()> {
        ensure_allowed_snapshot_name(name)?;
        if let Some(expected) = self
            .artifact_markers
            .iter_mut()
            .find(|expected| expected.name == name)
        {
            if expected.marker != marker {
                return Err(HeleosError::PolicyDenied);
            }
            return Ok(());
        }
        self.artifact_markers.push(SnapshotArtifactMarker {
            name: name.to_os_string(),
            marker,
            private: false,
        });
        Ok(())
    }

    fn record_artifact_private(&mut self, name: &OsStr) -> Result<()> {
        let artifact = self
            .artifact_markers
            .iter_mut()
            .find(|artifact| artifact.name == name)
            .ok_or(HeleosError::PolicyDenied)?;
        artifact.private = true;
        Ok(())
    }

    fn artifact_marker(&self, name: &OsStr) -> Option<FileMarker> {
        self.artifact_markers
            .iter()
            .find(|artifact| artifact.name == name)
            .map(|artifact| artifact.marker)
    }

    fn artifact_is_private(&self, name: &OsStr) -> bool {
        self.artifact_markers
            .iter()
            .find(|artifact| artifact.name == name)
            .is_some_and(|artifact| artifact.private)
    }

    fn verify_recovered_files_are_private(&mut self) -> Result<()> {
        let names = self.entry_names(
            SnapshotOpenOperation::RecoveredPrivacyIterator,
            SnapshotOpenOperation::RecoveredPrivacyEntryRead,
        )?;
        for name in &names {
            snapshot_open_operation(SnapshotOpenOperation::RecoveredPrivacyAllowedName, || {
                ensure_allowed_snapshot_name(name)
            })?;
            let options = snapshot_file_binding_options();
            let cap_file =
                snapshot_open_operation(SnapshotOpenOperation::RecoveredPrivacyOpen, || {
                    self.child_directory()?
                        .open_with(name, &options)
                        .map_err(map_snapshot_binding_error)
                })?;
            let file = cap_file.into_std();
            let metadata =
                snapshot_open_operation(SnapshotOpenOperation::RecoveredPrivacyMetadata, || {
                    file.metadata().map_err(HeleosError::Io)
                })?;
            snapshot_open_operation(SnapshotOpenOperation::RecoveredPrivacyType, || {
                validate_regular_type_metadata(&metadata)
            })?;
            snapshot_open_operation(SnapshotOpenOperation::RecoveredPrivacyReparse, || {
                reject_reparse_point(&metadata)
            })?;
            snapshot_open_operation(SnapshotOpenOperation::RecoveredPrivacyPolicy, || {
                verify_private_permissions_on_handle(&file)
            })?;
            snapshot_open_operation(SnapshotOpenOperation::RecoveredPrivacyMarker, || {
                let marker = FileMarker::from_metadata(&metadata);
                if name == OsStr::new("snapshot.sqlite3") {
                    if self.database_marker != Some(marker) {
                        return Err(HeleosError::PolicyDenied);
                    }
                } else if let Some(expected) = self
                    .artifact_markers
                    .iter_mut()
                    .find(|expected| expected.name == *name)
                {
                    // SQLite may remove and recreate its own pre-hardened sidecars during
                    // recovery. This post-recovery site binds the controlled replacement.
                    expected.marker = marker;
                    expected.private = true;
                } else {
                    return Err(HeleosError::PolicyDenied);
                }
                Ok(())
            })?;
        }
        snapshot_open_operation(SnapshotOpenOperation::RecoveredPrivacyFinalBinding, || {
            self.recheck_all()
        })
    }

    fn validate_recovered(
        &self,
        lengths: SnapshotSourceLengths,
        capacity_mode: SnapshotCapacityMode,
    ) -> Result<()> {
        let mut logical_bytes = 0_u64;
        let mut database_found = false;
        for name in self.entry_names(
            SnapshotOpenOperation::RecoveredLayoutIterator,
            SnapshotOpenOperation::RecoveredLayoutEntryRead,
        )? {
            snapshot_open_operation(SnapshotOpenOperation::RecoveredLayoutAllowedName, || {
                ensure_allowed_snapshot_name(&name)
            })?;
            database_found |= name == OsStr::new("snapshot.sqlite3");
            let options = snapshot_file_binding_options();
            let file = snapshot_open_operation(SnapshotOpenOperation::RecoveredLayoutOpen, || {
                self.child_directory()?
                    .open_with(&name, &options)
                    .map_err(map_snapshot_cap_open_error)
                    .map(cap_std::fs::File::into_std)
            })?;
            let metadata =
                snapshot_open_operation(SnapshotOpenOperation::RecoveredLayoutMetadata, || {
                    file.metadata().map_err(HeleosError::Io)
                })?;
            snapshot_open_operation(SnapshotOpenOperation::RecoveredLayoutType, || {
                validate_regular_type_metadata(&metadata)
            })?;
            snapshot_open_operation(SnapshotOpenOperation::RecoveredLayoutReparse, || {
                reject_reparse_point(&metadata)
            })?;
            snapshot_open_operation(SnapshotOpenOperation::RecoveredLayoutPrivacy, || {
                verify_private_permissions_on_handle(&file)
            })?;
            logical_bytes =
                snapshot_open_operation(SnapshotOpenOperation::RecoveredLayoutGrowthAdd, || {
                    logical_bytes
                        .checked_add(metadata.len())
                        .ok_or(HeleosError::PolicyDenied)
                })?;
        }
        snapshot_open_operation(SnapshotOpenOperation::RecoveredLayoutDecision, || {
            if !database_found || logical_bytes > lengths.maximum_staging_bytes {
                Err(HeleosError::PolicyDenied)
            } else {
                Ok(())
            }
        })?;
        if capacity_mode == SnapshotCapacityMode::StoreManaged {
            ensure_snapshot_reserve(self.path())?;
        }
        snapshot_open_operation(SnapshotOpenOperation::RecoveredLayoutFinalBinding, || {
            self.recheck_all()
        })
    }

    fn entry_names(
        &self,
        iterator_step: SnapshotOpenOperation,
        entry_step: SnapshotOpenOperation,
    ) -> Result<Vec<OsString>> {
        let mut names = Vec::new();
        let mut entries = snapshot_open_operation(iterator_step, || {
            self.child_directory()?.entries().map_err(HeleosError::Io)
        })?;
        loop {
            let entry = snapshot_open_operation(entry_step, || {
                entries.next().transpose().map_err(HeleosError::Io)
            })?;
            let Some(entry) = entry else {
                break;
            };
            names.push(entry.file_name());
        }
        Ok(names)
    }

    fn recheck_all(&self) -> Result<()> {
        self.recheck_parent_and_child()?;
        if let (Some(file), Some(marker)) = (&self.database_file, &self.database_marker) {
            let marker = *marker;
            #[cfg(all(test, windows))]
            let marker =
                snapshot_endpoint_expected_marker_for_test(SnapshotEndpointAxis::Database, marker);
            self.recheck_relative_regular_file(OsStr::new("snapshot.sqlite3"), file, &marker)?;
            recheck_snapshot_file_ambient(&self.database_path, file, &marker)?;
            verify_private_permissions_on_handle(file)?;
        }
        Ok(())
    }

    fn sqlite_endpoint_barrier(&self, barrier: SnapshotEndpointBarrier) -> Result<()> {
        #[cfg(not(test))]
        let _ = barrier;
        #[cfg(test)]
        self.apply_snapshot_endpoint_fault_for_test(barrier)?;
        self.recheck_all()
    }

    #[cfg(test)]
    fn apply_snapshot_endpoint_fault_for_test(
        &self,
        barrier: SnapshotEndpointBarrier,
    ) -> Result<()> {
        SNAPSHOT_ENDPOINT_BARRIER_COUNTS.with(|counts| {
            counts.borrow_mut()[barrier.index()] += 1;
        });
        SNAPSHOT_ENDPOINT_FAULT.with(|fault| {
            let mut fault = fault.borrow_mut();
            let Some(fault) = fault.as_mut() else {
                return Ok(());
            };
            if fault.applied || fault.barrier != barrier {
                return Ok(());
            }
            let (original, moved) = match fault.axis {
                SnapshotEndpointAxis::Parent => (
                    self.parent_path.clone(),
                    self.parent_path.with_extension("endpoint-retained-parent"),
                ),
                SnapshotEndpointAxis::Child => (
                    self.child_path.clone(),
                    self.child_path.with_extension("endpoint-retained-child"),
                ),
                SnapshotEndpointAxis::Database => (
                    self.database_path.clone(),
                    self.database_path
                        .with_extension("endpoint-retained-database"),
                ),
            };
            #[cfg(windows)]
            if fault.mutation == SnapshotEndpointMutation::MarkerMismatch {
                fault.applied = true;
                fault.original = Some(original);
                fault.moved = None;
                return Ok(());
            }
            #[cfg(unix)]
            if fault.mutation == SnapshotEndpointMutation::PrivatePolicy {
                use std::os::unix::fs::PermissionsExt;

                let mode = match fault.axis {
                    SnapshotEndpointAxis::Parent | SnapshotEndpointAxis::Child => 0o777,
                    SnapshotEndpointAxis::Database => 0o666,
                };
                fs::set_permissions(&original, fs::Permissions::from_mode(mode))
                    .map_err(HeleosError::Io)?;
                fault.applied = true;
                fault.original = Some(original);
                fault.moved = None;
                return Ok(());
            }
            fs::rename(&original, &moved).map_err(HeleosError::Io)?;
            match fault.mutation {
                SnapshotEndpointMutation::Rebound => match fault.axis {
                    SnapshotEndpointAxis::Parent | SnapshotEndpointAxis::Child => {
                        fs::create_dir(&original).map_err(HeleosError::Io)?;
                        apply_private_permissions(&original)?;
                    }
                    SnapshotEndpointAxis::Database => {
                        fs::write(&original, b"snapshot endpoint decoy")
                            .map_err(HeleosError::Io)?;
                        apply_private_permissions(&original)?;
                    }
                },
                #[cfg(unix)]
                SnapshotEndpointMutation::Missing => {}
                #[cfg(unix)]
                SnapshotEndpointMutation::WrongType => match fault.axis {
                    SnapshotEndpointAxis::Parent | SnapshotEndpointAxis::Child => {
                        fs::write(&original, b"wrong endpoint type").map_err(HeleosError::Io)?;
                        apply_private_permissions(&original)?;
                    }
                    SnapshotEndpointAxis::Database => {
                        fs::create_dir(&original).map_err(HeleosError::Io)?;
                        apply_private_permissions(&original)?;
                    }
                },
                #[cfg(unix)]
                SnapshotEndpointMutation::SymlinkLoop => {
                    use std::os::unix::fs::symlink;

                    symlink(&original, &original).map_err(HeleosError::Io)?;
                }
                #[cfg(unix)]
                SnapshotEndpointMutation::PrivatePolicy => unreachable!(),
                #[cfg(windows)]
                SnapshotEndpointMutation::MarkerMismatch => unreachable!(),
            }
            fault.applied = true;
            fault.original = Some(original);
            fault.moved = Some(moved);
            Ok(())
        })
    }

    fn recheck_parent_and_child(&self) -> Result<()> {
        let parent_marker = self.parent_marker;
        #[cfg(all(test, windows))]
        let parent_marker =
            snapshot_endpoint_expected_marker_for_test(SnapshotEndpointAxis::Parent, parent_marker);
        recheck_snapshot_parent_retained(
            &self.parent_file,
            &parent_marker,
            &self.parent_policy_marker,
        )?;
        recheck_snapshot_parent_ambient(
            &self.parent_path,
            &parent_marker,
            &self.parent_policy_marker,
        )?;
        let child_file = self.child_file.as_ref().ok_or(HeleosError::PolicyDenied)?;
        let child_marker = self.child_marker.ok_or(HeleosError::PolicyDenied)?;
        #[cfg(all(test, windows))]
        let child_marker =
            snapshot_endpoint_expected_marker_for_test(SnapshotEndpointAxis::Child, child_marker);
        recheck_directory_identity(&self.child_path, child_file, &child_marker)?;
        let options = snapshot_directory_open_options(PermissionPolicy::VerifyOnly);
        let rebound = self
            .parent
            .open_with(&self.child_name, &options)
            .map_err(map_snapshot_cap_open_error)?
            .into_std();
        validate_directory_metadata(&rebound.metadata().map_err(HeleosError::Io)?)?;
        verify_private_permissions_on_handle(&rebound)?;
        if FileMarker::from_metadata(&rebound.metadata().map_err(HeleosError::Io)?) != child_marker
        {
            return Err(HeleosError::PolicyDenied);
        }
        Ok(())
    }

    fn recheck_relative_regular_file(
        &self,
        name: &OsStr,
        retained: &File,
        expected: &FileMarker,
    ) -> Result<()> {
        ensure_one_normal_component(name)?;
        let metadata = retained.metadata().map_err(HeleosError::Io)?;
        validate_regular_metadata(&metadata)?;
        if &FileMarker::from_metadata(&metadata) != expected {
            return Err(HeleosError::PolicyDenied);
        }
        let options = snapshot_file_binding_options();
        let rebound = self
            .child_directory()?
            .open_with(name, &options)
            .map_err(map_snapshot_cap_open_error)?
            .into_std();
        let rebound_metadata = rebound.metadata().map_err(HeleosError::Io)?;
        validate_regular_metadata(&rebound_metadata)?;
        verify_private_permissions_on_handle(&rebound)?;
        if &FileMarker::from_metadata(&rebound_metadata) != expected {
            return Err(HeleosError::PolicyDenied);
        }
        Ok(())
    }

    fn cleanup_error_precedence(self, primary: HeleosError) -> HeleosError {
        match self.close() {
            Ok(()) => primary,
            Err(cleanup) => cleanup,
        }
    }

    fn close(mut self) -> Result<()> {
        if self.state != SnapshotCleanupState::Owned {
            return Err(HeleosError::PolicyDenied);
        }
        // From this point onward any failure can leave namespace ownership uncertain. Latch the
        // state before the first fallible cleanup step so Drop never retries after an error.
        self.state = SnapshotCleanupState::Uncertain;
        self.database_file.take();
        #[cfg(test)]
        self.record_cleanup_event(SnapshotCleanupEvent::DatabaseHandleDropped);
        self.child_directory.take();
        self.child_file.take();
        #[cfg(test)]
        self.record_cleanup_event(SnapshotCleanupEvent::ChildHandlesDropped);
        recheck_snapshot_parent_retained(
            &self.parent_file,
            &self.parent_marker,
            &self.parent_policy_marker,
        )?;
        let ambient_parent_result = recheck_snapshot_parent_ambient(
            &self.parent_path,
            &self.parent_marker,
            &self.parent_policy_marker,
        );
        #[cfg(test)]
        self.record_cleanup_event(SnapshotCleanupEvent::AmbientParentChecked);
        self.recheck_retained_bindings_for_cleanup()?;
        #[cfg(test)]
        self.record_cleanup_event(SnapshotCleanupEvent::RelativeBindingsValidated);
        // Every handle reopened during the scoped validation above has now been dropped.
        #[cfg(test)]
        self.record_cleanup_event(SnapshotCleanupEvent::ValidationHandlesDropped);
        #[cfg(test)]
        if self.cleanup_io_fault {
            return Err(HeleosError::Io(io::Error::other(
                "injected checked snapshot cleanup failure",
            )));
        }
        // The validation handles above have left scope. This is the final retained-relative
        // checkpoint; the governed cap-std removal that follows is a separate operation. The
        // same-principal replacement interval between them is explicitly outside Foundation 0.1.
        self.recheck_retained_bindings_for_cleanup()?;
        #[cfg(test)]
        self.record_cleanup_event(SnapshotCleanupEvent::FinalRetainedCheckpoint);
        #[cfg(test)]
        if let Some(error) = take_snapshot_cleanup_fault_for_test() {
            return Err(error);
        }
        #[cfg(test)]
        if self.cleanup_remove_io_fault {
            return Err(HeleosError::Io(io::Error::other(
                "injected snapshot removal failure",
            )));
        }
        #[cfg(test)]
        if take_snapshot_cleanup_namespace_fault_for_test(
            SnapshotCleanupNamespaceFaultForTest::RemoveBeforeProductionRemove,
        ) {
            self.parent
                .remove_dir_all(&self.child_name)
                .map_err(HeleosError::Io)?;
        }
        // On Windows, pinned cap-std's one-component recursive removal internally resolves a
        // handle/path and closes it before deletion. Heleos supplies only this retained-parent,
        // one-component authority and has no ambient remove_dir_all fallback.
        self.parent
            .remove_dir_all(&self.child_name)
            .map_err(|error| {
                if error.kind() == io::ErrorKind::NotFound {
                    HeleosError::PolicyDenied
                } else {
                    HeleosError::Io(error)
                }
            })?;
        #[cfg(test)]
        self.record_cleanup_event(SnapshotCleanupEvent::EntryRemoved);
        #[cfg(test)]
        if take_snapshot_cleanup_namespace_fault_for_test(
            SnapshotCleanupNamespaceFaultForTest::InstallDecoyAfterRemove,
        ) {
            create_private_snapshot_directory(&self.parent, &self.child_name)
                .map_err(HeleosError::Io)?;
        }
        match self.parent.symlink_metadata(&self.child_name) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Ok(_) => {
                self.state = SnapshotCleanupState::Uncertain;
                return Err(HeleosError::PolicyDenied);
            }
            Err(error) => {
                self.state = SnapshotCleanupState::Uncertain;
                return Err(HeleosError::Io(error));
            }
        }
        #[cfg(test)]
        self.record_cleanup_event(SnapshotCleanupEvent::RelativeAbsenceVerified);
        self.state = SnapshotCleanupState::Cleaned;
        if ambient_parent_result.is_ok() {
            match fs::symlink_metadata(&self.child_path) {
                Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                Ok(_) => return Err(HeleosError::PolicyDenied),
                Err(error) => return Err(HeleosError::Io(error)),
            }
        }
        #[cfg(test)]
        self.record_cleanup_event(SnapshotCleanupEvent::AmbientAbsenceResolved);
        ambient_parent_result
    }

    fn recheck_retained_bindings_for_cleanup(&self) -> Result<()> {
        recheck_snapshot_parent_retained(
            &self.parent_file,
            &self.parent_marker,
            &self.parent_policy_marker,
        )?;
        let expected_child = self.child_marker.ok_or(HeleosError::PolicyDenied)?;
        let options = snapshot_directory_open_options(PermissionPolicy::VerifyOnly);
        let child_file = self
            .parent
            .open_with(&self.child_name, &options)
            .map_err(map_snapshot_binding_error)?
            .into_std();
        let child_metadata = child_file.metadata().map_err(HeleosError::Io)?;
        validate_directory_metadata(&child_metadata)?;
        verify_private_permissions_on_handle(&child_file)?;
        if FileMarker::from_metadata(&child_metadata) != expected_child {
            return Err(HeleosError::PolicyDenied);
        }
        let child = CapDir::from_std_file(child_file.try_clone().map_err(HeleosError::Io)?);
        let mut database_found = false;
        for entry in child.entries().map_err(HeleosError::Io)? {
            let name = entry.map_err(HeleosError::Io)?.file_name();
            ensure_allowed_snapshot_name(&name)?;
            let file = child
                .open_with(&name, &snapshot_file_binding_options())
                .map_err(map_snapshot_binding_error)?
                .into_std();
            let metadata = file.metadata().map_err(HeleosError::Io)?;
            validate_regular_metadata(&metadata)?;
            if self.artifact_is_private(&name) {
                verify_private_permissions_on_handle(&file)?;
            } else if metadata.len() != 0 {
                // Before the hardening operation succeeds, the only certainly-owned form is the
                // exact retained, regular, still-empty artifact. No sensitive byte was written.
                return Err(HeleosError::PolicyDenied);
            }
            let expected_artifact = self
                .artifact_marker(&name)
                .ok_or(HeleosError::PolicyDenied)?;
            if FileMarker::from_metadata(&metadata) != expected_artifact {
                return Err(HeleosError::PolicyDenied);
            }
            if name == OsStr::new("snapshot.sqlite3") {
                database_found = true;
                if let Some(expected_database) = self.database_marker
                    && FileMarker::from_metadata(&metadata) != expected_database
                {
                    return Err(HeleosError::PolicyDenied);
                }
            }
        }
        if let Some(expected_database) = self.database_marker {
            if !database_found {
                return Err(HeleosError::PolicyDenied);
            }
            let file = child
                .open_with("snapshot.sqlite3", &snapshot_file_binding_options())
                .map_err(map_snapshot_binding_error)?
                .into_std();
            let metadata = file.metadata().map_err(HeleosError::Io)?;
            validate_regular_metadata(&metadata)?;
            verify_private_permissions_on_handle(&file)?;
            if FileMarker::from_metadata(&metadata) != expected_database {
                return Err(HeleosError::PolicyDenied);
            }
        }
        Ok(())
    }

    fn best_effort_cleanup(&mut self) {
        if self.state != SnapshotCleanupState::Owned {
            return;
        }
        // Best-effort cleanup is one attempt. An error after this latch is never retried.
        self.state = SnapshotCleanupState::Uncertain;
        self.database_file.take();
        self.child_directory.take();
        self.child_file.take();
        if self.recheck_retained_bindings_for_cleanup().is_err() {
            self.state = SnapshotCleanupState::Uncertain;
            return;
        }
        if self.recheck_retained_bindings_for_cleanup().is_err() {
            self.state = SnapshotCleanupState::Uncertain;
            return;
        }
        #[cfg(test)]
        if take_snapshot_cleanup_fault_for_test().is_some() {
            return;
        }
        if self.parent.remove_dir_all(&self.child_name).is_err() {
            self.state = SnapshotCleanupState::Uncertain;
            return;
        }
        match self.parent.symlink_metadata(&self.child_name) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                self.state = SnapshotCleanupState::Cleaned;
            }
            _ => self.state = SnapshotCleanupState::Uncertain,
        }
    }

    #[cfg(test)]
    fn enable_cleanup_events_for_test(&mut self) -> SnapshotCleanupEvents {
        let events = std::rc::Rc::new(std::cell::RefCell::new(Vec::new()));
        self.cleanup_events = Some(std::rc::Rc::clone(&events));
        events
    }

    #[cfg(test)]
    fn cleanup_events_for_test(&self) -> Option<SnapshotCleanupEvents> {
        self.cleanup_events.as_ref().map(std::rc::Rc::clone)
    }

    #[cfg(test)]
    fn inject_cleanup_io_for_test(&mut self) {
        self.cleanup_io_fault = true;
    }

    #[cfg(test)]
    fn inject_cleanup_remove_io_for_test(&mut self) {
        self.cleanup_remove_io_fault = true;
    }

    #[cfg(test)]
    fn record_cleanup_event(&self, event: SnapshotCleanupEvent) {
        if let Some(events) = &self.cleanup_events {
            events.borrow_mut().push(event);
        }
    }
}

impl Drop for ReadSnapshotDirectory {
    fn drop(&mut self) {
        self.best_effort_cleanup();
    }
}

pub struct Store {
    // Rust drops fields in declaration order. For readers this closes SQLite before every scratch
    // handle/owner. Checked close additionally keeps source handles and the reader lock alive until
    // capability-relative scratch cleanup has completed.
    pub(super) connection: Connection,
    _snapshot_directory: Option<ReadSnapshotDirectory>,
    database_identity_handle: Option<File>,
    source_sidecar_handles: Vec<CheckedSourceFile>,
    writer_lock: Option<WriterLock>,
    reader_lock: Option<ReaderLock>,
    database_path: Option<PathBuf>,
    pub(super) read_only: bool,
    #[cfg(test)]
    commit_outcome_unknown_after: Option<usize>,
    #[cfg(test)]
    connection_close_failure: bool,
}

impl Store {
    pub fn open_writer(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref();
        check_parent(path, PermissionPolicy::ApplyAndVerify)?;
        let writer_lock = WriterLock::acquire(path)?;
        reject_invalid_existing_path(path)?;
        check_sqlite_sidecars(path, PermissionPolicy::ApplyAndVerify)?;
        let (database_file, identity) =
            open_checked_regular_file(path, true, true, PermissionPolicy::ApplyAndVerify)?;
        writer_lock.recheck_identity()?;

        let flags = OpenFlags::SQLITE_OPEN_READ_WRITE
            | OpenFlags::SQLITE_OPEN_NO_MUTEX
            | OpenFlags::SQLITE_OPEN_NOFOLLOW
            | OpenFlags::SQLITE_OPEN_PRIVATE_CACHE;
        let connection = Connection::open_with_flags(path, flags).map_err(database_error)?;
        recheck_file_identity(path, &database_file, &identity)?;
        configure_connection(&connection, ConnectionKind::FileWriter)?;
        recheck_file_identity(path, &database_file, &identity)?;

        let store = Self {
            connection,
            _snapshot_directory: None,
            database_identity_handle: Some(database_file),
            source_sidecar_handles: Vec::new(),
            writer_lock: Some(writer_lock),
            reader_lock: None,
            database_path: Some(path.to_owned()),
            read_only: false,
            #[cfg(test)]
            commit_outcome_unknown_after: None,
            #[cfg(test)]
            connection_close_failure: false,
        };
        store.harden_sqlite_sidecars()?;

        Ok(store)
    }

    pub fn open_read_only(path: impl AsRef<Path>) -> Result<Self> {
        Self::open_read_only_impl(path.as_ref(), SnapshotParentSelection::StoreManaged)
    }

    #[cfg_attr(
        not(test),
        expect(
            dead_code,
            reason = "BackupService composes caller-admitted Store scratch in the next reviewed slice"
        )
    )]
    pub(crate) fn open_read_only_with_snapshot_parent(
        path: &Path,
        snapshot_parent: &ReadOnlySnapshotParent,
    ) -> Result<Self> {
        snapshot_parent.recheck()?;
        Self::open_read_only_impl(
            path,
            SnapshotParentSelection::CallerAdmitted(snapshot_parent),
        )
    }

    fn open_read_only_impl(
        path: &Path,
        parent_selection: SnapshotParentSelection<'_>,
    ) -> Result<Self> {
        check_parent(path, PermissionPolicy::VerifyOnly)?;
        let reader_lock = ReaderLock::acquire(path)?;
        reject_invalid_existing_path(path)?;
        check_sqlite_sidecars(path, PermissionPolicy::VerifyOnly)?;
        check_parent(path, PermissionPolicy::VerifyOnly)?;
        reject_invalid_existing_path(path)?;
        check_sqlite_sidecars(path, PermissionPolicy::VerifyOnly)?;
        let (database_file, identity) =
            open_checked_regular_file(path, false, false, PermissionPolicy::VerifyOnly)?;
        let wal_path = sidecar_path(path, "-wal");
        let shm_path = sidecar_path(path, "-shm");
        let wal_file = open_optional_verified_file(&wal_path)?;
        let shm_file = open_optional_verified_file(&shm_path)?;
        let source_lengths = snapshot_source_lengths(&database_file, wal_file.as_ref())?;
        let retained_default;
        let (snapshot_parent, capacity_mode) = match parent_selection {
            SnapshotParentSelection::StoreManaged => {
                retained_default = ReadOnlySnapshotParent::retain_default()?;
                (&retained_default, SnapshotCapacityMode::StoreManaged)
            }
            SnapshotParentSelection::CallerAdmitted(parent) => {
                parent.recheck()?;
                (parent, SnapshotCapacityMode::CallerAdmitted)
            }
        };
        let mut snapshot_directory = ReadSnapshotDirectory::create(snapshot_parent)?;

        let construction = (|| -> Result<Connection> {
            if capacity_mode == SnapshotCapacityMode::StoreManaged {
                ensure_snapshot_capacity(snapshot_directory.path(), source_lengths)?;
            }
            snapshot_open_operation(SnapshotOpenOperation::DatabaseCopy, || {
                snapshot_directory.copy_database(&database_file, source_lengths.database_bytes)
            })?;
            snapshot_open_operation(SnapshotOpenOperation::SidecarPreparation, || {
                if let Some(wal_file) = &wal_file {
                    snapshot_directory.copy_wal(&wal_file.file, source_lengths.wal_bytes)?;
                }
                snapshot_directory.prepare_sqlite_sidecars(wal_file.is_some())
            })?;
            snapshot_open_operation(SnapshotOpenOperation::SourcePreOpenRechecks, || {
                snapshot_open_operation(SnapshotOpenOperation::SourcePreLengths, || {
                    recheck_snapshot_source_lengths(
                        &database_file,
                        wal_file.as_ref(),
                        source_lengths,
                    )
                })?;
                snapshot_open_operation(SnapshotOpenOperation::SourcePreDatabaseIdentity, || {
                    recheck_file_identity(path, &database_file, &identity)
                })?;
                snapshot_open_operation(SnapshotOpenOperation::SourcePreWalIdentity, || {
                    recheck_optional_file(&wal_path, wal_file.as_ref())
                })?;
                snapshot_open_operation(SnapshotOpenOperation::SourcePreShmIdentity, || {
                    recheck_optional_file(&shm_path, shm_file.as_ref())
                })?;
                snapshot_open_operation(SnapshotOpenOperation::SourcePreReaderLock, || {
                    reader_lock.recheck_identity()
                })?;
                snapshot_open_operation(SnapshotOpenOperation::SourcePreParent, || {
                    check_parent(path, PermissionPolicy::VerifyOnly)
                })?;
                snapshot_open_operation(SnapshotOpenOperation::SourcePreSidecarPolicy, || {
                    check_sqlite_sidecars(path, PermissionPolicy::VerifyOnly)
                })
            })?;

            let flags = OpenFlags::SQLITE_OPEN_READ_WRITE
                | OpenFlags::SQLITE_OPEN_NO_MUTEX
                | OpenFlags::SQLITE_OPEN_NOFOLLOW
                | OpenFlags::SQLITE_OPEN_PRIVATE_CACHE;
            snapshot_open_operation(SnapshotOpenOperation::BeforeSqliteBarrier, || {
                snapshot_directory
                    .sqlite_endpoint_barrier(SnapshotEndpointBarrier::BeforeSqliteOpen)
            })?;
            // rusqlite requires an ambient pathname. These same-principal endpoint barriers catch
            // ordinary rebinding before/after the open; they do not claim continuous protection
            // against a transient Unix ABA replacement. A capability-aware VFS remains post-0.1.
            let connection = snapshot_open_operation(SnapshotOpenOperation::SqliteOpen, || {
                if wal_file.is_none() {
                    snapshot_directory.checkpoint_named_file_before_first_byte(
                        OsStr::new("snapshot.sqlite3-wal"),
                        SnapshotFirstByteCheckpoint::FirstWalWrite,
                    )?;
                }
                snapshot_directory.checkpoint_named_file_before_first_byte(
                    OsStr::new("snapshot.sqlite3-shm"),
                    SnapshotFirstByteCheckpoint::FirstShmWrite,
                )?;
                Connection::open_with_flags(snapshot_directory.database_path(), flags)
                    .map_err(database_error)
            })?;
            snapshot_open_operation(SnapshotOpenOperation::AfterSqliteBarrier, || {
                snapshot_directory.sqlite_endpoint_barrier(SnapshotEndpointBarrier::AfterSqliteOpen)
            })?;
            snapshot_open_operation(
                SnapshotOpenOperation::ConfigureSourceDatabaseIdentity,
                || recheck_file_identity(path, &database_file, &identity),
            )?;
            snapshot_open_operation(SnapshotOpenOperation::ConfigureAndRecover, || {
                configure_connection(&connection, ConnectionKind::FileReader)
            })?;
            snapshot_open_operation(SnapshotOpenOperation::AfterRecoveryBarrier, || {
                snapshot_directory
                    .sqlite_endpoint_barrier(SnapshotEndpointBarrier::AfterConfigurationAndRecovery)
            })?;
            snapshot_open_operation(SnapshotOpenOperation::SourcePostRecoveryRechecks, || {
                snapshot_open_operation(SnapshotOpenOperation::SourcePostLengths, || {
                    recheck_snapshot_source_lengths(
                        &database_file,
                        wal_file.as_ref(),
                        source_lengths,
                    )
                })?;
                snapshot_open_operation(SnapshotOpenOperation::SourcePostDatabaseIdentity, || {
                    recheck_file_identity(path, &database_file, &identity)
                })?;
                snapshot_open_operation(SnapshotOpenOperation::SourcePostWalIdentity, || {
                    recheck_optional_file(&wal_path, wal_file.as_ref())
                })?;
                snapshot_open_operation(SnapshotOpenOperation::SourcePostShmIdentity, || {
                    recheck_optional_file(&shm_path, shm_file.as_ref())
                })?;
                snapshot_open_operation(SnapshotOpenOperation::SourcePostReaderLock, || {
                    reader_lock.recheck_identity()
                })?;
                snapshot_open_operation(SnapshotOpenOperation::SourcePostParent, || {
                    check_parent(path, PermissionPolicy::VerifyOnly)
                })?;
                snapshot_open_operation(SnapshotOpenOperation::SourcePostSidecarPolicy, || {
                    check_sqlite_sidecars(path, PermissionPolicy::VerifyOnly)
                })
            })?;
            snapshot_open_operation(SnapshotOpenOperation::RecoveredPrivacy, || {
                snapshot_directory.verify_recovered_files_are_private()
            })?;
            snapshot_open_operation(
                SnapshotOpenOperation::RecoveredLayoutGrowthAndCapacity,
                || snapshot_directory.validate_recovered(source_lengths, capacity_mode),
            )?;
            snapshot_open_operation(SnapshotOpenOperation::FinalBarrier, || {
                snapshot_directory
                    .sqlite_endpoint_barrier(SnapshotEndpointBarrier::BeforeStoreReturn)
            })?;
            snapshot_open_operation(SnapshotOpenOperation::FieldOwnership, || {
                snapshot_directory.recheck_all()
            })?;
            snapshot_open_operation(SnapshotOpenOperation::StoreAssembly, || {
                snapshot_parent.recheck_retained_policy()
            })?;
            snapshot_open_operation(SnapshotOpenOperation::Return, || {
                reader_lock.recheck_identity()
            })?;
            Ok(connection)
        })();

        let connection = match construction {
            Ok(connection) => connection,
            Err(primary) => {
                return Err(snapshot_directory.cleanup_error_precedence(primary));
            }
        };

        Ok(Self {
            connection,
            _snapshot_directory: Some(snapshot_directory),
            database_identity_handle: Some(database_file),
            source_sidecar_handles: wal_file.into_iter().chain(shm_file).collect(),
            writer_lock: None,
            reader_lock: Some(reader_lock),
            database_path: Some(path.to_owned()),
            read_only: true,
            #[cfg(test)]
            commit_outcome_unknown_after: None,
            #[cfg(test)]
            connection_close_failure: false,
        })
    }

    pub fn open_in_memory() -> Result<Self> {
        let connection = Connection::open_in_memory().map_err(database_error)?;
        configure_connection(&connection, ConnectionKind::MemoryWriter)?;
        Ok(Self {
            connection,
            _snapshot_directory: None,
            database_identity_handle: None,
            source_sidecar_handles: Vec::new(),
            writer_lock: None,
            reader_lock: None,
            database_path: None,
            read_only: false,
            #[cfg(test)]
            commit_outcome_unknown_after: None,
            #[cfg(test)]
            connection_close_failure: false,
        })
    }

    #[cfg_attr(
        not(test),
        expect(
            dead_code,
            reason = "BackupService composes checked Store close in the next reviewed slice"
        )
    )]
    pub(crate) fn close_read_only(self) -> Result<()> {
        let Self {
            connection,
            mut _snapshot_directory,
            database_identity_handle,
            source_sidecar_handles,
            writer_lock,
            reader_lock,
            database_path,
            read_only,
            #[cfg(test)]
            commit_outcome_unknown_after,
            #[cfg(test)]
            connection_close_failure,
        } = self;

        if !read_only || writer_lock.is_some() || _snapshot_directory.is_none() {
            return Err(HeleosError::PolicyDenied);
        }

        #[cfg(test)]
        let cleanup_events = _snapshot_directory
            .as_ref()
            .and_then(ReadSnapshotDirectory::cleanup_events_for_test);

        let connection_result = match connection.close() {
            Ok(()) => Ok(()),
            Err((connection, _)) => {
                drop(connection);
                Err(HeleosError::Database)
            }
        };
        #[cfg(test)]
        let connection_result = if connection_close_failure && connection_result.is_ok() {
            Err(HeleosError::Database)
        } else {
            connection_result
        };
        #[cfg(test)]
        if let Some(snapshot_directory) = &_snapshot_directory {
            snapshot_directory.record_cleanup_event(SnapshotCleanupEvent::ConnectionClosed);
            snapshot_directory.record_cleanup_event(SnapshotCleanupEvent::ScratchUsersReleased);
        }
        let cleanup_result = _snapshot_directory
            .take()
            .ok_or(HeleosError::PolicyDenied)
            .and_then(ReadSnapshotDirectory::close);

        drop(database_identity_handle);
        drop(source_sidecar_handles);
        drop(database_path);
        #[cfg(test)]
        let _ = commit_outcome_unknown_after;
        drop(reader_lock);
        #[cfg(test)]
        if let Some(events) = cleanup_events {
            events
                .borrow_mut()
                .push(SnapshotCleanupEvent::SourceHandlesAndReaderLockDropped);
        }

        match cleanup_result {
            Err(cleanup) => Err(cleanup),
            Ok(()) => connection_result,
        }
    }

    /// Runs one crate-owned immediate transaction.
    ///
    /// Normal consumers cannot use this primitive to bypass audited repositories.
    ///
    /// ```compile_fail
    /// #![forbid(unsafe_code)]
    /// use heleos_core::Store;
    ///
    /// fn raw_write(store: &mut Store) {
    ///     let _ = store.with_immediate_transaction(|transaction| {
    ///         transaction.execute("DELETE FROM projects", []).unwrap();
    ///         Ok(())
    ///     });
    /// }
    /// ```
    pub(crate) fn with_immediate_transaction<T>(
        &mut self,
        operation: impl FnOnce(&Transaction<'_>) -> Result<T>,
    ) -> Result<T> {
        if self.read_only {
            return Err(HeleosError::PolicyDenied);
        }
        self.recheck_writer_lock_identity()?;
        self.recheck_database_identity()?;
        let transaction = self
            .connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(database_error)?;
        let value = operation(&transaction)?;
        transaction
            .commit()
            .map_err(|_| HeleosError::CommitOutcomeUnknown)?;
        #[cfg(test)]
        if let Some(remaining) = self.commit_outcome_unknown_after.take() {
            if remaining == 0 {
                return Err(HeleosError::CommitOutcomeUnknown);
            }
            self.commit_outcome_unknown_after = Some(remaining - 1);
        }
        self.recheck_writer_lock_identity()
            .and_then(|()| self.harden_sqlite_sidecars())
            .and_then(|()| self.recheck_database_identity())
            .map_err(|_| HeleosError::CommitOutcomeUnknown)?;
        Ok(value)
    }

    #[cfg(test)]
    pub(crate) fn inject_commit_outcome_unknown_after_for_test(
        &mut self,
        successful_commits_before_failure: usize,
    ) {
        self.commit_outcome_unknown_after = Some(successful_commits_before_failure);
    }

    #[cfg(test)]
    fn inject_connection_close_failure_for_test(&mut self) {
        self.connection_close_failure = true;
    }

    pub(crate) fn require_writer_capability(&self) -> Result<()> {
        if self.read_only {
            return Err(HeleosError::PolicyDenied);
        }
        self.recheck_writer_lock_identity()?;
        self.recheck_database_identity()
    }

    pub fn verify_integrity(&self) -> Result<IntegrityReport> {
        self.recheck_database_identity()?;
        let (integrity_check_violations, integrity_check_truncated) =
            collect_single_column_check(&self.connection, INTEGRITY_CHECK_SQL)?;
        let (quick_check_violations, quick_check_truncated) =
            collect_single_column_check(&self.connection, QUICK_CHECK_SQL)?;

        let mut statement = self
            .connection
            .prepare("PRAGMA foreign_key_check")
            .map_err(database_error)?;
        let rows = statement
            .query_map([], |row| {
                let table: String = row.get(0)?;
                let row_id: Option<i64> = row.get(1)?;
                let parent: String = row.get(2)?;
                let foreign_key_index: i64 = row.get(3)?;
                Ok(format!(
                    "table={table};row_id={row_id:?};parent={parent};foreign_key={foreign_key_index}"
                ))
            })
            .map_err(database_error)?;
        let mut foreign_key_violations = Vec::new();
        for row in rows {
            foreign_key_violations.push(row.map_err(database_error)?);
            if foreign_key_violations.len() > INTEGRITY_VIOLATION_LIMIT {
                break;
            }
        }
        let foreign_key_check_truncated = foreign_key_violations.len() > INTEGRITY_VIOLATION_LIMIT;
        foreign_key_violations.truncate(INTEGRITY_VIOLATION_LIMIT);

        Ok(IntegrityReport {
            integrity_check_violations,
            integrity_check_truncated,
            quick_check_violations,
            quick_check_truncated,
            foreign_key_violations,
            foreign_key_check_truncated,
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
            match open_checked_regular_file(&path, false, true, PermissionPolicy::ApplyAndVerify) {
                Ok(_) | Err(HeleosError::NotFound) => {}
                Err(error) => return Err(error),
            }
        }
        Ok(())
    }

    pub(super) fn recheck_database_identity(&self) -> Result<()> {
        if let (Some(path), Some(file)) = (&self.database_path, &self.database_identity_handle) {
            let identity = FileMarker::from_metadata(&file.metadata().map_err(HeleosError::Io)?);
            recheck_file_identity(path, file, &identity)?;
        }
        if let Some(reader_lock) = &self.reader_lock {
            reader_lock.recheck_identity()?;
        }
        for sidecar in &self.source_sidecar_handles {
            sidecar.recheck()?;
        }
        Ok(())
    }

    pub(super) fn recheck_writer_lock_identity(&self) -> Result<()> {
        match (&self.database_path, &self.writer_lock) {
            (Some(_), Some(writer_lock)) => writer_lock.recheck_identity(),
            (None, None) => Ok(()),
            _ => Err(HeleosError::PolicyDenied),
        }
    }
}

fn ensure_one_normal_component(name: &OsStr) -> Result<()> {
    let mut components = Path::new(name).components();
    if !matches!(
        (components.next(), components.next()),
        (Some(Component::Normal(_)), None)
    ) {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

fn ensure_allowed_snapshot_name(name: &OsStr) -> Result<()> {
    ensure_one_normal_component(name)?;
    if ![
        OsStr::new("snapshot.sqlite3"),
        OsStr::new("snapshot.sqlite3-wal"),
        OsStr::new("snapshot.sqlite3-shm"),
    ]
    .contains(&name)
    {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

fn create_private_snapshot_directory(parent: &CapDir, name: &OsStr) -> io::Result<()> {
    let mut builder = CapDirBuilder::new();
    #[cfg(unix)]
    {
        use cap_std::fs::DirBuilderExt;

        builder.mode(0o700);
    }
    parent.create_dir_with(name, &builder)
}

fn snapshot_directory_open_options(policy: PermissionPolicy) -> CapOpenOptions {
    let mut options = CapOpenOptions::new();
    options
        .read(true)
        .follow(FollowSymlinks::No)
        .maybe_dir(true);
    configure_snapshot_directory_options(&mut options, policy);
    options
}

#[cfg(unix)]
fn configure_snapshot_directory_options(options: &mut CapOpenOptions, _: PermissionPolicy) {
    use cap_std::fs::OpenOptionsExt;

    options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_DIRECTORY);
}

#[cfg(windows)]
fn configure_snapshot_directory_options(options: &mut CapOpenOptions, policy: PermissionPolicy) {
    use cap_std::fs::OpenOptionsExt;

    const GENERIC_READ: u32 = 0x8000_0000;
    const READ_CONTROL: u32 = 0x0002_0000;
    const WRITE_DAC: u32 = 0x0004_0000;
    const FILE_READ_ATTRIBUTES: u32 = 0x0000_0080;
    const FILE_SHARE_READ: u32 = 0x0000_0001;
    const FILE_SHARE_WRITE: u32 = 0x0000_0002;
    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;

    let write_dac = if matches!(policy, PermissionPolicy::ApplyAndVerify) {
        WRITE_DAC
    } else {
        0
    };
    options
        .access_mode(GENERIC_READ | READ_CONTROL | write_dac | FILE_READ_ATTRIBUTES)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS);
}

#[cfg(not(any(unix, windows)))]
fn configure_snapshot_directory_options(_: &mut CapOpenOptions, _: PermissionPolicy) {}

fn snapshot_file_create_options() -> CapOpenOptions {
    let mut options = CapOpenOptions::new();
    options
        .read(true)
        .write(true)
        .create_new(true)
        .follow(FollowSymlinks::No)
        .maybe_dir(false);
    configure_snapshot_file_options(&mut options, true, true);
    options
}

fn snapshot_file_binding_options() -> CapOpenOptions {
    let mut options = CapOpenOptions::new();
    options
        .read(true)
        .follow(FollowSymlinks::No)
        .maybe_dir(false);
    configure_snapshot_file_options(&mut options, false, false);
    options
}

#[cfg(unix)]
fn configure_snapshot_file_options(options: &mut CapOpenOptions, create: bool, _: bool) {
    use cap_std::fs::OpenOptionsExt;

    options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    if create {
        options.mode(0o600);
    }
}

#[cfg(windows)]
fn configure_snapshot_file_options(
    options: &mut CapOpenOptions,
    create: bool,
    apply_permissions: bool,
) {
    use cap_std::fs::OpenOptionsExt;

    const GENERIC_READ: u32 = 0x8000_0000;
    const GENERIC_WRITE: u32 = 0x4000_0000;
    const READ_CONTROL: u32 = 0x0002_0000;
    const WRITE_DAC: u32 = 0x0004_0000;
    const FILE_READ_ATTRIBUTES: u32 = 0x0000_0080;
    const FILE_SHARE_READ: u32 = 0x0000_0001;
    const FILE_SHARE_WRITE: u32 = 0x0000_0002;
    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;

    let write = if create { GENERIC_WRITE } else { 0 };
    let write_dac = if apply_permissions { WRITE_DAC } else { 0 };
    options
        .access_mode(GENERIC_READ | write | READ_CONTROL | write_dac | FILE_READ_ATTRIBUTES)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
}

#[cfg(not(any(unix, windows)))]
fn configure_snapshot_file_options(_: &mut CapOpenOptions, _: bool, _: bool) {}

fn snapshot_candidate_parent_error(error: HeleosError) -> io::Error {
    match error {
        HeleosError::Io(error) => error,
        _ => io::Error::other(SnapshotCandidatePolicyError),
    }
}

fn map_snapshot_binding_error(error: io::Error) -> HeleosError {
    if error.kind() == io::ErrorKind::NotFound {
        return HeleosError::PolicyDenied;
    }
    #[cfg(unix)]
    if matches!(error.raw_os_error(), Some(code) if code == libc::ELOOP || code == libc::ENOTDIR) {
        return HeleosError::PolicyDenied;
    }
    HeleosError::Io(error)
}

fn map_snapshot_cap_open_error(error: io::Error) -> HeleosError {
    map_snapshot_binding_error(error)
}

fn open_retained_snapshot_parent(
    path: &Path,
) -> Result<(File, FileMarker, SnapshotParentPolicyMarker)> {
    let before = fs::symlink_metadata(path).map_err(HeleosError::Io)?;
    validate_directory_metadata(&before)?;
    let before_marker = FileMarker::from_metadata(&before);
    let mut options = OpenOptions::new();
    options.read(true);
    configure_retained_directory_sharing(&mut options, PermissionPolicy::VerifyOnly);
    let file = options.open(path).map_err(HeleosError::Io)?;
    let metadata = file.metadata().map_err(HeleosError::Io)?;
    validate_directory_metadata(&metadata)?;
    let marker = FileMarker::from_metadata(&metadata);
    if marker != before_marker {
        return Err(HeleosError::PolicyDenied);
    }
    let policy_marker = capture_snapshot_parent_policy(&file)?;
    recheck_snapshot_parent_retained(&file, &marker, &policy_marker)?;
    recheck_snapshot_parent_ambient(path, &marker, &policy_marker)?;
    Ok((file, marker, policy_marker))
}

fn recheck_snapshot_parent_retained(
    file: &File,
    expected_marker: &FileMarker,
    expected_policy: &SnapshotParentPolicyMarker,
) -> Result<()> {
    let metadata = snapshot_open_operation(SnapshotOpenOperation::ParentRetainedMetadata, || {
        file.metadata().map_err(HeleosError::Io)
    })?;
    snapshot_open_operation(SnapshotOpenOperation::ParentRetainedType, || {
        validate_directory_type_metadata(&metadata)
    })?;
    snapshot_open_operation(SnapshotOpenOperation::ParentRetainedReparse, || {
        reject_reparse_point(&metadata)
    })?;
    snapshot_open_operation(SnapshotOpenOperation::ParentRetainedMarker, || {
        if &FileMarker::from_metadata(&metadata) == expected_marker {
            Ok(())
        } else {
            Err(HeleosError::PolicyDenied)
        }
    })?;
    snapshot_open_operation(SnapshotOpenOperation::ParentRetainedPolicy, || {
        recheck_snapshot_parent_policy(file, expected_policy)
    })
}

fn recheck_snapshot_parent_ambient(
    path: &Path,
    expected_marker: &FileMarker,
    expected_policy: &SnapshotParentPolicyMarker,
) -> Result<()> {
    let metadata = snapshot_open_operation(SnapshotOpenOperation::ParentAmbientMetadata, || {
        fs::symlink_metadata(path).map_err(map_snapshot_binding_error)
    })?;
    snapshot_open_operation(SnapshotOpenOperation::ParentAmbientType, || {
        validate_directory_type_metadata(&metadata)
    })?;
    snapshot_open_operation(SnapshotOpenOperation::ParentAmbientReparse, || {
        reject_reparse_point(&metadata)
    })?;
    snapshot_open_operation(SnapshotOpenOperation::ParentAmbientMarker, || {
        if &FileMarker::from_metadata(&metadata) == expected_marker {
            Ok(())
        } else {
            Err(HeleosError::PolicyDenied)
        }
    })?;

    // The canonical ambient name is evidence only. The retained handle is the policy authority;
    // this second check binds the ambient endpoint to that same handle without mutating it.
    let rebound = snapshot_open_operation(SnapshotOpenOperation::ParentAmbientOpen, || {
        open_retained_snapshot_parent_binding(path)
    })?;
    snapshot_open_operation(SnapshotOpenOperation::ParentAmbientRebound, || {
        recheck_snapshot_parent_retained(&rebound, expected_marker, expected_policy)
    })
}

fn open_retained_snapshot_parent_binding(path: &Path) -> Result<File> {
    let mut options = OpenOptions::new();
    options.read(true);
    configure_retained_directory_sharing(&mut options, PermissionPolicy::VerifyOnly);
    options.open(path).map_err(map_snapshot_binding_error)
}

#[cfg(unix)]
fn configure_retained_directory_sharing(options: &mut OpenOptions, _: PermissionPolicy) {
    use std::os::unix::fs::OpenOptionsExt;

    options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_DIRECTORY);
}

#[cfg(windows)]
fn retained_directory_windows_open_masks(policy: PermissionPolicy) -> (u32, u32, u32) {
    const GENERIC_READ: u32 = 0x8000_0000;
    const READ_CONTROL: u32 = 0x0002_0000;
    const WRITE_DAC: u32 = 0x0004_0000;
    const FILE_READ_ATTRIBUTES: u32 = 0x0000_0080;
    const FILE_SHARE_READ: u32 = 0x0000_0001;
    const FILE_SHARE_WRITE: u32 = 0x0000_0002;
    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;

    let write_dac = if matches!(policy, PermissionPolicy::ApplyAndVerify) {
        WRITE_DAC
    } else {
        0
    };
    (
        GENERIC_READ | READ_CONTROL | write_dac | FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS,
    )
}

#[cfg(windows)]
fn configure_retained_directory_sharing(options: &mut OpenOptions, policy: PermissionPolicy) {
    use std::os::windows::fs::OpenOptionsExt;

    let (access, share, flags) = retained_directory_windows_open_masks(policy);
    options
        .access_mode(access)
        .share_mode(share)
        .custom_flags(flags);
}

#[cfg(not(any(unix, windows)))]
fn configure_retained_directory_sharing(_: &mut OpenOptions, _: PermissionPolicy) {}

fn validate_directory_metadata(metadata: &fs::Metadata) -> Result<()> {
    validate_directory_type_metadata(metadata)?;
    reject_reparse_point(metadata)
}

fn validate_directory_type_metadata(metadata: &fs::Metadata) -> Result<()> {
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

fn recheck_directory_identity(path: &Path, file: &File, expected: &FileMarker) -> Result<()> {
    let handle_metadata = file.metadata().map_err(HeleosError::Io)?;
    validate_directory_metadata(&handle_metadata)?;
    verify_private_permissions_on_handle(file)?;
    let path_metadata = fs::symlink_metadata(path).map_err(map_snapshot_binding_error)?;
    validate_directory_metadata(&path_metadata)?;
    let handle_marker = FileMarker::from_metadata(&handle_metadata);
    let path_marker = FileMarker::from_metadata(&path_metadata);
    if &handle_marker != expected || &path_marker != expected {
        return Err(HeleosError::PolicyDenied);
    }
    verify_private_permissions(path)
}

#[derive(Debug)]
struct CheckedSourceFile {
    file: File,
    path: PathBuf,
    marker: FileMarker,
}

impl CheckedSourceFile {
    fn recheck(&self) -> Result<()> {
        recheck_file_identity(&self.path, &self.file, &self.marker)
    }
}

fn open_optional_verified_file(path: &Path) -> Result<Option<CheckedSourceFile>> {
    match fs::symlink_metadata(path) {
        Ok(_) => open_checked_regular_file(path, false, false, PermissionPolicy::VerifyOnly).map(
            |(file, marker)| {
                Some(CheckedSourceFile {
                    file,
                    path: path.to_owned(),
                    marker,
                })
            },
        ),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(HeleosError::Io(error)),
    }
}

fn recheck_optional_file(path: &Path, opened: Option<&CheckedSourceFile>) -> Result<()> {
    match opened {
        Some(file) => file.recheck(),
        None => match fs::symlink_metadata(path) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Ok(_) => Err(HeleosError::PolicyDenied),
            Err(error) => Err(HeleosError::Io(error)),
        },
    }
}

#[derive(Clone, Copy)]
struct SnapshotSourceLengths {
    database_bytes: u64,
    wal_bytes: u64,
    copy_bytes: u64,
    recovery_allowance: u64,
    maximum_staging_bytes: u64,
}

fn snapshot_source_lengths(
    database: &File,
    wal: Option<&CheckedSourceFile>,
) -> Result<SnapshotSourceLengths> {
    let database_bytes = database.metadata().map_err(HeleosError::Io)?.len();
    let wal_bytes = wal
        .map(|checked| checked.file.metadata().map(|metadata| metadata.len()))
        .transpose()
        .map_err(HeleosError::Io)?
        .unwrap_or(0);
    snapshot_budget_from_lengths(database_bytes, wal_bytes)
}

fn snapshot_budget_from_lengths(
    database_bytes: u64,
    wal_bytes: u64,
) -> Result<SnapshotSourceLengths> {
    let copy_bytes = snapshot_copy_bytes_from_lengths(database_bytes, wal_bytes)?;
    let recovery_allowance = wal_bytes
        .checked_add(SNAPSHOT_RECOVERY_OVERHEAD)
        .ok_or(HeleosError::PolicyDenied)?
        .min(MAX_SNAPSHOT_RECOVERY_ALLOWANCE);
    let maximum_staging_bytes = copy_bytes
        .checked_add(recovery_allowance)
        .ok_or(HeleosError::PolicyDenied)?;
    Ok(SnapshotSourceLengths {
        database_bytes,
        wal_bytes,
        copy_bytes,
        recovery_allowance,
        maximum_staging_bytes,
    })
}

fn recheck_snapshot_source_lengths(
    database: &File,
    wal: Option<&CheckedSourceFile>,
    expected: SnapshotSourceLengths,
) -> Result<()> {
    let actual = snapshot_source_lengths(database, wal)?;
    if actual.database_bytes != expected.database_bytes
        || actual.wal_bytes != expected.wal_bytes
        || actual.copy_bytes != expected.copy_bytes
        || actual.recovery_allowance != expected.recovery_allowance
        || actual.maximum_staging_bytes != expected.maximum_staging_bytes
    {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

fn snapshot_copy_bytes_from_lengths(database_bytes: u64, wal_bytes: u64) -> Result<u64> {
    if database_bytes > MAX_SNAPSHOT_DATABASE_BYTES || wal_bytes > MAX_SNAPSHOT_WAL_BYTES {
        return Err(HeleosError::PolicyDenied);
    }
    let copy_bytes = database_bytes
        .checked_add(wal_bytes)
        .ok_or(HeleosError::PolicyDenied)?;
    if copy_bytes > MAX_SNAPSHOT_COPY_BYTES {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(copy_bytes)
}

fn ensure_snapshot_capacity(staging_path: &Path, lengths: SnapshotSourceLengths) -> Result<()> {
    record_snapshot_capacity_check();
    let total_bytes =
        snapshot_open_operation(SnapshotOpenOperation::PreCapacityTotalQuery, || {
            fs2::total_space(staging_path).map_err(HeleosError::Io)
        })?;
    let available_bytes =
        snapshot_open_operation(SnapshotOpenOperation::PreCapacityAvailableQuery, || {
            fs2::available_space(staging_path).map_err(HeleosError::Io)
        })?;
    snapshot_open_operation(SnapshotOpenOperation::PreCapacityArithmetic, || {
        validate_snapshot_capacity(
            lengths.copy_bytes,
            lengths.recovery_allowance,
            total_bytes,
            available_bytes,
        )
    })
}

fn ensure_snapshot_reserve(staging_path: &Path) -> Result<()> {
    record_snapshot_capacity_check();
    let total_bytes =
        snapshot_open_operation(SnapshotOpenOperation::PostCapacityTotalQuery, || {
            fs2::total_space(staging_path).map_err(HeleosError::Io)
        })?;
    let available_bytes =
        snapshot_open_operation(SnapshotOpenOperation::PostCapacityAvailableQuery, || {
            fs2::available_space(staging_path).map_err(HeleosError::Io)
        })?;
    snapshot_open_operation(SnapshotOpenOperation::PostCapacityReserveDecision, || {
        if available_bytes < required_snapshot_reserve(total_bytes) {
            Err(HeleosError::PolicyDenied)
        } else {
            Ok(())
        }
    })
}

#[cfg(test)]
std::thread_local! {
    static SNAPSHOT_CAPACITY_CHECKS: std::cell::Cell<usize> = const { std::cell::Cell::new(0) };
}

#[cfg(test)]
fn record_snapshot_capacity_check() {
    SNAPSHOT_CAPACITY_CHECKS.with(|checks| checks.set(checks.get() + 1));
}

#[cfg(not(test))]
const fn record_snapshot_capacity_check() {}

#[cfg(test)]
fn reset_snapshot_capacity_checks_for_test() {
    SNAPSHOT_CAPACITY_CHECKS.with(|checks| checks.set(0));
}

#[cfg(test)]
fn snapshot_capacity_checks_for_test() -> usize {
    SNAPSHOT_CAPACITY_CHECKS.with(std::cell::Cell::get)
}

fn validate_snapshot_capacity(
    copy_bytes: u64,
    recovery_allowance: u64,
    total_bytes: u64,
    available_bytes: u64,
) -> Result<()> {
    let reserve = required_snapshot_reserve(total_bytes);
    let required = copy_bytes
        .checked_add(recovery_allowance)
        .ok_or(HeleosError::PolicyDenied)?
        .checked_add(reserve)
        .ok_or(HeleosError::PolicyDenied)?;
    if available_bytes < required {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

fn copy_snapshot_chunks(
    source: &mut File,
    destination: &mut File,
    expected_bytes: u64,
    operations: SnapshotCopyOperations,
) -> Result<()> {
    let mut buffer = vec![0_u8; SNAPSHOT_COPY_CHUNK_BYTES];
    let mut copied = 0_u64;
    loop {
        let count = snapshot_open_operation(operations.source_read, || {
            source.read(&mut buffer).map_err(HeleosError::Io)
        })?;
        if count == 0 {
            break;
        }
        copied = snapshot_open_operation(operations.byte_accounting, || {
            copied
                .checked_add(
                    u64::try_from(count)
                        .map_err(|error| HeleosError::Io(io::Error::other(error)))?,
                )
                .ok_or_else(|| HeleosError::Io(io::Error::other("snapshot byte count overflow")))
        })?;
        if copied > expected_bytes {
            return Err(HeleosError::Io(io::Error::other(
                "snapshot source grew beyond its declared length",
            )));
        }
        snapshot_open_operation(operations.destination_write, || {
            destination
                .write_all(&buffer[..count])
                .map_err(HeleosError::Io)
        })?;
        #[cfg(test)]
        snapshot_copy_test_barrier_after_first_chunk(copied).map_err(HeleosError::Io)?;
    }
    snapshot_open_operation(operations.final_byte_count, || {
        if copied != expected_bytes {
            Err(HeleosError::Io(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "snapshot source length changed during copy",
            )))
        } else {
            Ok(())
        }
    })
}

#[cfg(test)]
fn snapshot_copy_test_barrier_after_first_chunk(copied: u64) -> io::Result<()> {
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::time::Instant;

    static BARRIER_USED: AtomicBool = AtomicBool::new(false);
    let Some(root) = std::env::var_os("HELEOS_TEST_SNAPSHOT_COPY_BARRIER") else {
        return Ok(());
    };
    if copied == 0 || BARRIER_USED.swap(true, Ordering::SeqCst) {
        return Ok(());
    }
    let root = PathBuf::from(root);
    fs::write(root.join("copied"), b"ready")?;
    let deadline = Instant::now() + Duration::from_secs(15);
    while !root.join("continue").is_file() {
        if Instant::now() >= deadline {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "snapshot copy test barrier timed out",
            ));
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    Ok(())
}

fn required_snapshot_reserve(total_bytes: u64) -> u64 {
    let ten_percent = total_bytes / 10 + u64::from(!total_bytes.is_multiple_of(10));
    MINIMUM_FREE_SPACE_RESERVE.max(ten_percent)
}

#[cfg(test)]
fn stream_and_sync_snapshot(
    source: &mut File,
    destination: &mut File,
    copy: impl FnOnce(&mut File, &mut File) -> io::Result<()>,
    flush: impl FnOnce(&mut File) -> io::Result<()>,
    sync: impl FnOnce(&File) -> io::Result<()>,
) -> io::Result<()> {
    copy(source, destination)?;
    flush(destination)?;
    sync(destination)
}

#[derive(Clone, Copy)]
enum ConnectionKind {
    FileWriter,
    FileReader,
    MemoryWriter,
}

fn configure_connection(connection: &Connection, kind: ConnectionKind) -> Result<()> {
    register_jcs_validator(connection)?;
    snapshot_open_operation(SnapshotOpenOperation::BusyTimeout, || {
        connection
            .busy_timeout(BUSY_TIMEOUT)
            .map_err(database_error)
    })?;
    snapshot_open_operation(SnapshotOpenOperation::DefensiveDbConfig, || {
        connection
            .set_db_config(DbConfig::SQLITE_DBCONFIG_DEFENSIVE, true)
            .map(|_| ())
            .map_err(database_error)
    })?;
    snapshot_open_operation(SnapshotOpenOperation::TrustedSchemaDbConfig, || {
        connection
            .set_db_config(DbConfig::SQLITE_DBCONFIG_TRUSTED_SCHEMA, false)
            .map(|_| ())
            .map_err(database_error)
    })?;
    snapshot_open_operation(SnapshotOpenOperation::DqsDdlDbConfig, || {
        connection
            .set_db_config(DbConfig::SQLITE_DBCONFIG_DQS_DDL, false)
            .map(|_| ())
            .map_err(database_error)
    })?;
    snapshot_open_operation(SnapshotOpenOperation::DqsDmlDbConfig, || {
        connection
            .set_db_config(DbConfig::SQLITE_DBCONFIG_DQS_DML, false)
            .map(|_| ())
            .map_err(database_error)
    })?;
    snapshot_open_operation(SnapshotOpenOperation::AttachCreateDbConfig, || {
        connection
            .set_db_config(DbConfig::SQLITE_DBCONFIG_ENABLE_ATTACH_CREATE, false)
            .map(|_| ())
            .map_err(database_error)
    })?;
    snapshot_open_operation(SnapshotOpenOperation::AttachWriteDbConfig, || {
        connection
            .set_db_config(DbConfig::SQLITE_DBCONFIG_ENABLE_ATTACH_WRITE, false)
            .map(|_| ())
            .map_err(database_error)
    })?;
    snapshot_open_operation(SnapshotOpenOperation::ForeignKeysPragma, || {
        connection
            .pragma_update(None, "foreign_keys", "ON")
            .map_err(database_error)
    })?;
    snapshot_open_operation(SnapshotOpenOperation::TrustedSchemaPragma, || {
        connection
            .pragma_update(None, "trusted_schema", "OFF")
            .map_err(database_error)
    })?;
    snapshot_open_operation(SnapshotOpenOperation::SynchronousPragma, || {
        connection
            .pragma_update(None, "synchronous", "FULL")
            .map_err(database_error)
    })?;
    snapshot_open_operation(SnapshotOpenOperation::TempStorePragma, || {
        connection
            .pragma_update(None, "temp_store", "MEMORY")
            .map_err(database_error)
    })?;

    match kind {
        ConnectionKind::FileWriter => {
            let mode =
                snapshot_open_operation(SnapshotOpenOperation::JournalModeRecoveryQuery, || {
                    connection
                        .pragma_query_value(Some("main"), "journal_mode", |row| {
                            row.get::<_, String>(0)
                        })
                        .map_err(database_error)
                })?;
            if !mode.eq_ignore_ascii_case("wal") {
                connection
                    .pragma_update(None, "journal_mode", "WAL")
                    .map_err(database_error)?;
            }
            let mode =
                snapshot_open_operation(SnapshotOpenOperation::JournalModeRecoveryQuery, || {
                    connection
                        .pragma_query_value(Some("main"), "journal_mode", |row| {
                            row.get::<_, String>(0)
                        })
                        .map_err(database_error)
                })?;
            if !mode.eq_ignore_ascii_case("wal") {
                return Err(HeleosError::Database);
            }
        }
        ConnectionKind::FileReader => {
            let mode =
                snapshot_open_operation(SnapshotOpenOperation::JournalModeRecoveryQuery, || {
                    connection
                        .pragma_query_value(Some("main"), "journal_mode", |row| {
                            row.get::<_, String>(0)
                        })
                        .map_err(database_error)
                })?;
            if !mode.eq_ignore_ascii_case("wal") {
                return Err(HeleosError::Database);
            }
            snapshot_open_operation(SnapshotOpenOperation::QueryOnlyPragma, || {
                connection
                    .pragma_update(None, "query_only", "ON")
                    .map_err(database_error)
            })?;
        }
        ConnectionKind::MemoryWriter => {}
    }
    Ok(())
}

fn register_jcs_validator(connection: &Connection) -> Result<()> {
    let flags = FunctionFlags::SQLITE_UTF8
        | FunctionFlags::SQLITE_DETERMINISTIC
        | FunctionFlags::SQLITE_INNOCUOUS;
    snapshot_open_operation(SnapshotOpenOperation::RegisterJcsScalar, || {
        connection
            .create_scalar_function("heleos_is_jcs", 1, flags, |context| {
                let text = context.get::<String>(0)?;
                let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) else {
                    return Ok(false);
                };
                let Ok(canonical) = serde_jcs::to_string(&value) else {
                    return Ok(false);
                };
                Ok(canonical == text)
            })
            .map_err(database_error)
    })?;
    snapshot_open_operation(SnapshotOpenOperation::RegisterUuidScalar, || {
        connection
            .create_scalar_function("heleos_is_uuid", 1, flags, |context| {
                let text = context.get::<String>(0)?;
                let valid = uuid::Uuid::parse_str(&text)
                    .is_ok_and(|value| value.hyphenated().to_string() == text);
                Ok(valid)
            })
            .map_err(database_error)
    })?;
    snapshot_open_operation(SnapshotOpenOperation::RegisterValidTextScalar, || {
        connection
            .create_scalar_function("heleos_valid_text", 3, flags, |context| {
                let text = context.get::<String>(0)?;
                let max_bytes = context.get::<i64>(1)?;
                let allow_ordinary_whitespace = context.get::<i64>(2)? != 0;
                let valid_control = |character: char| {
                    allow_ordinary_whitespace && matches!(character, '\n' | '\r' | '\t')
                };
                let valid = max_bytes >= 0
                    && !text.is_empty()
                    && u64::try_from(text.len()).is_ok_and(|length| length <= max_bytes as u64)
                    && !text
                        .chars()
                        .any(|character| character.is_control() && !valid_control(character));
                Ok(valid)
            })
            .map_err(database_error)
    })
}

fn collect_single_column_check(
    connection: &Connection,
    sql: &'static str,
) -> Result<(Vec<String>, bool)> {
    let mut statement = connection.prepare(sql).map_err(database_error)?;
    let rows = statement
        .query_map([], |row| row.get::<_, String>(0))
        .map_err(database_error)?;
    bound_integrity_violations(rows)
}

fn bound_integrity_violations(
    rows: impl IntoIterator<Item = rusqlite::Result<String>>,
) -> Result<(Vec<String>, bool)> {
    let mut violations = Vec::new();
    for row in rows {
        let result = row.map_err(database_error)?;
        if result != "ok" {
            violations.push(result);
        }
    }
    let truncated = violations.len() > INTEGRITY_VIOLATION_LIMIT;
    violations.truncate(INTEGRITY_VIOLATION_LIMIT);
    Ok((violations, truncated))
}

#[derive(Clone, Copy)]
enum PermissionPolicy {
    ApplyAndVerify,
    VerifyOnly,
}

fn check_parent(path: &Path, policy: PermissionPolicy) -> Result<()> {
    let parent = path
        .parent()
        .filter(|value| !value.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let metadata = fs::symlink_metadata(parent).map_err(HeleosError::Io)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(HeleosError::PolicyDenied);
    }
    reject_reparse_point(&metadata)?;
    enforce_permission_policy(parent, policy)
}

fn check_sqlite_sidecars(database_path: &Path, policy: PermissionPolicy) -> Result<()> {
    for suffix in ["-wal", "-shm"] {
        let path = sidecar_path(database_path, suffix);
        match fs::symlink_metadata(&path) {
            Ok(metadata) => {
                validate_regular_metadata(&metadata)?;
                enforce_permission_policy(&path, policy)?;
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(HeleosError::Io(error)),
        }
    }
    Ok(())
}

fn enforce_permission_policy(path: &Path, policy: PermissionPolicy) -> Result<()> {
    match policy {
        PermissionPolicy::ApplyAndVerify => apply_private_permissions(path),
        PermissionPolicy::VerifyOnly => verify_private_permissions(path),
    }
}

fn enforce_permission_policy_on_handle(file: &mut File, policy: PermissionPolicy) -> Result<()> {
    match policy {
        PermissionPolicy::ApplyAndVerify => permissions::apply_private_permissions_to_handle(file),
        PermissionPolicy::VerifyOnly => permissions::verify_private_permissions_on_handle(file),
    }
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
    permission_policy: PermissionPolicy,
) -> Result<(File, FileMarker)> {
    let (mut file, identity) = open_checked_regular_file_without_permissions(
        path,
        create_if_missing,
        writable,
        permission_policy,
    )?;
    enforce_permission_policy_on_handle(&mut file, permission_policy)?;
    recheck_file_identity(path, &file, &identity)?;
    Ok((file, identity))
}

fn open_checked_regular_file_without_permissions(
    path: &Path,
    create_if_missing: bool,
    writable: bool,
    permission_policy: PermissionPolicy,
) -> Result<(File, FileMarker)> {
    for _ in 0..8 {
        let before = match fs::symlink_metadata(path) {
            Ok(metadata) => {
                validate_regular_metadata(&metadata)?;
                Some(FileMarker::from_metadata(&metadata))
            }
            Err(error) if error.kind() == io::ErrorKind::NotFound => None,
            Err(error) => return Err(HeleosError::Io(error)),
        };
        if before.is_none() && !create_if_missing {
            return Err(HeleosError::NotFound);
        }

        let mut options = OpenOptions::new();
        options.read(true).write(writable);
        configure_retained_file_sharing(&mut options, writable, permission_policy);
        if before.is_none() {
            #[cfg(test)]
            first_create_test_barrier(path)?;
            options.create_new(true);
        }
        let file = match options.open(path) {
            Ok(file) => file,
            Err(error)
                if create_if_missing
                    && matches!(
                        error.kind(),
                        io::ErrorKind::AlreadyExists | io::ErrorKind::NotFound
                    ) =>
            {
                continue;
            }
            Err(error) => return Err(HeleosError::Io(error)),
        };
        let metadata = file.metadata().map_err(HeleosError::Io)?;
        validate_regular_metadata(&metadata)?;
        let identity = FileMarker::from_metadata(&metadata);
        if let Some(before) = before
            && before != identity
        {
            return Err(HeleosError::PolicyDenied);
        }
        recheck_file_identity(path, &file, &identity)?;
        return Ok((file, identity));
    }
    Err(HeleosError::PolicyDenied)
}

#[cfg(test)]
fn first_create_test_barrier(path: &Path) -> Result<()> {
    use std::time::Instant;

    let Some(expected_path) = std::env::var_os("HELEOS_TEST_FIRST_CREATE_DATABASE") else {
        return Ok(());
    };
    let expected_lock = sidecar_path(Path::new(&expected_path), ".writer.lock");
    if path != expected_lock {
        return Ok(());
    }
    let barrier = PathBuf::from(
        std::env::var_os("HELEOS_TEST_FIRST_CREATE_BARRIER").ok_or(HeleosError::PolicyDenied)?,
    );
    let role =
        std::env::var_os("HELEOS_TEST_FIRST_CREATE_ROLE").ok_or(HeleosError::PolicyDenied)?;
    fs::write(barrier.join(role), b"ready").map_err(HeleosError::Io)?;
    let deadline = Instant::now() + Duration::from_secs(15);
    while !barrier.join("release").is_file() {
        if Instant::now() >= deadline {
            return Err(HeleosError::Io(io::Error::new(
                io::ErrorKind::TimedOut,
                "first-create test barrier timed out",
            )));
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    Ok(())
}

#[cfg(windows)]
fn configure_retained_file_sharing(
    options: &mut OpenOptions,
    writable: bool,
    permission_policy: PermissionPolicy,
) {
    use std::os::windows::fs::OpenOptionsExt;
    use windows_permissions::constants::AccessRights;

    const FILE_SHARE_READ: u32 = 0x0000_0001;
    const FILE_SHARE_WRITE: u32 = 0x0000_0002;
    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;

    let mut rights = AccessRights::GenericRead | AccessRights::ReadControl;
    if writable {
        rights |= AccessRights::GenericWrite;
    }
    if matches!(permission_policy, PermissionPolicy::ApplyAndVerify) {
        rights |= AccessRights::WriteDac;
    }
    options
        .access_mode(rights.bits())
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
}

#[cfg(unix)]
fn configure_retained_file_sharing(options: &mut OpenOptions, _: bool, _: PermissionPolicy) {
    use std::os::unix::fs::OpenOptionsExt;

    options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
}

#[cfg(not(any(unix, windows)))]
const fn configure_retained_file_sharing(_: &mut OpenOptions, _: bool, _: PermissionPolicy) {}

fn recheck_file_identity(path: &Path, file: &File, expected: &FileMarker) -> Result<()> {
    let handle_metadata = file.metadata().map_err(HeleosError::Io)?;
    validate_regular_metadata(&handle_metadata)?;
    let path_metadata = fs::symlink_metadata(path).map_err(HeleosError::Io)?;
    validate_regular_metadata(&path_metadata)?;
    let handle_identity = FileMarker::from_metadata(&handle_metadata);
    let path_identity = FileMarker::from_metadata(&path_metadata);
    if &handle_identity != expected || &path_identity != expected {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

fn recheck_snapshot_file_ambient(path: &Path, file: &File, expected: &FileMarker) -> Result<()> {
    let handle_metadata = file.metadata().map_err(HeleosError::Io)?;
    validate_regular_metadata(&handle_metadata)?;
    let path_metadata = fs::symlink_metadata(path).map_err(map_snapshot_binding_error)?;
    validate_regular_metadata(&path_metadata)?;
    if &FileMarker::from_metadata(&handle_metadata) != expected
        || &FileMarker::from_metadata(&path_metadata) != expected
    {
        return Err(HeleosError::PolicyDenied);
    }
    verify_private_permissions_on_handle(file)?;
    verify_private_permissions(path)
}

fn validate_regular_metadata(metadata: &fs::Metadata) -> Result<()> {
    validate_regular_type_metadata(metadata)?;
    reject_reparse_point(metadata)
}

fn validate_regular_type_metadata(metadata: &fs::Metadata) -> Result<()> {
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
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
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct FileMarker {
    device: u64,
    inode: u64,
}

#[cfg(unix)]
impl FileMarker {
    fn from_metadata(metadata: &fs::Metadata) -> Self {
        use std::os::unix::fs::MetadataExt;

        Self {
            device: metadata.dev(),
            inode: metadata.ino(),
        }
    }
}

#[cfg(windows)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
// This detects some unexpected changes; no-delete sharing, not these fields, prevents replacement.
struct FileMarker {
    attributes: u32,
    creation_time: u64,
}

#[cfg(windows)]
impl FileMarker {
    fn from_metadata(metadata: &fs::Metadata) -> Self {
        use std::os::windows::fs::MetadataExt;

        Self {
            attributes: metadata.file_attributes(),
            creation_time: metadata.creation_time(),
        }
    }
}

#[cfg(not(any(unix, windows)))]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct FileMarker {
    length: u64,
}

#[cfg(not(any(unix, windows)))]
impl FileMarker {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crate_owned_connections_enforce_foundation_settings_and_raw_boundary() {
        let root = tempfile::Builder::new()
            .prefix("heleos-connection-settings-")
            .tempdir()
            .expect("create settings fixture directory");
        apply_private_permissions(root.path()).expect("harden settings fixture directory");
        let canonical_root = fs::canonicalize(root.path()).expect("canonical settings fixture");
        let database = canonical_root.join("foundation.sqlite3");
        let mut store = Store::open_writer(&database).expect("open settings writer");

        let settings = (
            store
                .connection
                .pragma_query_value(None, "foreign_keys", |row| row.get::<_, i64>(0))
                .expect("query foreign keys"),
            store
                .connection
                .pragma_query_value(None, "journal_mode", |row| row.get::<_, String>(0))
                .expect("query journal mode"),
            store
                .connection
                .pragma_query_value(None, "synchronous", |row| row.get::<_, i64>(0))
                .expect("query synchronous"),
            store
                .connection
                .pragma_query_value(None, "busy_timeout", |row| row.get::<_, i64>(0))
                .expect("query busy timeout"),
            store
                .connection
                .pragma_query_value(None, "query_only", |row| row.get::<_, i64>(0))
                .expect("query writer query-only"),
            store
                .connection
                .pragma_query_value(None, "temp_store", |row| row.get::<_, i64>(0))
                .expect("query temp store"),
            store
                .connection
                .db_config(DbConfig::SQLITE_DBCONFIG_TRUSTED_SCHEMA)
                .expect("query trusted schema"),
            store
                .connection
                .db_config(DbConfig::SQLITE_DBCONFIG_DEFENSIVE)
                .expect("query defensive mode"),
            store
                .connection
                .db_config(DbConfig::SQLITE_DBCONFIG_DQS_DDL)
                .expect("query DQS DDL"),
            store
                .connection
                .db_config(DbConfig::SQLITE_DBCONFIG_DQS_DML)
                .expect("query DQS DML"),
            store
                .connection
                .db_config(DbConfig::SQLITE_DBCONFIG_ENABLE_ATTACH_CREATE)
                .expect("query attach-create"),
            store
                .connection
                .db_config(DbConfig::SQLITE_DBCONFIG_ENABLE_ATTACH_WRITE)
                .expect("query attach-write"),
        );
        assert_eq!(
            settings,
            (
                1,
                "wal".to_owned(),
                2,
                5_000,
                0,
                2,
                false,
                true,
                false,
                false,
                false,
                false
            )
        );

        store.read_only = true;
        assert!(matches!(
            store.with_immediate_transaction(|_| Ok(())),
            Err(HeleosError::PolicyDenied)
        ));

        let memory = Store::open_in_memory().expect("open in-memory settings store");
        assert!(
            memory
                .connection
                .db_config(DbConfig::SQLITE_DBCONFIG_DEFENSIVE)
                .expect("query in-memory defensive mode")
        );
    }

    #[test]
    fn simultaneous_first_use_process_helper() {
        let Some(database) = std::env::var_os("HELEOS_TEST_FIRST_CREATE_DATABASE") else {
            return;
        };
        let barrier = PathBuf::from(
            std::env::var_os("HELEOS_TEST_FIRST_CREATE_BARRIER")
                .expect("first-create barrier directory is configured"),
        );
        let mut outcome_name = PathBuf::from(
            std::env::var_os("HELEOS_TEST_FIRST_CREATE_ROLE")
                .expect("first-create role is configured"),
        );
        outcome_name.set_extension("outcome");
        let outcome_path = barrier.join(outcome_name);
        match WriterLock::acquire(Path::new(&database)) {
            Ok(lock) => {
                fs::write(outcome_path, b"winner").expect("record first-create winner");
                let mut release = [0_u8; 1];
                let _ = io::stdin().read(&mut release);
                drop(lock);
            }
            Err(HeleosError::WriterBusy) => {
                fs::write(outcome_path, b"busy").expect("record first-create loser");
            }
            Err(error) => panic!("first-create caller returned unexpected error: {error:?}"),
        }
    }

    #[test]
    fn simultaneous_first_use_restarts_after_create_race() {
        use std::process::{Command, Stdio};
        use std::time::Instant;

        fn wait_for_file(path: &Path) {
            let deadline = Instant::now() + Duration::from_secs(15);
            while !path.is_file() {
                assert!(Instant::now() < deadline, "timed out waiting for {path:?}");
                std::thread::sleep(Duration::from_millis(10));
            }
        }

        let root = tempfile::Builder::new()
            .prefix("heleos-first-create-source-")
            .tempdir()
            .expect("create first-create source directory");
        apply_private_permissions(root.path()).expect("harden first-create source directory");
        let database = root.path().join("foundation.sqlite3");
        let barrier = tempfile::Builder::new()
            .prefix("heleos-first-create-barrier-")
            .tempdir()
            .expect("create first-create barrier directory");

        let mut children = ["caller-a", "caller-b"].map(|role| {
            Command::new(std::env::current_exe().expect("locate unit test executable"))
                .arg("--exact")
                .arg("store::tests::simultaneous_first_use_process_helper")
                .arg("--nocapture")
                .env("HELEOS_TEST_FIRST_CREATE_DATABASE", &database)
                .env("HELEOS_TEST_FIRST_CREATE_BARRIER", barrier.path())
                .env("HELEOS_TEST_FIRST_CREATE_ROLE", role)
                .stdin(Stdio::piped())
                .stdout(Stdio::null())
                .spawn()
                .expect("spawn first-create caller")
        });
        wait_for_file(&barrier.path().join("caller-a"));
        wait_for_file(&barrier.path().join("caller-b"));
        fs::write(barrier.path().join("release"), b"release").expect("release first-create race");

        let outcome_paths =
            ["caller-a.outcome", "caller-b.outcome"].map(|name| barrier.path().join(name));
        for path in &outcome_paths {
            wait_for_file(path);
        }
        let outcomes = outcome_paths.map(|path| fs::read_to_string(path).expect("read outcome"));
        assert_eq!(
            outcomes
                .iter()
                .filter(|outcome| outcome.as_str() == "winner")
                .count(),
            1
        );
        assert_eq!(
            outcomes
                .iter()
                .filter(|outcome| outcome.as_str() == "busy")
                .count(),
            1
        );

        for child in &mut children {
            drop(child.stdin.take());
        }
        for child in &mut children {
            assert!(
                child
                    .wait()
                    .expect("wait for first-create caller")
                    .success()
            );
        }
    }

    #[cfg(unix)]
    #[test]
    fn retained_open_options_reject_a_final_symlink_at_the_os_boundary() {
        use std::os::unix::fs::symlink;

        let root = tempfile::Builder::new()
            .prefix("heleos-retained-nofollow-")
            .tempdir()
            .expect("create no-follow fixture directory");
        apply_private_permissions(root.path()).expect("harden no-follow fixture directory");
        let target = root.path().join("target");
        let link = root.path().join("link");
        fs::write(&target, b"target").expect("write no-follow target");
        symlink(&target, &link).expect("create final symlink");

        let mut options = OpenOptions::new();
        options.read(true);
        configure_retained_file_sharing(&mut options, false, PermissionPolicy::VerifyOnly);
        assert!(
            options.open(&link).is_err(),
            "retained open followed a final symlink"
        );
    }

    #[cfg(unix)]
    #[test]
    fn permission_mutation_targets_the_retained_handle_after_path_replacement() {
        use std::os::unix::fs::PermissionsExt;

        let root = tempfile::Builder::new()
            .prefix("heleos-retained-permission-")
            .tempdir()
            .expect("create retained-permission fixture directory");
        apply_private_permissions(root.path()).expect("harden retained-permission directory");
        let path = root.path().join("target");
        let retained_path = root.path().join("retained");
        fs::write(&path, b"retained").expect("write retained-permission target");
        fs::set_permissions(&path, fs::Permissions::from_mode(0o640))
            .expect("broaden original permissions");
        let mut retained = File::open(&path).expect("open retained-permission handle");
        fs::rename(&path, &retained_path).expect("move retained object away from pathname");
        fs::write(&path, b"replacement").expect("replace retained pathname");
        fs::set_permissions(&path, fs::Permissions::from_mode(0o640))
            .expect("broaden replacement permissions");

        permissions::apply_private_permissions_to_handle(&mut retained)
            .expect("harden retained object by handle");

        assert_eq!(
            fs::metadata(retained_path)
                .expect("retained object metadata")
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        assert_eq!(
            fs::metadata(path)
                .expect("replacement metadata")
                .permissions()
                .mode()
                & 0o777,
            0o640
        );
    }

    #[cfg(windows)]
    #[test]
    fn retained_database_and_lock_handles_deny_delete_and_rename_until_store_drop() {
        let root = tempfile::Builder::new()
            .prefix("heleos-windows-no-delete-share-")
            .tempdir()
            .expect("create Windows no-delete fixture directory");
        apply_private_permissions(root.path()).expect("harden Windows no-delete fixture directory");
        let database = root.path().join("foundation.sqlite3");
        let lock = sidecar_path(&database, ".writer.lock");
        let mut store = Store::open_writer(&database).expect("open Windows no-delete writer");
        store.migrate().expect("migrate Windows no-delete fixture");

        let database_moved = root.path().join("database-moved");
        let lock_moved = root.path().join("lock-moved");
        assert!(fs::rename(&database, &database_moved).is_err());
        assert!(fs::remove_file(&database).is_err());
        assert!(fs::rename(&lock, &lock_moved).is_err());
        assert!(fs::remove_file(&lock).is_err());

        drop(store);
        fs::rename(&database, &database_moved).expect("rename database after retained handle drop");
        fs::write(&database, b"replacement").expect("replace database after retained handle drop");
        fs::remove_file(&database).expect("delete replacement database after handle drop");
        fs::rename(&database_moved, &database).expect("restore database after replacement proof");
        fs::remove_file(&database).expect("delete database after retained handle drop");

        fs::rename(&lock, &lock_moved).expect("rename lock after retained handle drop");
        fs::write(&lock, b"replacement").expect("replace lock after retained handle drop");
        fs::remove_file(&lock).expect("delete replacement lock after handle drop");
        fs::rename(&lock_moved, &lock).expect("restore lock after replacement proof");
        fs::remove_file(&lock).expect("delete lock after retained handle drop");
    }

    #[cfg(windows)]
    #[test]
    fn retained_opens_reject_file_reparse_points_and_nonregular_paths() {
        use std::os::windows::fs::symlink_file;

        let root = tempfile::Builder::new()
            .prefix("heleos-windows-reparse-")
            .tempdir()
            .expect("create Windows reparse fixture directory");
        apply_private_permissions(root.path()).expect("harden Windows reparse fixture directory");
        let target = root.path().join("target");
        fs::write(&target, b"target").expect("write Windows reparse target");
        apply_private_permissions(&target).expect("harden Windows reparse target");
        let reparse = root.path().join("reparse");
        symlink_file(&target, &reparse).expect("create Windows file reparse point");

        assert!(matches!(
            open_checked_regular_file(&reparse, false, false, PermissionPolicy::VerifyOnly),
            Err(HeleosError::PolicyDenied)
        ));

        let directory = root.path().join("not-regular");
        fs::create_dir(&directory).expect("create Windows nonregular fixture");
        apply_private_permissions(&directory).expect("harden Windows nonregular fixture");
        assert!(matches!(
            open_checked_regular_file(&directory, false, false, PermissionPolicy::VerifyOnly),
            Err(HeleosError::PolicyDenied)
        ));

        let database = root.path().join("foundation.sqlite3");
        let lock = sidecar_path(&database, ".writer.lock");
        symlink_file(&target, &lock).expect("create Windows lock-file reparse point");
        assert!(matches!(
            WriterLock::acquire(&database),
            Err(HeleosError::PolicyDenied)
        ));
    }

    #[test]
    fn file_reader_enables_sqlite_query_only() {
        let root =
            std::env::temp_dir().join(format!("heleos-reader-settings-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&root).expect("create reader settings fixture directory");
        let root = fs::canonicalize(root).expect("canonicalize reader settings fixture");
        let database = root.join("foundation.sqlite3");

        let mut writer = Store::open_writer(&database).expect("open fixture writer");
        writer.migrate().expect("migrate fixture database");
        drop(writer);

        let reader = Store::open_read_only(&database).expect("open fixture reader");
        let query_only = reader
            .connection
            .pragma_query_value(None, "query_only", |row| row.get::<_, i64>(0))
            .expect("query reader query_only");
        assert_eq!(query_only, 1);
        assert!(
            reader
                .connection
                .execute("CREATE TABLE reader_must_not_write (id INTEGER)", [])
                .is_err()
        );
        drop(reader);

        fs::remove_dir_all(root).expect("remove reader settings fixture");
    }

    #[test]
    fn snapshot_staging_directory_is_removed_when_reader_drops() {
        let root =
            std::env::temp_dir().join(format!("heleos-reader-cleanup-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&root).expect("create reader cleanup fixture directory");
        let root = fs::canonicalize(root).expect("canonicalize reader cleanup fixture");
        let database = root.join("foundation.sqlite3");

        let mut writer = Store::open_writer(&database).expect("open cleanup fixture writer");
        writer.migrate().expect("migrate cleanup fixture database");
        drop(writer);

        let reader = Store::open_read_only(&database).expect("open cleanup fixture reader");
        let staging = reader
            ._snapshot_directory
            .as_ref()
            .expect("reader retains staging directory")
            .path()
            .to_owned();
        assert!(staging.is_dir());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;

            assert_eq!(
                fs::symlink_metadata(&staging)
                    .expect("staging directory metadata")
                    .permissions()
                    .mode()
                    & 0o777,
                0o700
            );
        }
        drop(reader);
        assert!(!staging.exists());
        drop(Store::open_writer(&database).expect("writer resumes after snapshot cleanup"));

        fs::remove_dir_all(root).expect("remove reader cleanup fixture");
    }

    #[test]
    fn integrity_collector_keeps_exact_limit_without_truncation() {
        let rows = (0..INTEGRITY_VIOLATION_LIMIT)
            .map(|index| Ok::<_, rusqlite::Error>(format!("violation-{index}")));
        let (violations, truncated) =
            bound_integrity_violations(rows).expect("bound exact-limit results");

        assert_eq!(violations.len(), INTEGRITY_VIOLATION_LIMIT);
        assert!(!truncated);
    }

    #[test]
    fn integrity_collector_uses_extra_row_as_explicit_truncation_sentinel() {
        let rows = (0..=INTEGRITY_VIOLATION_LIMIT)
            .map(|index| Ok::<_, rusqlite::Error>(format!("violation-{index}")));
        let (violations, truncated) =
            bound_integrity_violations(rows).expect("bound sentinel results");

        assert_eq!(violations.len(), INTEGRITY_VIOLATION_LIMIT);
        assert!(truncated);
        let report = IntegrityReport {
            integrity_check_violations: violations,
            integrity_check_truncated: true,
            quick_check_violations: Vec::new(),
            quick_check_truncated: false,
            foreign_key_violations: Vec::new(),
            foreign_key_check_truncated: false,
        };
        assert!(!report.is_clean());
    }

    #[test]
    fn snapshot_size_caps_reject_each_oversize_boundary() {
        let exact =
            snapshot_budget_from_lengths(MAX_SNAPSHOT_DATABASE_BYTES, MAX_SNAPSHOT_WAL_BYTES)
                .expect("accept exact snapshot caps");
        assert_eq!(exact.copy_bytes, MAX_SNAPSHOT_COPY_BYTES);
        assert_eq!(exact.recovery_allowance, MAX_SNAPSHOT_RECOVERY_ALLOWANCE);
        assert_eq!(
            exact.maximum_staging_bytes,
            MAX_SNAPSHOT_COPY_BYTES + MAX_SNAPSHOT_RECOVERY_ALLOWANCE
        );
        assert_eq!(
            snapshot_budget_from_lengths(1, 0)
                .expect("budget without WAL")
                .recovery_allowance,
            SNAPSHOT_RECOVERY_OVERHEAD
        );
        assert!(matches!(
            snapshot_copy_bytes_from_lengths(MAX_SNAPSHOT_DATABASE_BYTES + 1, 0),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(matches!(
            snapshot_copy_bytes_from_lengths(0, MAX_SNAPSHOT_WAL_BYTES + 1),
            Err(HeleosError::PolicyDenied)
        ));
    }

    #[test]
    fn insufficient_snapshot_capacity_fails_and_private_staging_cleans_up() {
        let staging = tempfile::Builder::new()
            .prefix("heleos-capacity-failure-")
            .tempdir()
            .expect("create capacity failure staging");
        apply_private_permissions(staging.path()).expect("harden capacity failure staging");
        let staging_path = staging.path().to_owned();

        assert!(matches!(
            validate_snapshot_capacity(
                1,
                SNAPSHOT_RECOVERY_OVERHEAD,
                100 * GIBIBYTE,
                MINIMUM_FREE_SPACE_RESERVE
            ),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(matches!(
            validate_snapshot_capacity(
                1,
                SNAPSHOT_RECOVERY_OVERHEAD,
                300 * GIBIBYTE,
                31 * GIBIBYTE
            ),
            Err(HeleosError::PolicyDenied)
        ));
        drop(staging);
        assert!(!staging_path.exists());
    }

    #[test]
    fn exact_snapshot_capacity_includes_recovery_allowance_and_rounded_reserve() {
        let copy_bytes = 7;
        let allowance = SNAPSHOT_RECOVERY_OVERHEAD;
        let total_bytes = 300 * GIBIBYTE + 1;
        let reserve = required_snapshot_reserve(total_bytes);
        assert_eq!(reserve, 30 * GIBIBYTE + 1);
        let required = copy_bytes + allowance + reserve;

        validate_snapshot_capacity(copy_bytes, allowance, total_bytes, required)
            .expect("accept exact required capacity");
        assert!(matches!(
            validate_snapshot_capacity(copy_bytes, allowance, total_bytes, required - 1),
            Err(HeleosError::PolicyDenied)
        ));
    }

    #[test]
    fn recovered_snapshot_rejects_unexpected_entries_and_growth() {
        let staging_parent = tempfile::Builder::new()
            .prefix("heleos-post-recovery-validation-")
            .tempdir()
            .expect("create post-recovery parent");
        apply_private_permissions(staging_parent.path()).expect("harden post-recovery parent");
        let parent_path =
            fs::canonicalize(staging_parent.path()).expect("canonicalize post-recovery parent");
        let parent = ReadOnlySnapshotParent::retain_path_for_test(&parent_path)
            .expect("retain post-recovery parent");
        let source_path = parent_path.join("source.sqlite3");
        fs::write(&source_path, b"a").expect("write source database");
        apply_private_permissions(&source_path).expect("harden source database");
        let source = File::open(&source_path).expect("open source database");
        let mut staging = ReadSnapshotDirectory::create(&parent)
            .expect("create capability-owned post-recovery staging");
        staging
            .copy_database(&source, 1)
            .expect("copy post-recovery database");
        let lengths = SnapshotSourceLengths {
            database_bytes: 1,
            wal_bytes: 0,
            copy_bytes: 1,
            recovery_allowance: 0,
            maximum_staging_bytes: 1,
        };
        staging
            .validate_recovered(lengths, SnapshotCapacityMode::CallerAdmitted)
            .expect("accept exact staging size");

        let unexpected = staging.path().join("unexpected");
        fs::write(&unexpected, b"x").expect("write unexpected staging entry");
        assert!(matches!(
            staging.validate_recovered(lengths, SnapshotCapacityMode::CallerAdmitted),
            Err(HeleosError::PolicyDenied)
        ));
        fs::remove_file(unexpected).expect("remove unexpected staging entry");

        OpenOptions::new()
            .write(true)
            .open(staging.database_path())
            .expect("open staging database for growth")
            .set_len(2)
            .expect("grow staging database");
        assert!(matches!(
            staging.validate_recovered(lengths, SnapshotCapacityMode::CallerAdmitted),
            Err(HeleosError::PolicyDenied)
        ));
        let staging_path = staging.path().to_owned();
        staging
            .close()
            .expect("checked-close post-recovery staging");
        assert!(!staging_path.exists());
    }

    #[test]
    fn injected_copy_and_sync_failures_clean_private_staging() {
        use std::io::Write;

        for (fail_copy, fail_flush, fail_sync) in [
            (true, false, false),
            (false, true, false),
            (false, false, true),
        ] {
            let staging = tempfile::Builder::new()
                .prefix("heleos-snapshot-io-failure-")
                .tempdir()
                .expect("create I/O failure staging");
            apply_private_permissions(staging.path()).expect("harden I/O failure staging");
            let staging_path = staging.path().to_owned();
            let source_path = staging.path().join("source");
            let destination_path = staging.path().join("destination");
            fs::write(&source_path, b"snapshot bytes").expect("write I/O failure source");
            let mut source = File::open(&source_path).expect("open I/O failure source");
            let mut destination =
                File::create(&destination_path).expect("create I/O failure destination");

            let result = stream_and_sync_snapshot(
                &mut source,
                &mut destination,
                |source, destination| {
                    if fail_copy {
                        destination.write_all(b"partial")?;
                        return Err(io::Error::other("injected copy failure"));
                    }
                    io::copy(source, destination).map(|_| ())
                },
                |destination| {
                    if fail_flush {
                        return Err(io::Error::other("injected flush failure"));
                    }
                    destination.flush()
                },
                |destination| {
                    if fail_sync {
                        return Err(io::Error::other("injected sync failure"));
                    }
                    destination.sync_all()
                },
            );
            assert!(result.is_err());
            drop(destination);
            drop(source);
            drop(staging);
            assert!(!staging_path.exists());
        }
    }

    #[test]
    fn snapshot_copy_barrier_process_helper() {
        use std::time::Instant;

        let Some(database) = std::env::var_os("HELEOS_TEST_SNAPSHOT_DATABASE") else {
            return;
        };
        let barrier = PathBuf::from(
            std::env::var_os("HELEOS_TEST_SNAPSHOT_COPY_BARRIER")
                .expect("snapshot barrier directory is configured"),
        );
        let reader = Store::open_read_only(PathBuf::from(database)).expect("child opens reader");
        fs::write(barrier.join("reader-open"), b"ready").expect("signal reader open");
        let deadline = Instant::now() + Duration::from_secs(15);
        while !barrier.join("release-reader").is_file() {
            assert!(
                Instant::now() < deadline,
                "timed out waiting to release snapshot reader"
            );
            std::thread::sleep(Duration::from_millis(10));
        }
        drop(reader);
        fs::write(barrier.join("reader-released"), b"released").expect("signal reader released");
    }

    #[test]
    fn shared_lock_blocks_writer_mid_copy_and_for_complete_reader_lifetime() {
        use std::process::Command;
        use std::time::Instant;

        fn wait_for_file(path: &Path) {
            let deadline = Instant::now() + Duration::from_secs(15);
            while !path.is_file() {
                assert!(Instant::now() < deadline, "timed out waiting for {path:?}");
                std::thread::sleep(Duration::from_millis(10));
            }
        }

        let source_root = tempfile::Builder::new()
            .prefix("heleos-copy-barrier-source-")
            .tempdir()
            .expect("create source directory");
        apply_private_permissions(source_root.path()).expect("harden source directory");
        let source_path =
            fs::canonicalize(source_root.path()).expect("canonicalize source directory");
        let database = source_path.join("foundation.sqlite3");
        let mut writer = Store::open_writer(&database).expect("open fixture writer");
        writer.migrate().expect("migrate fixture database");
        writer
            .with_immediate_transaction(|transaction| {
                transaction
                    .execute_batch("CREATE TABLE private_snapshot_padding (bytes BLOB NOT NULL);")
                    .map_err(database_error)?;
                transaction
                    .execute(
                        "INSERT INTO private_snapshot_padding (bytes) VALUES (zeroblob(?1))",
                        [i64::try_from(3 * SNAPSHOT_COPY_CHUNK_BYTES)
                            .map_err(|_| HeleosError::PolicyDenied)?],
                    )
                    .map_err(database_error)?;
                Ok(())
            })
            .expect("pad fixture beyond two copy chunks");
        writer
            .connection
            .execute_batch("PRAGMA wal_checkpoint(TRUNCATE);")
            .expect("checkpoint padded fixture");
        drop(writer);
        assert!(
            fs::metadata(&database)
                .expect("padded database metadata")
                .len()
                > u64::try_from(2 * SNAPSHOT_COPY_CHUNK_BYTES).expect("chunk length fits u64")
        );

        let barrier = tempfile::Builder::new()
            .prefix("heleos-copy-barrier-signals-")
            .tempdir()
            .expect("create barrier directory");
        let mut child = Command::new(std::env::current_exe().expect("locate unit test executable"))
            .arg("--exact")
            .arg("store::tests::snapshot_copy_barrier_process_helper")
            .arg("--nocapture")
            .env("HELEOS_TEST_SNAPSHOT_DATABASE", &database)
            .env("HELEOS_TEST_SNAPSHOT_COPY_BARRIER", barrier.path())
            .spawn()
            .expect("spawn snapshot child");

        wait_for_file(&barrier.path().join("copied"));
        assert!(matches!(
            Store::open_writer(&database),
            Err(HeleosError::WriterBusy)
        ));

        fs::write(barrier.path().join("continue"), b"continue").expect("release chunked copy");
        wait_for_file(&barrier.path().join("reader-open"));
        assert!(matches!(
            Store::open_writer(&database),
            Err(HeleosError::WriterBusy)
        ));

        fs::write(barrier.path().join("release-reader"), b"release").expect("release reader");
        wait_for_file(&barrier.path().join("reader-released"));
        assert!(child.wait().expect("wait for snapshot child").success());
        drop(Store::open_writer(&database).expect("writer resumes after reader drop"));
    }

    fn migrated_reader_fixture(prefix: &str) -> (tempfile::TempDir, PathBuf) {
        let root = tempfile::Builder::new()
            .prefix(prefix)
            .tempdir()
            .expect("create reader fixture directory");
        apply_private_permissions(root.path()).expect("harden reader fixture directory");
        let canonical = fs::canonicalize(root.path()).expect("canonicalize reader fixture");
        let database = canonical.join("foundation.sqlite3");
        let mut writer = Store::open_writer(&database).expect("open reader fixture writer");
        writer.migrate().expect("migrate reader fixture");
        drop(writer);
        (root, database)
    }

    fn isolated_snapshot_parent(prefix: &str) -> (tempfile::TempDir, ReadOnlySnapshotParent) {
        let root = tempfile::Builder::new()
            .prefix(prefix)
            .tempdir()
            .expect("create isolated snapshot parent");
        apply_private_permissions(root.path()).expect("harden isolated snapshot parent");
        let canonical = fs::canonicalize(root.path()).expect("canonicalize snapshot parent");
        let parent = ReadOnlySnapshotParent::retain_path_for_test(&canonical)
            .expect("retain isolated snapshot parent");
        (root, parent)
    }

    #[test]
    fn retained_snapshot_parent_rechecks_the_exact_captured_directory() {
        let root = tempfile::Builder::new()
            .prefix("heleos-retained-snapshot-parent-")
            .tempdir()
            .expect("create retained-parent fixture");
        apply_private_permissions(root.path()).expect("harden retained-parent fixture");
        let canonical = fs::canonicalize(root.path()).expect("canonicalize retained parent");

        let parent = ReadOnlySnapshotParent::retain_path_for_test(&canonical)
            .expect("retain controlled snapshot parent");

        assert_eq!(parent.path(), canonical);
        assert!(
            parent
                .retained_file()
                .metadata()
                .expect("retained parent metadata")
                .is_dir()
        );
        parent.recheck().expect("recheck retained snapshot parent");
    }

    #[cfg(unix)]
    #[test]
    fn unix_snapshot_parent_policy_is_exactly_trusted_owner_and_private_or_sticky() {
        let effective_uid = 1_000;

        for trusted_uid in [effective_uid, 0] {
            for admitted_mode in [0o700, 0o750, 0o755, 0o1700, 0o1777] {
                assert!(snapshot_unix_parent_policy_accepts(
                    trusted_uid,
                    admitted_mode,
                    effective_uid
                ));
            }
            for denied_mode in [0o020, 0o002, 0o022, 0o077, 0o777] {
                assert!(!snapshot_unix_parent_policy_accepts(
                    trusted_uid,
                    denied_mode,
                    effective_uid
                ));
            }
        }

        for untrusted_uid in [1, effective_uid + 1, u32::MAX] {
            for mode in [0o700, 0o755, 0o1700, 0o1777] {
                assert!(!snapshot_unix_parent_policy_accepts(
                    untrusted_uid,
                    mode,
                    effective_uid
                ));
            }
        }
    }

    #[cfg(unix)]
    #[test]
    fn retained_snapshot_parent_accepts_shared_sticky_without_mutating_it() {
        use std::os::unix::fs::PermissionsExt;

        let root = tempfile::Builder::new()
            .prefix("heleos-sticky-snapshot-parent-")
            .tempdir()
            .expect("create sticky-parent fixture");
        fs::set_permissions(root.path(), fs::Permissions::from_mode(0o1777))
            .expect("set shared sticky mode");
        let before = fs::symlink_metadata(root.path())
            .expect("shared sticky metadata before admission")
            .permissions()
            .mode()
            & 0o7777;

        let parent = ReadOnlySnapshotParent::retain_path_for_test(root.path())
            .expect("admit current-owned shared sticky parent");
        parent.recheck().expect("recheck shared sticky parent");

        let after = fs::symlink_metadata(root.path())
            .expect("shared sticky metadata after admission")
            .permissions()
            .mode()
            & 0o7777;
        assert_eq!(before, 0o1777);
        assert_eq!(after, before, "Store mutated the retained temp parent");
    }

    #[cfg(unix)]
    #[test]
    fn retained_snapshot_parent_rejects_nonsticky_shared_writable_without_mutating_it() {
        use std::os::unix::fs::PermissionsExt;

        let root = tempfile::Builder::new()
            .prefix("heleos-nonsticky-snapshot-parent-")
            .tempdir()
            .expect("create nonsticky-parent fixture");
        fs::set_permissions(root.path(), fs::Permissions::from_mode(0o0777))
            .expect("set unsafe shared mode");

        assert!(matches!(
            ReadOnlySnapshotParent::retain_path_for_test(root.path()),
            Err(HeleosError::PolicyDenied)
        ));
        assert_eq!(
            fs::symlink_metadata(root.path())
                .expect("unsafe parent metadata after rejection")
                .permissions()
                .mode()
                & 0o7777,
            0o0777,
            "Store mutated the rejected temp parent"
        );
    }

    #[cfg(unix)]
    #[test]
    fn retained_snapshot_parent_rejects_every_mode_drift_without_rewriting_it() {
        use std::os::unix::fs::PermissionsExt;

        for changed_mode in [0o0750, 0o1777, 0o0777] {
            let root = tempfile::Builder::new()
                .prefix("heleos-snapshot-parent-mode-drift-")
                .tempdir()
                .expect("create parent-mode-drift fixture");
            fs::set_permissions(root.path(), fs::Permissions::from_mode(0o0700))
                .expect("set captured parent mode");
            let parent = ReadOnlySnapshotParent::retain_path_for_test(root.path())
                .expect("retain parent before mode drift");

            fs::set_permissions(root.path(), fs::Permissions::from_mode(changed_mode))
                .expect("apply parent mode drift");

            assert!(matches!(
                parent.recheck_retained_policy(),
                Err(HeleosError::PolicyDenied)
            ));
            assert!(matches!(parent.recheck(), Err(HeleosError::PolicyDenied)));
            assert_eq!(
                fs::symlink_metadata(root.path())
                    .expect("mode-drift parent metadata")
                    .permissions()
                    .mode()
                    & 0o7777,
                changed_mode,
                "Store rewrote a drifted parent mode"
            );
        }
    }

    #[test]
    fn snapshot_child_is_one_private_empty_component_before_first_byte() {
        let root = tempfile::Builder::new()
            .prefix("heleos-empty-snapshot-child-")
            .tempdir()
            .expect("create empty-child fixture");
        apply_private_permissions(root.path()).expect("harden empty-child fixture");
        let canonical = fs::canonicalize(root.path()).expect("canonicalize empty-child parent");
        let parent = ReadOnlySnapshotParent::retain_path_for_test(&canonical)
            .expect("retain empty-child parent");

        let scratch = ReadSnapshotDirectory::create(&parent).expect("create snapshot child");

        assert!(matches!(
            Path::new(scratch.child_name())
                .components()
                .collect::<Vec<_>>()
                .as_slice(),
            [std::path::Component::Normal(_)]
        ));
        assert!(
            scratch
                .child_name()
                .to_string_lossy()
                .starts_with("heleos-read-snapshot-")
        );
        assert_eq!(
            fs::read_dir(scratch.path())
                .expect("enumerate new snapshot child")
                .count(),
            0
        );
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;

            assert_eq!(
                fs::symlink_metadata(scratch.path())
                    .expect("snapshot child metadata")
                    .permissions()
                    .mode()
                    & 0o777,
                0o700
            );
        }
        let child = scratch.path().to_owned();
        scratch.close().expect("checked-close empty snapshot child");
        assert!(!child.exists());
    }

    #[test]
    fn child_database_wal_and_shm_cross_private_zero_length_barriers_before_first_byte() {
        let (_root, database) = migrated_reader_fixture("heleos-first-byte-barriers-");
        let (_snapshot_root, parent) = isolated_snapshot_parent("heleos-first-byte-parent-");
        reset_snapshot_first_byte_events_for_test();

        let reader = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open first-byte reader");
        assert_eq!(
            snapshot_first_byte_events_for_test(),
            [
                SnapshotFirstByteEvent::ChildEmptyBeforeHardening,
                SnapshotFirstByteEvent::ChildEmptyAfterHardening,
                SnapshotFirstByteEvent::DatabasePrivateAndEmpty,
                SnapshotFirstByteEvent::WalPrivateAndEmpty,
                SnapshotFirstByteEvent::ShmPrivateAndEmpty,
            ]
        );
        let scratch = reader
            ._snapshot_directory
            .as_ref()
            .expect("reader owns first-byte scratch");
        for name in [
            "snapshot.sqlite3",
            "snapshot.sqlite3-wal",
            "snapshot.sqlite3-shm",
        ] {
            verify_private_permissions(&scratch.path().join(name))
                .expect("scratch database artifact remains private");
        }
        reader.close_read_only().expect("close first-byte reader");
    }

    #[test]
    fn every_pre_first_byte_checkpoint_records_real_handle_evidence_and_can_fail_closed() {
        let (_root, database) = migrated_reader_fixture("heleos-first-byte-faults-");
        let (_snapshot_root, parent) = isolated_snapshot_parent("heleos-first-byte-fault-parent-");

        for checkpoint in SnapshotFirstByteCheckpoint::ALL {
            let before = snapshot_children(parent.path()).expect("inventory before byte fault");
            reset_snapshot_first_byte_witnesses_for_test();
            set_snapshot_first_byte_checkpoint_fault_for_test(checkpoint);

            let result = Store::open_read_only_with_snapshot_parent(&database, &parent);

            assert!(matches!(result, Err(HeleosError::PolicyDenied)));
            let witnesses = snapshot_first_byte_witnesses_for_test();
            let witness = witnesses
                .iter()
                .find(|witness| witness.checkpoint == checkpoint)
                .unwrap_or_else(|| panic!("missing real witness for {checkpoint:?}"));
            assert!(witness.private_verified);
            match checkpoint {
                SnapshotFirstByteCheckpoint::DatabaseCreation => {
                    assert!(witness.child_empty);
                    assert_eq!(witness.length, None);
                    #[cfg(unix)]
                    assert_eq!(witness.unix_mode, Some(0o700));
                }
                SnapshotFirstByteCheckpoint::FirstDatabaseWrite
                | SnapshotFirstByteCheckpoint::FirstWalWrite
                | SnapshotFirstByteCheckpoint::FirstShmWrite => {
                    assert_eq!(witness.length, Some(0));
                    #[cfg(unix)]
                    assert_eq!(witness.unix_mode, Some(0o600));
                }
            }
            assert_eq!(
                snapshot_children(parent.path()).expect("inventory after byte fault"),
                before
            );
            clear_snapshot_first_byte_checkpoint_fault_for_test();
        }
    }

    #[test]
    fn disabled_temp_path_is_source_frozen_and_not_cleanup_authority() {
        let source = include_str!("mod.rs");
        let disabled_cleanup = [".disable_", "cleanup(true)"].concat();
        assert_eq!(source.matches(&disabled_cleanup).count(), 1);
        for forbidden in [
            ["override_", "temp_dir"].concat(),
            ["temp", "dir_in("].concat(),
            ["std::fs::remove_", "dir_all"].concat(),
            ["fs::remove_", "dir_all(&self.child_path"].concat(),
        ] {
            assert!(
                !source.contains(&forbidden),
                "forbidden source: {forbidden}"
            );
        }

        reset_snapshot_temp_path_events_for_test();
        let root = tempfile::Builder::new()
            .prefix("heleos-disabled-temp-path-")
            .tempdir()
            .expect("create disabled-TempPath fixture");
        apply_private_permissions(root.path()).expect("harden disabled-TempPath fixture");
        let canonical = fs::canonicalize(root.path()).expect("canonicalize TempPath fixture");
        let parent = ReadOnlySnapshotParent::retain_path_for_test(&canonical)
            .expect("retain disabled-TempPath parent");
        let scratch = ReadSnapshotDirectory::create(&parent)
            .expect("disabled TempPath leaves child under Store ownership");
        assert_eq!(
            snapshot_temp_path_events_for_test(),
            [
                SnapshotTempPathEvent::VerifiedBeforeDrop,
                SnapshotTempPathEvent::VerifiedAfterDrop,
            ]
        );
        assert_eq!(
            snapshot_children(parent.path()).expect("inventory after disabled TempPath drop"),
            [scratch.child_name().to_os_string()]
        );
        let path = scratch.path().to_owned();
        scratch.close().expect("checked-close TempPath fixture");
        assert!(!path.exists());
    }

    #[test]
    fn public_reader_runs_two_capacity_checks_and_caller_admitted_runs_none() {
        let (_root, database) = migrated_reader_fixture("heleos-capacity-mode-reader-");

        reset_snapshot_capacity_checks_for_test();
        let public = Store::open_read_only(&database).expect("open public reader");
        assert_eq!(snapshot_capacity_checks_for_test(), 2);
        public
            .close_read_only()
            .expect("checked-close public reader");

        let parent = ReadOnlySnapshotParent::retain_default().expect("retain default parent");
        reset_snapshot_capacity_checks_for_test();
        let admitted = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open caller-admitted reader");
        assert_eq!(snapshot_capacity_checks_for_test(), 0);
        admitted
            .close_read_only()
            .expect("checked-close caller-admitted reader");
    }

    #[test]
    fn every_store_managed_capacity_operation_is_one_shot_and_cleans_exactly() {
        const HELPER_ENV: &str = "HELEOS_TEST_STORE_CAPACITY_OPERATION_MATRIX";
        if std::env::var_os(HELPER_ENV).is_none() {
            let selector = tempfile::Builder::new()
                .prefix("heleos-capacity-operation-selector-")
                .tempdir()
                .expect("create capacity-operation selector");
            apply_private_permissions(selector.path()).expect("harden capacity-operation selector");
            let status = std::process::Command::new(
                std::env::current_exe().expect("locate unit test executable"),
            )
            .arg("--exact")
            .arg("store::tests::every_store_managed_capacity_operation_is_one_shot_and_cleans_exactly")
            .arg("--nocapture")
            .env(HELPER_ENV, "1")
            .env("TMPDIR", selector.path())
            .env("TMP", selector.path())
            .env("TEMP", selector.path())
            .status()
            .expect("run isolated capacity-operation matrix");
            assert!(status.success());
            return;
        }

        let (_root, database) = migrated_reader_fixture("heleos-capacity-operation-sites-");
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain capacity inventory");

        for operation in SNAPSHOT_CAPACITY_OPERATIONS {
            for primary in SnapshotInjectedPrimaryKind::ALL {
                let before =
                    snapshot_children(parent.path()).expect("inventory before capacity fault");
                set_snapshot_open_operation_fault_for_test(operation, primary);

                let result = Store::open_read_only(&database);

                assert!(
                    snapshot_open_operation_fault_fired_for_test(),
                    "capacity fault did not fire: {operation:?} {primary:?}"
                );
                match primary {
                    SnapshotInjectedPrimaryKind::Io => assert!(matches!(
                        result,
                        Err(HeleosError::Io(error)) if error.kind() == io::ErrorKind::Other
                    )),
                    SnapshotInjectedPrimaryKind::Database => {
                        assert!(matches!(result, Err(HeleosError::Database)))
                    }
                    SnapshotInjectedPrimaryKind::PolicyDenied => {
                        assert!(matches!(result, Err(HeleosError::PolicyDenied)))
                    }
                }
                assert_eq!(
                    snapshot_children(parent.path()).expect("inventory after capacity fault"),
                    before,
                    "capacity fault leaked scratch: {operation:?} {primary:?}"
                );
                clear_snapshot_open_operation_fault_for_test();
            }
        }
    }

    #[test]
    fn sqlite_endpoint_barrier_runs_at_all_four_committed_boundaries() {
        let (_root, database) = migrated_reader_fixture("heleos-endpoint-count-");
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain endpoint parent");
        reset_snapshot_endpoint_barriers_for_test();

        let reader = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open endpoint-count reader");

        assert_eq!(snapshot_endpoint_barriers_for_test(), [1, 1, 1, 1]);
        reader
            .close_read_only()
            .expect("close endpoint-count reader");
    }

    #[test]
    fn windows_four_by_three_endpoint_marker_matrix_source_contract_is_frozen() {
        let source = include_str!("mod.rs");
        let implementation_end = source
            .find("\n#[cfg(test)]\nmod tests {")
            .expect("Store tests have a bounded implementation prefix");
        let implementation = &source[..implementation_end];
        assert_eq!(
            implementation
                .matches("snapshot_endpoint_expected_marker_for_test(")
                .count(),
            4,
            "Windows marker-mismatch seam must have one definition and three endpoint uses"
        );
        for token in [
            "creation_time: expected.creation_time ^ 1",
            "SnapshotEndpointAxis::Parent, parent_marker",
            "SnapshotEndpointAxis::Child, child_marker",
            "SnapshotEndpointAxis::Database, marker",
        ] {
            assert!(
                implementation.contains(token),
                "Windows endpoint marker seam lost {token}"
            );
        }
        let signature = "#[cfg(windows)]\n    #[test]\n    fn every_windows_endpoint_barrier_rejects_each_axis_marker_mismatch()";
        let start = source
            .find(signature)
            .expect("Windows four-by-three endpoint matrix exists");
        let body_start = start + signature.len();
        let end = source[body_start..]
            .find("\n    #[cfg(unix)]")
            .map(|offset| body_start + offset)
            .expect("Windows endpoint matrix has a bounded test body");
        let matrix = &source[start..end];
        for token in [
            "for barrier in SnapshotEndpointBarrier::ALL",
            "for axis in SnapshotEndpointAxis::ALL",
            "SnapshotEndpointMutation::MarkerMismatch",
            "Store::open_read_only_with_snapshot_parent(&database, &parent)",
            "Err(HeleosError::PolicyDenied)",
            "assert_snapshot_endpoint_marker_fault_for_test(barrier, axis)",
            "snapshot_children(parent.path())",
            "parent.recheck()",
        ] {
            assert!(
                matrix.contains(token),
                "Windows endpoint matrix lost {token}"
            );
        }
    }

    #[cfg(windows)]
    #[test]
    fn every_windows_endpoint_barrier_rejects_each_axis_marker_mismatch() {
        for barrier in SnapshotEndpointBarrier::ALL {
            for axis in SnapshotEndpointAxis::ALL {
                let (_source, database) =
                    migrated_reader_fixture("heleos-windows-endpoint-source-");
                let source_marker = FileMarker::from_metadata(
                    &fs::symlink_metadata(&database).expect("Windows endpoint source metadata"),
                );
                let (_snapshot_root, parent) =
                    isolated_snapshot_parent("heleos-windows-endpoint-parent-");
                let parent_marker = FileMarker::from_metadata(
                    &parent
                        .retained_file()
                        .metadata()
                        .expect("Windows endpoint parent metadata"),
                );
                let before = snapshot_children(parent.path())
                    .expect("Windows endpoint inventory before marker mismatch");
                set_snapshot_endpoint_fault_for_test(
                    barrier,
                    axis,
                    SnapshotEndpointMutation::MarkerMismatch,
                );

                let result = Store::open_read_only_with_snapshot_parent(&database, &parent);

                assert!(
                    matches!(result, Err(HeleosError::PolicyDenied)),
                    "{barrier:?} {axis:?} did not reject the marker mismatch"
                );
                assert_snapshot_endpoint_marker_fault_for_test(barrier, axis);
                assert_eq!(
                    snapshot_children(parent.path())
                        .expect("Windows endpoint inventory after marker mismatch"),
                    before,
                    "{barrier:?} {axis:?} redirected or leaked cleanup"
                );
                assert_eq!(
                    FileMarker::from_metadata(
                        &fs::symlink_metadata(&database)
                            .expect("Windows endpoint source metadata after mismatch")
                    ),
                    source_marker,
                    "{barrier:?} {axis:?} changed the source endpoint"
                );
                assert_eq!(
                    FileMarker::from_metadata(
                        &parent
                            .retained_file()
                            .metadata()
                            .expect("Windows endpoint parent metadata after mismatch")
                    ),
                    parent_marker,
                    "{barrier:?} {axis:?} changed the retained parent"
                );
                parent.recheck().expect("retained Windows parent preserved");
                clear_snapshot_endpoint_fault_for_test();
            }
        }
    }

    #[cfg(unix)]
    #[test]
    fn every_sqlite_endpoint_barrier_rejects_each_real_unix_endpoint_mutation() {
        for barrier in SnapshotEndpointBarrier::ALL {
            for axis in SnapshotEndpointAxis::ALL {
                for mutation in SnapshotEndpointMutation::ALL {
                    let source = tempfile::Builder::new()
                        .prefix("heleos-endpoint-source-")
                        .tempdir()
                        .expect("create endpoint source root");
                    apply_private_permissions(source.path()).expect("harden endpoint source root");
                    let database = fs::canonicalize(source.path())
                        .expect("canonicalize endpoint source root")
                        .join("foundation.sqlite3");
                    let mut writer = Store::open_writer(&database).expect("open endpoint writer");
                    writer.migrate().expect("migrate endpoint fixture");
                    drop(writer);

                    let outer = tempfile::Builder::new()
                        .prefix("heleos-endpoint-parent-")
                        .tempdir()
                        .expect("create endpoint parent outer");
                    apply_private_permissions(outer.path()).expect("harden endpoint outer");
                    let parent_path = outer.path().join("selected");
                    fs::create_dir(&parent_path).expect("create endpoint selected parent");
                    apply_private_permissions(&parent_path)
                        .expect("harden selected endpoint parent");
                    let parent = ReadOnlySnapshotParent::retain_path_for_test(&parent_path)
                        .expect("retain endpoint selected parent");
                    set_snapshot_endpoint_fault_for_test(barrier, axis, mutation);

                    let result = Store::open_read_only_with_snapshot_parent(&database, &parent);
                    assert!(
                        matches!(result, Err(HeleosError::PolicyDenied)),
                        "{barrier:?} {axis:?} {mutation:?} did not return PolicyDenied"
                    );
                    assert_snapshot_endpoint_fault_namespace_for_test(
                        outer.path(),
                        &parent_path,
                        axis,
                        mutation,
                    );
                    cleanup_snapshot_endpoint_fault_namespace_for_test(
                        outer.path(),
                        &parent_path,
                        axis,
                    );
                    clear_snapshot_endpoint_fault_for_test();
                }
            }
        }
    }

    #[test]
    fn checked_reader_close_removes_live_scratch_after_sqlite_closes() {
        let (_root, database) = migrated_reader_fixture("heleos-checked-reader-close-");
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain default parent");
        let reader = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open checked-close reader");
        let scratch = reader
            ._snapshot_directory
            .as_ref()
            .expect("reader owns scratch")
            .path()
            .to_owned();
        assert!(scratch.is_dir());
        assert!(matches!(
            Store::open_writer(&database),
            Err(HeleosError::WriterBusy)
        ));

        reader.close_read_only().expect("checked-close reader");

        assert!(!scratch.exists());
        parent
            .recheck()
            .expect("parent remains retained after close");
        drop(Store::open_writer(&database).expect("writer resumes after checked reader close"));
    }

    #[test]
    fn store_owned_parent_clone_outlives_the_callers_snapshot_parent() {
        let (_root, database) = migrated_reader_fixture("heleos-store-parent-clone-");
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain caller parent");
        let reader = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open reader with caller parent");
        let scratch = reader
            ._snapshot_directory
            .as_ref()
            .expect("reader owns scratch")
            .path()
            .to_owned();

        drop(parent);
        assert!(scratch.is_dir());
        reader
            .close_read_only()
            .expect("close after caller parent drop");
        assert!(!scratch.exists());
    }

    #[cfg(unix)]
    #[test]
    fn checked_close_preserves_a_rebound_child_and_reports_policy_denied() {
        let (_root, database) = migrated_reader_fixture("heleos-reader-child-rebind-");
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain default parent");
        let reader = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open child-rebind reader");
        let scratch = reader
            ._snapshot_directory
            .as_ref()
            .expect("reader owns scratch")
            .path()
            .to_owned();
        let moved = scratch.with_extension("retained-original");
        fs::rename(&scratch, &moved).expect("move retained scratch aside");
        fs::create_dir(&scratch).expect("install child decoy");
        apply_private_permissions(&scratch).expect("harden child decoy");

        assert!(matches!(
            reader.close_read_only(),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(scratch.is_dir(), "checked close deleted the child decoy");

        fs::remove_dir_all(&scratch).expect("remove child decoy fixture");
        fs::remove_dir_all(&moved).expect("remove retained original fixture");
    }

    #[test]
    fn caller_admitted_invalid_snapshot_preserves_database_taxonomy_and_cleans_child() {
        let root = tempfile::Builder::new()
            .prefix("heleos-invalid-reader-database-")
            .tempdir()
            .expect("create invalid-reader fixture");
        apply_private_permissions(root.path()).expect("harden invalid-reader fixture");
        let database = root.path().join("foundation.sqlite3");
        fs::write(&database, b"not sqlite").expect("write invalid database");
        apply_private_permissions(&database).expect("harden invalid database");
        fs::write(sidecar_path(&database, ".writer.lock"), b"").expect("write reader lock");
        apply_private_permissions(&sidecar_path(&database, ".writer.lock"))
            .expect("harden reader lock");
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain default parent");
        let before = snapshot_children(parent.path()).expect("list snapshot children before open");

        let result = Store::open_read_only_with_snapshot_parent(&database, &parent);

        assert!(matches!(result, Err(HeleosError::Database)));
        assert_eq!(
            snapshot_children(parent.path()).expect("list snapshot children after failure"),
            before
        );
    }

    #[test]
    fn retained_snapshot_selector_process_helper() {
        let Some(result_path) = std::env::var_os("HELEOS_TEST_SNAPSHOT_SELECTOR_RESULT") else {
            return;
        };
        reset_snapshot_selector_calls_for_test();
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain default parent");
        if let Some(ready) = std::env::var_os("HELEOS_TEST_SNAPSHOT_SELECTOR_READY") {
            let ready = PathBuf::from(ready);
            fs::write(&ready, b"captured").expect("record retained selector capture");
            let continue_path = PathBuf::from(
                std::env::var_os("HELEOS_TEST_SNAPSHOT_SELECTOR_CONTINUE")
                    .expect("selector continue path is configured"),
            );
            let deadline = std::time::Instant::now() + Duration::from_secs(15);
            while !continue_path.is_file() {
                assert!(
                    std::time::Instant::now() < deadline,
                    "timed out waiting for selector repoint"
                );
                std::thread::sleep(Duration::from_millis(10));
            }
        }
        let scratch = ReadSnapshotDirectory::create(&parent)
            .expect("create scratch beneath captured default parent");
        assert_eq!(snapshot_selector_calls_for_test(), 1);
        assert_eq!(scratch.path().parent(), Some(parent.path()));
        parent.recheck().expect("captured parent remains bound");
        assert_eq!(snapshot_selector_calls_for_test(), 1);
        scratch.close().expect("checked-close selector fixture");
        fs::write(result_path, b"one-captured-selector").expect("record captured-selector result");
    }

    #[test]
    fn retain_default_invokes_the_tempfile_selector_exactly_once_in_a_subprocess() {
        use std::process::Command;

        let selected = tempfile::Builder::new()
            .prefix("heleos-selected-default-parent-")
            .tempdir()
            .expect("create selected default parent");
        apply_private_permissions(selected.path()).expect("harden selected default parent");
        let selected_path =
            fs::canonicalize(selected.path()).expect("canonicalize selected default parent");
        let result = selected_path.join("selector-result");

        let status = Command::new(std::env::current_exe().expect("locate unit test executable"))
            .arg("--exact")
            .arg("store::tests::retained_snapshot_selector_process_helper")
            .arg("--nocapture")
            .env("TMPDIR", &selected_path)
            .env("TMP", &selected_path)
            .env("TEMP", &selected_path)
            .env("HELEOS_TEST_SNAPSHOT_SELECTOR_RESULT", &result)
            .status()
            .expect("run retained-selector subprocess");

        assert!(status.success());
        assert_eq!(
            fs::read(&result).expect("read retained-selector result"),
            b"one-captured-selector"
        );
        assert!(
            snapshot_children(&selected_path)
                .expect("list selected parent after subprocess")
                .is_empty()
        );
    }

    #[cfg(unix)]
    #[test]
    fn selector_symlink_repoint_after_capture_cannot_redirect_scratch_or_cleanup() {
        use std::os::unix::fs::symlink;
        use std::process::Command;

        let outer = tempfile::Builder::new()
            .prefix("heleos-selector-repoint-")
            .tempdir()
            .expect("create selector-repoint fixture");
        apply_private_permissions(outer.path()).expect("harden selector-repoint fixture");
        let parent_a = outer.path().join("parent-a");
        let parent_b = outer.path().join("parent-b");
        fs::create_dir(&parent_a).expect("create selector parent A");
        fs::create_dir(&parent_b).expect("create selector parent B");
        apply_private_permissions(&parent_a).expect("harden selector parent A");
        apply_private_permissions(&parent_b).expect("harden selector parent B");
        fs::write(parent_b.join("decoy"), b"untouched").expect("write selector decoy");
        apply_private_permissions(&parent_b.join("decoy")).expect("harden selector decoy");
        let selector = outer.path().join("selector");
        symlink(&parent_a, &selector).expect("point selector at parent A");
        let ready = outer.path().join("ready");
        let continue_path = outer.path().join("continue");
        let result = outer.path().join("result");

        let mut child = Command::new(std::env::current_exe().expect("locate unit test executable"))
            .arg("--exact")
            .arg("store::tests::retained_snapshot_selector_process_helper")
            .arg("--nocapture")
            .env("TMPDIR", &selector)
            .env("TMP", &selector)
            .env("TEMP", &selector)
            .env("HELEOS_TEST_SNAPSHOT_SELECTOR_RESULT", &result)
            .env("HELEOS_TEST_SNAPSHOT_SELECTOR_READY", &ready)
            .env("HELEOS_TEST_SNAPSHOT_SELECTOR_CONTINUE", &continue_path)
            .spawn()
            .expect("spawn selector-repoint child");
        let deadline = std::time::Instant::now() + Duration::from_secs(15);
        while !ready.is_file() {
            assert!(
                std::time::Instant::now() < deadline,
                "timed out waiting for selector capture"
            );
            std::thread::sleep(Duration::from_millis(10));
        }
        fs::remove_file(&selector).expect("remove selector link to A");
        symlink(&parent_b, &selector).expect("repoint selector at parent B");
        fs::write(&continue_path, b"continue").expect("release selector child");

        assert!(
            child
                .wait()
                .expect("wait for selector-repoint child")
                .success()
        );
        assert_eq!(
            fs::read(&result).expect("read selector result"),
            b"one-captured-selector"
        );
        assert!(
            snapshot_children(&parent_a)
                .expect("list parent A")
                .is_empty()
        );
        assert!(
            snapshot_children(&parent_b)
                .expect("list parent B")
                .is_empty()
        );
        assert_eq!(
            fs::read(parent_b.join("decoy")).expect("read selector decoy"),
            b"untouched"
        );
    }

    #[test]
    fn one_generated_collision_retries_without_leaking_a_child() {
        let root = tempfile::Builder::new()
            .prefix("heleos-snapshot-collision-")
            .tempdir()
            .expect("create collision fixture");
        apply_private_permissions(root.path()).expect("harden collision fixture");
        let canonical = fs::canonicalize(root.path()).expect("canonicalize collision fixture");
        let parent = ReadOnlySnapshotParent::retain_path_for_test(&canonical)
            .expect("retain collision parent");

        arm_generated_candidate_collision_for_test();
        let scratch = ReadSnapshotDirectory::create(&parent)
            .expect("normal Builder::make_in retries genuine generated collision");
        let collision = take_generated_candidate_collision_for_test()
            .expect("capture actual first generated collision");
        let collision_path = parent.path().join(&collision.name);

        assert_eq!(
            snapshot_children(parent.path())
                .expect("list collision children")
                .len(),
            2
        );
        assert_eq!(
            FileMarker::from_metadata(
                &fs::symlink_metadata(&collision_path)
                    .expect("colliding child metadata after retry")
            ),
            collision.marker
        );
        assert_eq!(
            fs::read(collision_path.join("sentinel")).expect("read collision sentinel"),
            b"do-not-touch"
        );
        let path = scratch.path().to_owned();
        scratch.close().expect("close collision-retry scratch");
        assert!(!path.exists());
        assert!(collision_path.is_dir());
    }

    #[test]
    fn lexical_candidate_sentinel_alone_maps_to_policy_denied() {
        let lexical = snapshot_lexical_candidate_error();
        assert!(matches!(
            map_snapshot_name_generation_error(lexical),
            HeleosError::PolicyDenied
        ));

        let genuine_invalid = io::Error::new(io::ErrorKind::InvalidInput, "capability create");
        assert!(matches!(
            map_snapshot_name_generation_error(genuine_invalid),
            HeleosError::Io(error) if error.kind() == io::ErrorKind::InvalidInput
        ));
    }

    #[test]
    fn portable_not_a_directory_binding_error_is_policy_denied_and_invalid_input_is_io() {
        assert!(matches!(
            map_snapshot_binding_error(io::Error::from(io::ErrorKind::NotADirectory)),
            HeleosError::PolicyDenied
        ));
        assert!(matches!(
            map_snapshot_binding_error(io::Error::from(io::ErrorKind::InvalidInput)),
            HeleosError::Io(error) if error.kind() == io::ErrorKind::InvalidInput
        ));
    }

    #[cfg(unix)]
    #[test]
    fn snapshot_binding_barrier_maps_not_found_eloop_and_enotdir_only_to_policy_denied() {
        for code in [libc::ENOENT, libc::ELOOP, libc::ENOTDIR] {
            assert!(matches!(
                map_snapshot_binding_error(io::Error::from_raw_os_error(code)),
                HeleosError::PolicyDenied
            ));
        }
        assert!(matches!(
            map_snapshot_binding_error(io::Error::from_raw_os_error(libc::EACCES)),
            HeleosError::Io(error) if error.raw_os_error() == Some(libc::EACCES)
        ));

        let root = tempfile::Builder::new()
            .prefix("heleos-binding-enotdir-")
            .tempdir()
            .expect("create binding taxonomy fixture");
        apply_private_permissions(root.path()).expect("harden binding taxonomy root");
        let component = root.path().join("component");
        let moved = root.path().join("retained-component");
        fs::create_dir(&component).expect("create binding component");
        apply_private_permissions(&component).expect("harden binding component");
        let path = component.join("snapshot.sqlite3");
        fs::write(&path, b"retained").expect("write retained binding file");
        apply_private_permissions(&path).expect("harden retained binding file");
        let retained = File::open(&path).expect("open retained binding file");
        let marker =
            FileMarker::from_metadata(&retained.metadata().expect("retained binding metadata"));
        fs::rename(&component, &moved).expect("move binding component");
        fs::write(&component, b"not a directory").expect("install ENOTDIR component");

        assert!(matches!(
            recheck_snapshot_file_ambient(&path, &retained, &marker),
            Err(HeleosError::PolicyDenied)
        ));
    }

    #[test]
    fn failure_immediately_after_child_create_uses_provisional_cleanup() {
        let root = tempfile::Builder::new()
            .prefix("heleos-snapshot-provisional-failure-")
            .tempdir()
            .expect("create provisional-cleanup fixture");
        apply_private_permissions(root.path()).expect("harden provisional-cleanup fixture");
        let canonical = fs::canonicalize(root.path()).expect("canonicalize provisional fixture");
        let parent = ReadOnlySnapshotParent::retain_path_for_test(&canonical)
            .expect("retain provisional-cleanup parent");
        let before = snapshot_children(parent.path()).expect("list prefix inventory");

        let result = ReadSnapshotDirectory::create_with_test_fault(
            &parent,
            SnapshotCreateTestFault::ImmediatelyAfterChildCreateIo,
        );

        assert!(matches!(
            result,
            Err(HeleosError::Io(error)) if error.kind() == io::ErrorKind::Other
        ));
        assert_eq!(
            snapshot_children(parent.path()).expect("list inventory after provisional fault"),
            before
        );
    }

    #[test]
    fn uncertain_provisional_cleanup_is_attempted_once_and_never_retried_by_drop() {
        let root = tempfile::Builder::new()
            .prefix("heleos-snapshot-provisional-uncertain-")
            .tempdir()
            .expect("create provisional uncertainty fixture");
        apply_private_permissions(root.path()).expect("harden provisional uncertainty fixture");
        let canonical =
            fs::canonicalize(root.path()).expect("canonicalize provisional uncertainty fixture");
        let parent = ReadOnlySnapshotParent::retain_path_for_test(&canonical)
            .expect("retain provisional uncertainty parent");
        let before = snapshot_children(parent.path()).expect("list prefix inventory");
        arm_provisional_cleanup_io_once_for_test();

        let result = ReadSnapshotDirectory::create_with_test_fault(
            &parent,
            SnapshotCreateTestFault::ImmediatelyAfterChildCreateIo,
        );

        assert!(matches!(
            result,
            Err(HeleosError::Io(error)) if error.kind() == io::ErrorKind::Other
        ));
        assert_eq!(provisional_cleanup_attempts_for_test(), 1);
        assert_eq!(
            snapshot_children(parent.path())
                .expect("list inventory after uncertain provisional cleanup")
                .len(),
            before.len() + 1,
            "Drop retried and removed a child after cleanup authority became uncertain"
        );
    }

    #[test]
    fn post_create_io_failure_returns_io_and_leaks_no_child() {
        let root = tempfile::Builder::new()
            .prefix("heleos-snapshot-post-create-failure-")
            .tempdir()
            .expect("create post-create failure fixture");
        apply_private_permissions(root.path()).expect("harden post-create failure fixture");
        let canonical =
            fs::canonicalize(root.path()).expect("canonicalize post-create failure fixture");
        let parent = ReadOnlySnapshotParent::retain_path_for_test(&canonical)
            .expect("retain post-create failure parent");
        let before = snapshot_children(parent.path()).expect("list children before fault");

        let result = ReadSnapshotDirectory::create_with_test_fault(
            &parent,
            SnapshotCreateTestFault::AfterChildOpenIo,
        );

        assert!(matches!(
            result,
            Err(HeleosError::Io(error)) if error.kind() == io::ErrorKind::Other
        ));
        assert_eq!(
            snapshot_children(parent.path()).expect("list children after fault"),
            before
        );
    }

    #[test]
    fn every_committed_store_return_operation_cleans_and_preserves_exact_primary() {
        let (_root, database) = migrated_reader_fixture("heleos-reader-operation-matrix-");
        let source = Connection::open(&database).expect("open matrix live-WAL source");
        source
            .execute_batch(
                "PRAGMA journal_mode = WAL;
                 CREATE TABLE snapshot_operation_matrix_probe(value INTEGER NOT NULL);
                 INSERT INTO snapshot_operation_matrix_probe(value) VALUES (1);",
            )
            .expect("leave committed matrix WAL bytes");
        apply_private_permissions(&sidecar_path(&database, "-wal")).expect("harden matrix WAL");
        apply_private_permissions(&sidecar_path(&database, "-shm")).expect("harden matrix SHM");
        let (_snapshot_root, parent) = isolated_snapshot_parent("heleos-operation-matrix-parent-");

        let baseline_inventory =
            snapshot_children(parent.path()).expect("inventory before operation baseline");
        set_snapshot_open_operation_fault_for_test(
            SnapshotOpenOperation::Return,
            SnapshotInjectedPrimaryKind::PolicyDenied,
        );
        assert!(matches!(
            Store::open_read_only_with_snapshot_parent(&database, &parent),
            Err(HeleosError::PolicyDenied)
        ));
        let baseline_trace = snapshot_open_operation_trace_for_test();
        let child_created = baseline_trace
            .iter()
            .position(|operation| *operation == SnapshotOpenOperation::IntoPartsTransfer)
            .expect("baseline reaches the first post-create operation");
        assert_eq!(
            snapshot_children(parent.path()).expect("inventory after operation baseline"),
            baseline_inventory
        );
        clear_snapshot_open_operation_fault_for_test();

        for &operation in SnapshotOpenOperation::ALL {
            for primary in SnapshotInjectedPrimaryKind::ALL {
                let before =
                    snapshot_children(parent.path()).expect("inventory before operation fault");
                let pre_create_matches = baseline_trace[..child_created]
                    .iter()
                    .filter(|candidate| **candidate == operation)
                    .count();
                set_snapshot_open_operation_fault_after_matches_for_test(
                    operation,
                    primary,
                    pre_create_matches,
                );

                let result = Store::open_read_only_with_snapshot_parent(&database, &parent);
                let actual = match &result {
                    Ok(_) => "Ok",
                    Err(HeleosError::Io(_)) => "Io",
                    Err(HeleosError::Database) => "Database",
                    Err(HeleosError::PolicyDenied) => "PolicyDenied",
                    Err(_) => "Other",
                };

                assert!(
                    snapshot_open_operation_fault_fired_for_test(),
                    "operation fault did not fire: {operation:?} {primary:?}"
                );
                match primary {
                    SnapshotInjectedPrimaryKind::Io => assert!(
                        matches!(
                            result,
                            Err(HeleosError::Io(error)) if error.kind() == io::ErrorKind::Other
                        ),
                        "unexpected {actual} result for {operation:?} {primary:?}"
                    ),
                    SnapshotInjectedPrimaryKind::Database => {
                        assert!(
                            matches!(result, Err(HeleosError::Database)),
                            "unexpected {actual} result for {operation:?} {primary:?}"
                        )
                    }
                    SnapshotInjectedPrimaryKind::PolicyDenied => {
                        assert!(
                            matches!(result, Err(HeleosError::PolicyDenied)),
                            "unexpected {actual} result for {operation:?} {primary:?}"
                        )
                    }
                }
                assert_eq!(
                    snapshot_children(parent.path()).expect("inventory after operation fault"),
                    before,
                    "certainly-owned scratch leaked: {operation:?} {primary:?}"
                );
                clear_snapshot_open_operation_fault_for_test();
            }
        }
        drop(source);
    }

    #[test]
    fn sqlite_reader_configuration_has_independently_ordered_one_shot_sites() {
        let (_root, database) = migrated_reader_fixture("heleos-reader-config-sites-");
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain config parent");
        set_snapshot_open_operation_fault_for_test(
            SnapshotOpenOperation::Return,
            SnapshotInjectedPrimaryKind::PolicyDenied,
        );

        assert!(matches!(
            Store::open_read_only_with_snapshot_parent(&database, &parent),
            Err(HeleosError::PolicyDenied)
        ));
        let trace = snapshot_open_operation_trace_for_test();
        let expected = [
            SnapshotOpenOperation::RegisterJcsScalar,
            SnapshotOpenOperation::RegisterUuidScalar,
            SnapshotOpenOperation::RegisterValidTextScalar,
            SnapshotOpenOperation::BusyTimeout,
            SnapshotOpenOperation::DefensiveDbConfig,
            SnapshotOpenOperation::TrustedSchemaDbConfig,
            SnapshotOpenOperation::DqsDdlDbConfig,
            SnapshotOpenOperation::DqsDmlDbConfig,
            SnapshotOpenOperation::AttachCreateDbConfig,
            SnapshotOpenOperation::AttachWriteDbConfig,
            SnapshotOpenOperation::ForeignKeysPragma,
            SnapshotOpenOperation::TrustedSchemaPragma,
            SnapshotOpenOperation::SynchronousPragma,
            SnapshotOpenOperation::TempStorePragma,
            SnapshotOpenOperation::JournalModeRecoveryQuery,
            SnapshotOpenOperation::QueryOnlyPragma,
        ];
        assert!(
            trace
                .windows(expected.len())
                .any(|window| window == expected),
            "configuration trace was not exact: {trace:?}"
        );
        clear_snapshot_open_operation_fault_for_test();
    }

    #[test]
    fn database_wal_and_shm_artifact_operations_are_real_ordered_one_shot_sites() {
        let (_root, database) = migrated_reader_fixture("heleos-reader-artifact-sites-");
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain artifact parent");
        set_snapshot_open_operation_fault_for_test(
            SnapshotOpenOperation::Return,
            SnapshotInjectedPrimaryKind::PolicyDenied,
        );

        assert!(matches!(
            Store::open_read_only_with_snapshot_parent(&database, &parent),
            Err(HeleosError::PolicyDenied)
        ));
        let trace = snapshot_open_operation_trace_for_test();
        for expected in [
            &[
                SnapshotOpenOperation::DatabaseSourceClone,
                SnapshotOpenOperation::DatabaseSourceSeek,
                SnapshotOpenOperation::DatabaseCreate,
                SnapshotOpenOperation::DatabaseInitialMetadata,
                SnapshotOpenOperation::DatabaseType,
                SnapshotOpenOperation::DatabaseReparse,
                SnapshotOpenOperation::DatabaseZeroLength,
                SnapshotOpenOperation::DatabasePrivateApply,
                SnapshotOpenOperation::DatabasePrivateReadback,
                SnapshotOpenOperation::DatabaseSecondZeroLength,
                SnapshotOpenOperation::DatabaseMarker,
                SnapshotOpenOperation::DatabaseHandleClone,
                SnapshotOpenOperation::DatabaseCopyBytes,
                SnapshotOpenOperation::DatabaseFlush,
                SnapshotOpenOperation::DatabaseSync,
                SnapshotOpenOperation::DatabasePostWritePrivacy,
                SnapshotOpenOperation::DatabaseRelativeBinding,
                SnapshotOpenOperation::DatabaseAmbientBinding,
            ][..],
            &[
                SnapshotOpenOperation::WalCreate,
                SnapshotOpenOperation::WalInitialMetadata,
                SnapshotOpenOperation::WalType,
                SnapshotOpenOperation::WalReparse,
                SnapshotOpenOperation::WalZeroLength,
                SnapshotOpenOperation::WalPrivateApply,
                SnapshotOpenOperation::WalPrivateReadback,
                SnapshotOpenOperation::WalSecondZeroLength,
                SnapshotOpenOperation::WalMarker,
                SnapshotOpenOperation::WalFlush,
                SnapshotOpenOperation::WalSync,
                SnapshotOpenOperation::WalRelativeBinding,
                SnapshotOpenOperation::WalAmbientBinding,
            ][..],
            &[
                SnapshotOpenOperation::ShmCreate,
                SnapshotOpenOperation::ShmInitialMetadata,
                SnapshotOpenOperation::ShmType,
                SnapshotOpenOperation::ShmReparse,
                SnapshotOpenOperation::ShmZeroLength,
                SnapshotOpenOperation::ShmPrivateApply,
                SnapshotOpenOperation::ShmPrivateReadback,
                SnapshotOpenOperation::ShmSecondZeroLength,
                SnapshotOpenOperation::ShmMarker,
                SnapshotOpenOperation::ShmFlush,
                SnapshotOpenOperation::ShmSync,
                SnapshotOpenOperation::ShmRelativeBinding,
                SnapshotOpenOperation::ShmAmbientBinding,
            ][..],
        ] {
            let mut remaining = expected.iter();
            let mut next = remaining.next();
            for actual in &trace {
                if next == Some(&actual) {
                    next = remaining.next();
                }
            }
            assert!(
                next.is_none(),
                "artifact trace missing {expected:?}: {trace:?}"
            );
        }
        clear_snapshot_open_operation_fault_for_test();
    }

    #[test]
    fn copied_database_and_live_wal_stream_operations_are_independent_real_sites() {
        let (_root, database) = migrated_reader_fixture("heleos-reader-live-wal-sites-");
        let source = Connection::open(&database).expect("open raw live-WAL source");
        source
            .execute_batch(
                "PRAGMA journal_mode = WAL;
                 CREATE TABLE snapshot_wal_probe(value INTEGER NOT NULL);
                 INSERT INTO snapshot_wal_probe(value) VALUES (1);",
            )
            .expect("leave committed live WAL bytes");
        let wal_path = sidecar_path(&database, "-wal");
        let shm_path = sidecar_path(&database, "-shm");
        assert!(wal_path.is_file(), "fixture did not retain a live WAL");
        apply_private_permissions(&wal_path).expect("harden live WAL fixture");
        apply_private_permissions(&shm_path).expect("harden live SHM fixture");

        let parent = ReadOnlySnapshotParent::retain_default().expect("retain live-WAL parent");
        set_snapshot_open_operation_fault_for_test(
            SnapshotOpenOperation::Return,
            SnapshotInjectedPrimaryKind::PolicyDenied,
        );
        assert!(matches!(
            Store::open_read_only_with_snapshot_parent(&database, &parent),
            Err(HeleosError::PolicyDenied)
        ));
        let trace = snapshot_open_operation_trace_for_test();
        for expected in [
            &[
                SnapshotOpenOperation::DatabaseSourceRead,
                SnapshotOpenOperation::DatabaseByteAccounting,
                SnapshotOpenOperation::DatabaseDestinationWrite,
                SnapshotOpenOperation::DatabaseFinalByteCount,
            ][..],
            &[
                SnapshotOpenOperation::WalSourceClone,
                SnapshotOpenOperation::WalSourceSeek,
                SnapshotOpenOperation::WalSourceRead,
                SnapshotOpenOperation::WalByteAccounting,
                SnapshotOpenOperation::WalDestinationWrite,
                SnapshotOpenOperation::WalFinalByteCount,
                SnapshotOpenOperation::WalFlush,
                SnapshotOpenOperation::WalSync,
                SnapshotOpenOperation::WalPostWritePrivacy,
                SnapshotOpenOperation::WalRelativeBinding,
            ][..],
        ] {
            let mut remaining = expected.iter();
            let mut next = remaining.next();
            for actual in &trace {
                if next == Some(&actual) {
                    next = remaining.next();
                }
            }
            assert!(next.is_none(), "copy trace missing {expected:?}: {trace:?}");
        }
        clear_snapshot_open_operation_fault_for_test();
        drop(source);
    }

    #[test]
    fn every_source_recheck_is_an_independent_ordered_real_site_before_and_after_recovery() {
        let (_root, database) = migrated_reader_fixture("heleos-reader-source-rechecks-");
        let source = Connection::open(&database).expect("open source-recheck live WAL");
        source
            .execute_batch(
                "PRAGMA journal_mode = WAL;
                 CREATE TABLE snapshot_source_recheck_probe(value INTEGER NOT NULL);
                 INSERT INTO snapshot_source_recheck_probe(value) VALUES (1);",
            )
            .expect("leave source-recheck WAL bytes");
        apply_private_permissions(&sidecar_path(&database, "-wal"))
            .expect("harden source-recheck WAL");
        apply_private_permissions(&sidecar_path(&database, "-shm"))
            .expect("harden source-recheck SHM");
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain source parent");
        set_snapshot_open_operation_fault_for_test(
            SnapshotOpenOperation::Return,
            SnapshotInjectedPrimaryKind::PolicyDenied,
        );

        assert!(matches!(
            Store::open_read_only_with_snapshot_parent(&database, &parent),
            Err(HeleosError::PolicyDenied)
        ));
        let trace = snapshot_open_operation_trace_for_test();
        for expected in [
            &[
                SnapshotOpenOperation::SourcePreLengths,
                SnapshotOpenOperation::SourcePreDatabaseIdentity,
                SnapshotOpenOperation::SourcePreWalIdentity,
                SnapshotOpenOperation::SourcePreShmIdentity,
                SnapshotOpenOperation::SourcePreReaderLock,
                SnapshotOpenOperation::SourcePreParent,
                SnapshotOpenOperation::SourcePreSidecarPolicy,
                SnapshotOpenOperation::SourcePreOpenRechecks,
            ][..],
            &[
                SnapshotOpenOperation::ConfigureSourceDatabaseIdentity,
                SnapshotOpenOperation::ConfigureAndRecover,
                SnapshotOpenOperation::AfterRecoveryBarrier,
                SnapshotOpenOperation::SourcePostLengths,
                SnapshotOpenOperation::SourcePostDatabaseIdentity,
                SnapshotOpenOperation::SourcePostWalIdentity,
                SnapshotOpenOperation::SourcePostShmIdentity,
                SnapshotOpenOperation::SourcePostReaderLock,
                SnapshotOpenOperation::SourcePostParent,
                SnapshotOpenOperation::SourcePostSidecarPolicy,
                SnapshotOpenOperation::SourcePostRecoveryRechecks,
            ][..],
        ] {
            let mut remaining = expected.iter();
            let mut next = remaining.next();
            for actual in &trace {
                if next == Some(&actual) {
                    next = remaining.next();
                }
            }
            assert!(
                next.is_none(),
                "source trace missing {expected:?}: {trace:?}"
            );
        }
        clear_snapshot_open_operation_fault_for_test();
        drop(source);
    }

    #[test]
    fn recovered_privacy_layout_and_growth_operations_are_independent_real_sites() {
        let (_root, database) = migrated_reader_fixture("heleos-reader-recovered-sites-");
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain recovered parent");
        set_snapshot_open_operation_fault_for_test(
            SnapshotOpenOperation::Return,
            SnapshotInjectedPrimaryKind::PolicyDenied,
        );

        assert!(matches!(
            Store::open_read_only_with_snapshot_parent(&database, &parent),
            Err(HeleosError::PolicyDenied)
        ));
        let trace = snapshot_open_operation_trace_for_test();
        for expected in [
            &[
                SnapshotOpenOperation::RecoveredPrivacyIterator,
                SnapshotOpenOperation::RecoveredPrivacyEntryRead,
                SnapshotOpenOperation::RecoveredPrivacyAllowedName,
                SnapshotOpenOperation::RecoveredPrivacyOpen,
                SnapshotOpenOperation::RecoveredPrivacyMetadata,
                SnapshotOpenOperation::RecoveredPrivacyType,
                SnapshotOpenOperation::RecoveredPrivacyReparse,
                SnapshotOpenOperation::RecoveredPrivacyPolicy,
                SnapshotOpenOperation::RecoveredPrivacyMarker,
                SnapshotOpenOperation::RecoveredPrivacyFinalBinding,
                SnapshotOpenOperation::RecoveredPrivacy,
            ][..],
            &[
                SnapshotOpenOperation::RecoveredLayoutIterator,
                SnapshotOpenOperation::RecoveredLayoutEntryRead,
                SnapshotOpenOperation::RecoveredLayoutAllowedName,
                SnapshotOpenOperation::RecoveredLayoutOpen,
                SnapshotOpenOperation::RecoveredLayoutMetadata,
                SnapshotOpenOperation::RecoveredLayoutType,
                SnapshotOpenOperation::RecoveredLayoutReparse,
                SnapshotOpenOperation::RecoveredLayoutPrivacy,
                SnapshotOpenOperation::RecoveredLayoutGrowthAdd,
                SnapshotOpenOperation::RecoveredLayoutDecision,
                SnapshotOpenOperation::RecoveredLayoutFinalBinding,
                SnapshotOpenOperation::RecoveredLayoutGrowthAndCapacity,
            ][..],
        ] {
            let mut remaining = expected.iter();
            let mut next = remaining.next();
            for actual in &trace {
                if next == Some(&actual) {
                    next = remaining.next();
                }
            }
            assert!(
                next.is_none(),
                "recovered trace missing {expected:?}: {trace:?}"
            );
        }
        clear_snapshot_open_operation_fault_for_test();
    }

    #[cfg(unix)]
    #[test]
    fn retained_and_ambient_parent_rechecks_are_independent_ordered_real_sites() {
        let (_root, database) = migrated_reader_fixture("heleos-reader-parent-sites-");
        let (_snapshot_root, parent) = isolated_snapshot_parent("heleos-parent-sites-");
        set_snapshot_open_operation_fault_for_test(
            SnapshotOpenOperation::Return,
            SnapshotInjectedPrimaryKind::PolicyDenied,
        );

        assert!(matches!(
            Store::open_read_only_with_snapshot_parent(&database, &parent),
            Err(HeleosError::PolicyDenied)
        ));
        let trace = snapshot_open_operation_trace_for_test();
        for expected in [
            &[
                SnapshotOpenOperation::ParentRetainedMetadata,
                SnapshotOpenOperation::ParentRetainedType,
                SnapshotOpenOperation::ParentRetainedReparse,
                SnapshotOpenOperation::ParentRetainedMarker,
                SnapshotOpenOperation::UnixParentPolicyMetadata,
                SnapshotOpenOperation::UnixParentUidRead,
                SnapshotOpenOperation::UnixParentModeRead,
                SnapshotOpenOperation::UnixParentPredicate,
                SnapshotOpenOperation::UnixParentMarkerComparison,
                SnapshotOpenOperation::ParentRetainedPolicy,
            ][..],
            &[
                SnapshotOpenOperation::ParentAmbientMetadata,
                SnapshotOpenOperation::ParentAmbientType,
                SnapshotOpenOperation::ParentAmbientReparse,
                SnapshotOpenOperation::ParentAmbientMarker,
                SnapshotOpenOperation::ParentAmbientOpen,
                SnapshotOpenOperation::ParentAmbientRebound,
            ][..],
        ] {
            let mut remaining = expected.iter();
            let mut next = remaining.next();
            for actual in &trace {
                if next == Some(&actual) {
                    next = remaining.next();
                }
            }
            assert!(
                next.is_none(),
                "parent trace missing {expected:?}: {trace:?}"
            );
        }
        clear_snapshot_open_operation_fault_for_test();
    }

    #[test]
    fn child_handoff_operations_are_real_ordered_one_shot_sites() {
        let (_root, database) = migrated_reader_fixture("heleos-reader-child-sites-");
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain child-sites parent");
        set_snapshot_open_operation_fault_for_test(
            SnapshotOpenOperation::Return,
            SnapshotInjectedPrimaryKind::PolicyDenied,
        );

        assert!(matches!(
            Store::open_read_only_with_snapshot_parent(&database, &parent),
            Err(HeleosError::PolicyDenied)
        ));
        let trace = snapshot_open_operation_trace_for_test();
        for expected in [
            &[
                SnapshotOpenOperation::ProvisionalChildOpen,
                SnapshotOpenOperation::ProvisionalChildMetadata,
                SnapshotOpenOperation::ProvisionalChildType,
                SnapshotOpenOperation::ProvisionalChildReparse,
                SnapshotOpenOperation::ProvisionalChildMarker,
                SnapshotOpenOperation::ProvisionalChildCapabilityClone,
                SnapshotOpenOperation::ProvisionalChildIterator,
                SnapshotOpenOperation::ProvisionalChildFirstEntry,
                SnapshotOpenOperation::ProvisionalChildEmptyDecision,
            ][..],
            &[
                SnapshotOpenOperation::ChildRetainedOpen,
                SnapshotOpenOperation::ChildInitialMetadata,
                SnapshotOpenOperation::ChildType,
                SnapshotOpenOperation::ChildReparse,
                SnapshotOpenOperation::ChildMarkerCapture,
                SnapshotOpenOperation::ChildCapabilityClone,
                SnapshotOpenOperation::ChildOpen,
                SnapshotOpenOperation::ChildEmptyBeforeIterator,
                SnapshotOpenOperation::ChildEmptyBeforeFirstEntry,
                SnapshotOpenOperation::ChildEmptyBeforeDecision,
                SnapshotOpenOperation::ChildPrivateApply,
                SnapshotOpenOperation::ChildPrivateReadback,
                SnapshotOpenOperation::ChildEmptyAfterIterator,
                SnapshotOpenOperation::ChildEmptyAfterFirstEntry,
                SnapshotOpenOperation::ChildEmptyAfterDecision,
                SnapshotOpenOperation::ChildRelativeBinding,
            ][..],
        ] {
            let mut remaining = expected.iter();
            let mut next = remaining.next();
            for actual in &trace {
                if next == Some(&actual) {
                    next = remaining.next();
                }
            }
            assert!(
                next.is_none(),
                "child trace missing {expected:?}: {trace:?}"
            );
        }
        clear_snapshot_open_operation_fault_for_test();
    }

    #[cfg(unix)]
    #[test]
    fn retained_parent_rebind_is_policy_denied_without_deleting_the_decoy() {
        let outer = tempfile::Builder::new()
            .prefix("heleos-parent-rebind-outer-")
            .tempdir()
            .expect("create parent-rebind outer fixture");
        apply_private_permissions(outer.path()).expect("harden parent-rebind outer fixture");
        let parent_path = outer.path().join("selected-temp");
        fs::create_dir(&parent_path).expect("create selected temp parent");
        apply_private_permissions(&parent_path).expect("harden selected temp parent");
        let parent_path = fs::canonicalize(parent_path).expect("canonicalize selected temp parent");
        let moved = outer.path().join("retained-temp-parent");
        let parent = ReadOnlySnapshotParent::retain_path_for_test(&parent_path)
            .expect("retain selected temp parent");
        let scratch =
            ReadSnapshotDirectory::create(&parent).expect("create retained-parent scratch");
        fs::rename(&parent_path, &moved).expect("move retained temp parent");
        fs::create_dir(&parent_path).expect("install parent decoy");
        apply_private_permissions(&parent_path).expect("harden parent decoy");

        assert!(matches!(parent.recheck(), Err(HeleosError::PolicyDenied)));
        assert!(matches!(scratch.close(), Err(HeleosError::PolicyDenied)));
        assert!(
            parent_path.is_dir(),
            "checked close deleted the parent decoy"
        );
        assert!(
            snapshot_children(&moved)
                .expect("enumerate retained original parent")
                .is_empty(),
            "ambient parent drift prevented certain retained-parent cleanup"
        );

        fs::remove_dir_all(&parent_path).expect("remove parent decoy");
        fs::remove_dir_all(&moved).expect("remove retained parent tree");
    }

    #[cfg(unix)]
    #[test]
    fn retained_database_handle_and_endpoint_barrier_preserve_a_database_decoy() {
        let (_root, database) = migrated_reader_fixture("heleos-reader-database-rebind-");
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain default parent");
        let reader = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open database-rebind reader");
        let scratch = reader
            ._snapshot_directory
            .as_ref()
            .expect("reader owns scratch");
        let scratch_path = scratch.path().to_owned();
        let database_path = scratch.database_path().to_owned();
        let moved = scratch_path.join("retained-snapshot.sqlite3");
        fs::rename(&database_path, &moved).expect("move retained snapshot database");
        fs::write(&database_path, b"decoy").expect("install snapshot database decoy");
        apply_private_permissions(&database_path).expect("harden snapshot database decoy");
        assert!(
            scratch
                .database_file
                .as_ref()
                .expect("retained snapshot database handle")
                .metadata()
                .expect("retained database metadata")
                .is_file()
        );

        assert!(matches!(
            reader.close_read_only(),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(
            database_path.is_file(),
            "checked close deleted the database decoy"
        );

        fs::remove_dir_all(&scratch_path).expect("remove database-rebind scratch fixture");
    }

    #[cfg(windows)]
    #[test]
    fn retained_parent_child_and_database_handles_block_windows_rebinding_until_close() {
        let (_root, database) = migrated_reader_fixture("heleos-windows-snapshot-handles-");
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain default parent");
        let reader = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open Windows retained-handle reader");
        let scratch = reader
            ._snapshot_directory
            .as_ref()
            .expect("reader owns Windows scratch")
            .path()
            .to_owned();
        let scratch_database = scratch.join("snapshot.sqlite3");
        let moved_scratch = scratch.with_extension("moved");
        let moved_database = scratch.join("snapshot-moved.sqlite3");

        assert!(fs::rename(&scratch, &moved_scratch).is_err());
        assert!(fs::remove_dir_all(&scratch).is_err());
        assert!(fs::rename(&scratch_database, &moved_database).is_err());
        assert!(fs::remove_file(&scratch_database).is_err());

        reader
            .close_read_only()
            .expect("checked-close Windows retained-handle reader");
        assert!(!scratch.exists());
    }

    #[test]
    fn checked_close_drops_database_then_child_handles_before_removal() {
        let (_root, database) = migrated_reader_fixture("heleos-reader-close-order-");
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain default parent");
        let mut reader = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open close-order reader");
        let events = reader
            ._snapshot_directory
            .as_mut()
            .expect("reader owns scratch")
            .enable_cleanup_events_for_test();

        reader
            .close_read_only()
            .expect("checked-close ordered reader");

        assert_eq!(
            events.borrow().as_slice(),
            [
                SnapshotCleanupEvent::ConnectionClosed,
                SnapshotCleanupEvent::ScratchUsersReleased,
                SnapshotCleanupEvent::DatabaseHandleDropped,
                SnapshotCleanupEvent::ChildHandlesDropped,
                SnapshotCleanupEvent::AmbientParentChecked,
                SnapshotCleanupEvent::RelativeBindingsValidated,
                SnapshotCleanupEvent::ValidationHandlesDropped,
                SnapshotCleanupEvent::FinalRetainedCheckpoint,
                SnapshotCleanupEvent::EntryRemoved,
                SnapshotCleanupEvent::RelativeAbsenceVerified,
                SnapshotCleanupEvent::AmbientAbsenceResolved,
                SnapshotCleanupEvent::SourceHandlesAndReaderLockDropped,
            ]
        );
    }

    #[test]
    fn checked_cleanup_io_overrides_reader_success() {
        let (_root, database) = migrated_reader_fixture("heleos-reader-cleanup-error-");
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain default parent");
        let mut reader = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open cleanup-error reader");
        let scratch = reader
            ._snapshot_directory
            .as_mut()
            .expect("reader owns scratch");
        let scratch_path = scratch.path().to_owned();
        scratch.inject_cleanup_io_for_test();

        let result = reader.close_read_only();

        assert!(matches!(
            result,
            Err(HeleosError::Io(error)) if error.kind() == io::ErrorKind::Other
        ));
        assert!(scratch_path.is_dir());
        fs::remove_dir_all(scratch_path).expect("remove cleanup-error fixture");
    }

    #[test]
    fn checked_cleanup_remove_failure_latches_uncertain_and_drop_never_retries() {
        let (_root, database) = migrated_reader_fixture("heleos-reader-remove-error-");
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain default parent");
        let mut reader = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open remove-error reader");
        let scratch = reader
            ._snapshot_directory
            .as_mut()
            .expect("reader owns scratch");
        let scratch_path = scratch.path().to_owned();
        scratch.inject_cleanup_remove_io_for_test();

        let result = reader.close_read_only();

        assert!(matches!(
            result,
            Err(HeleosError::Io(error)) if error.kind() == io::ErrorKind::Other
        ));
        assert!(
            scratch_path.is_dir(),
            "Drop retried cleanup after removal authority became uncertain"
        );
        fs::remove_dir_all(scratch_path).expect("remove uncertain cleanup fixture");
    }

    #[test]
    fn cleanup_not_found_is_uncertain_before_remove_but_successful_after_remove() {
        let (_root, database) = migrated_reader_fixture("heleos-reader-remove-not-found-");
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain default parent");

        let reader = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open pre-remove NotFound reader");
        let scratch = reader
            ._snapshot_directory
            .as_ref()
            .expect("reader owns scratch")
            .path()
            .to_owned();
        arm_snapshot_cleanup_namespace_fault_for_test(
            SnapshotCleanupNamespaceFaultForTest::RemoveBeforeProductionRemove,
        );
        assert!(matches!(
            reader.close_read_only(),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(!scratch.exists());

        let reader = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open normal post-remove absence reader");
        let scratch = reader
            ._snapshot_directory
            .as_ref()
            .expect("reader owns scratch")
            .path()
            .to_owned();
        reader
            .close_read_only()
            .expect("post-remove NotFound proves absence");
        assert!(!scratch.exists());
    }

    #[test]
    fn post_remove_decoy_is_preserved_and_reports_cleanup_uncertainty() {
        let (_root, database) = migrated_reader_fixture("heleos-reader-post-remove-decoy-");
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain default parent");
        let reader = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open post-remove decoy reader");
        let scratch = reader
            ._snapshot_directory
            .as_ref()
            .expect("reader owns scratch")
            .path()
            .to_owned();
        arm_snapshot_cleanup_namespace_fault_for_test(
            SnapshotCleanupNamespaceFaultForTest::InstallDecoyAfterRemove,
        );

        assert!(matches!(
            reader.close_read_only(),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(
            scratch.is_dir(),
            "post-remove decoy was deleted by Drop retry"
        );
        fs::remove_dir_all(scratch).expect("remove preserved post-remove decoy");
    }

    #[test]
    fn drop_cleanup_failure_is_one_attempt_and_preserves_scratch() {
        let (_root, database) = migrated_reader_fixture("heleos-reader-drop-failure-");
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain default parent");
        let reader = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open Drop-failure reader");
        let scratch = reader
            ._snapshot_directory
            .as_ref()
            .expect("reader owns scratch")
            .path()
            .to_owned();
        arm_snapshot_cleanup_fault_for_test(SnapshotCleanupFaultForTest::Io);

        drop(reader);

        assert!(
            scratch.is_dir(),
            "Drop retried or acknowledged failed cleanup"
        );
        clear_snapshot_cleanup_fault_for_test();
        fs::remove_dir_all(scratch).expect("remove preserved Drop-failure scratch");
    }

    #[cfg(unix)]
    #[test]
    fn drop_handles_parent_drift_but_preserves_child_and_database_decoys() {
        let (_source, database) = migrated_reader_fixture("heleos-reader-drop-decoys-");

        let outer = tempfile::Builder::new()
            .prefix("heleos-drop-parent-decoy-")
            .tempdir()
            .expect("create Drop parent fixture");
        apply_private_permissions(outer.path()).expect("harden Drop parent fixture");
        let parent_path = outer.path().join("selected");
        let moved_parent = outer.path().join("retained-selected");
        fs::create_dir(&parent_path).expect("create selected Drop parent");
        apply_private_permissions(&parent_path).expect("harden selected Drop parent");
        let parent = ReadOnlySnapshotParent::retain_path_for_test(&parent_path)
            .expect("retain selected Drop parent");
        let reader = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open parent-decoy reader");
        fs::rename(&parent_path, &moved_parent).expect("move retained Drop parent");
        fs::create_dir(&parent_path).expect("install Drop parent decoy");
        apply_private_permissions(&parent_path).expect("harden Drop parent decoy");
        drop(reader);
        assert!(parent_path.is_dir());
        assert!(
            snapshot_children(&moved_parent)
                .expect("inventory retained parent after Drop")
                .is_empty()
        );
        fs::remove_dir_all(&parent_path).expect("remove Drop parent decoy");
        fs::remove_dir_all(&moved_parent).expect("remove retained Drop parent");

        let parent = ReadOnlySnapshotParent::retain_default().expect("retain child-decoy parent");
        let reader = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open child-decoy reader");
        let scratch = reader
            ._snapshot_directory
            .as_ref()
            .expect("reader owns scratch")
            .path()
            .to_owned();
        let moved_child = scratch.with_extension("drop-retained-child");
        fs::rename(&scratch, &moved_child).expect("move retained Drop child");
        fs::create_dir(&scratch).expect("install Drop child decoy");
        apply_private_permissions(&scratch).expect("harden Drop child decoy");
        drop(reader);
        assert!(scratch.is_dir());
        assert!(moved_child.is_dir());
        fs::remove_dir_all(&scratch).expect("remove Drop child decoy");
        fs::remove_dir_all(&moved_child).expect("remove retained Drop child");

        let reader = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open database-decoy reader");
        let scratch = reader
            ._snapshot_directory
            .as_ref()
            .expect("reader owns scratch")
            .path()
            .to_owned();
        let snapshot_database = scratch.join("snapshot.sqlite3");
        let moved_database = scratch.join("drop-retained.sqlite3");
        fs::rename(&snapshot_database, &moved_database).expect("move retained Drop database");
        fs::write(&snapshot_database, b"decoy").expect("install Drop database decoy");
        apply_private_permissions(&snapshot_database).expect("harden Drop database decoy");
        drop(reader);
        assert!(snapshot_database.is_file());
        assert!(moved_database.is_file());
        fs::remove_dir_all(scratch).expect("remove Drop database fixture");
    }

    #[test]
    fn checked_connection_close_database_primary_yields_to_cleanup_failure() {
        let (_root, database) = migrated_reader_fixture("heleos-reader-close-precedence-");
        let parent = ReadOnlySnapshotParent::retain_default().expect("retain default parent");

        let mut database_primary = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open close-primary reader");
        let database_scratch = database_primary
            ._snapshot_directory
            .as_ref()
            .expect("reader owns database-primary scratch")
            .path()
            .to_owned();
        database_primary.inject_connection_close_failure_for_test();
        assert!(matches!(
            database_primary.close_read_only(),
            Err(HeleosError::Database)
        ));
        assert!(!database_scratch.exists());

        let mut cleanup_override = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open cleanup-override reader");
        let override_scratch = cleanup_override
            ._snapshot_directory
            .as_ref()
            .expect("reader owns cleanup-override scratch")
            .path()
            .to_owned();
        cleanup_override.inject_connection_close_failure_for_test();
        cleanup_override
            ._snapshot_directory
            .as_mut()
            .expect("reader owns cleanup override")
            .inject_cleanup_io_for_test();
        assert!(matches!(
            cleanup_override.close_read_only(),
            Err(HeleosError::Io(error)) if error.kind() == io::ErrorKind::Other
        ));
        assert!(override_scratch.is_dir());
        fs::remove_dir_all(override_scratch).expect("remove cleanup-override fixture");
    }

    #[test]
    fn full_primary_by_cleanup_precedence_matrix_is_exact() {
        let (_root, database) = migrated_reader_fixture("heleos-reader-precedence-matrix-");
        let (_snapshot_root, parent) = isolated_snapshot_parent("heleos-precedence-matrix-parent-");

        for primary in SnapshotPrimaryOutcomeForTest::ALL {
            for cleanup in SnapshotCleanupOutcomeForTest::ALL {
                let before =
                    snapshot_children(parent.path()).expect("inventory before precedence case");
                if let Some(fault) = cleanup.fault() {
                    arm_snapshot_cleanup_fault_for_test(fault);
                }

                let result = if let Some(injected) = primary.injected() {
                    set_snapshot_open_operation_fault_for_test(
                        SnapshotOpenOperation::FinalBarrier,
                        injected,
                    );
                    Store::open_read_only_with_snapshot_parent(&database, &parent).map(|_| ())
                } else {
                    Store::open_read_only_with_snapshot_parent(&database, &parent)
                        .and_then(Store::close_read_only)
                };

                let expected = cleanup.error().or_else(|| primary.error());
                match expected {
                    None => assert!(result.is_ok(), "{primary:?} x {cleanup:?}"),
                    Some(SnapshotInjectedPrimaryKind::Io) => assert!(matches!(
                        result,
                        Err(HeleosError::Io(error)) if error.kind() == io::ErrorKind::Other
                    )),
                    Some(SnapshotInjectedPrimaryKind::Database) => {
                        assert!(matches!(result, Err(HeleosError::Database)))
                    }
                    Some(SnapshotInjectedPrimaryKind::PolicyDenied) => {
                        assert!(matches!(result, Err(HeleosError::PolicyDenied)))
                    }
                }

                let after =
                    snapshot_children(parent.path()).expect("inventory after precedence case");
                if cleanup == SnapshotCleanupOutcomeForTest::Success {
                    assert_eq!(after, before, "{primary:?} x {cleanup:?}");
                } else {
                    assert_eq!(after.len(), before.len() + 1, "{primary:?} x {cleanup:?}");
                    for name in after {
                        if !before.contains(&name) {
                            fs::remove_dir_all(parent.path().join(name))
                                .expect("remove deliberately preserved uncertain scratch");
                        }
                    }
                }
                clear_snapshot_open_operation_fault_for_test();
                clear_snapshot_cleanup_fault_for_test();
            }
        }
    }

    #[cfg(unix)]
    fn assert_snapshot_endpoint_fault_namespace_for_test(
        _outer: &Path,
        _parent_path: &Path,
        expected_axis: SnapshotEndpointAxis,
        expected_mutation: SnapshotEndpointMutation,
    ) {
        let fault = SNAPSHOT_ENDPOINT_FAULT.with(|fault| {
            fault
                .borrow()
                .as_ref()
                .cloned()
                .expect("endpoint fault remains recorded")
        });
        assert!(fault.applied);
        assert_eq!(fault.axis, expected_axis);
        assert_eq!(fault.mutation, expected_mutation);
        let original = fault.original.expect("record endpoint original");
        if expected_mutation == SnapshotEndpointMutation::PrivatePolicy {
            use std::os::unix::fs::PermissionsExt;

            assert!(fault.moved.is_none());
            let metadata = fs::symlink_metadata(&original).expect("private-policy endpoint exists");
            assert_ne!(metadata.permissions().mode() & 0o077, 0);
            match expected_axis {
                SnapshotEndpointAxis::Parent => assert_eq!(
                    snapshot_children(&original)
                        .expect("enumerate unsafe retained parent")
                        .len(),
                    1,
                    "policy-drifted parent scratch was incorrectly removed"
                ),
                SnapshotEndpointAxis::Child => assert!(original.is_dir()),
                SnapshotEndpointAxis::Database => assert!(original.is_file()),
            }
            apply_private_permissions(&original).expect("restore endpoint fixture privacy");
            return;
        }
        let moved = fault.moved.expect("record endpoint moved original");
        match expected_mutation {
            SnapshotEndpointMutation::Rebound => match expected_axis {
                SnapshotEndpointAxis::Parent => assert!(original.is_dir()),
                SnapshotEndpointAxis::Child => assert!(original.is_dir()),
                SnapshotEndpointAxis::Database => assert!(original.is_file()),
            },
            SnapshotEndpointMutation::Missing => assert!(
                matches!(fs::symlink_metadata(&original), Err(error) if error.kind() == io::ErrorKind::NotFound)
            ),
            SnapshotEndpointMutation::WrongType => match expected_axis {
                SnapshotEndpointAxis::Parent | SnapshotEndpointAxis::Child => {
                    assert!(original.is_file())
                }
                SnapshotEndpointAxis::Database => assert!(original.is_dir()),
            },
            SnapshotEndpointMutation::SymlinkLoop => assert!(
                fs::symlink_metadata(&original)
                    .expect("loop endpoint metadata")
                    .file_type()
                    .is_symlink()
            ),
            SnapshotEndpointMutation::PrivatePolicy => unreachable!(),
        }
        match expected_axis {
            SnapshotEndpointAxis::Parent => {
                assert!(moved.is_dir(), "retained original parent was removed");
                assert!(
                    snapshot_children(&moved)
                        .expect("enumerate retained endpoint parent")
                        .is_empty(),
                    "certain child under drifted ambient parent was not cleaned"
                );
            }
            SnapshotEndpointAxis::Child => {
                assert!(moved.is_dir(), "moved retained child was removed");
            }
            SnapshotEndpointAxis::Database => {
                assert!(moved.is_file(), "moved retained database was removed");
            }
        }
    }

    #[cfg(unix)]
    fn cleanup_snapshot_endpoint_fault_namespace_for_test(
        _: &Path,
        _: &Path,
        _: SnapshotEndpointAxis,
    ) {
        // The enclosing TempDir owns every deliberately preserved decoy. Keeping cleanup out of
        // production is part of the test: only fixture teardown removes these preserved entries.
    }

    #[cfg(windows)]
    fn assert_snapshot_endpoint_marker_fault_for_test(
        expected_barrier: SnapshotEndpointBarrier,
        expected_axis: SnapshotEndpointAxis,
    ) {
        let fault = SNAPSHOT_ENDPOINT_FAULT.with(|fault| {
            fault
                .borrow()
                .as_ref()
                .cloned()
                .expect("Windows endpoint fault remains recorded")
        });
        assert!(fault.applied);
        assert_eq!(fault.barrier, expected_barrier);
        assert_eq!(fault.axis, expected_axis);
        assert_eq!(fault.mutation, SnapshotEndpointMutation::MarkerMismatch);
        assert!(fault.original.is_some());
        assert!(fault.moved.is_none());
    }

    #[cfg(windows)]
    fn windows_parent_ace(trustee_sid: &str, flags: u8, mask: u32) -> SnapshotWindowsAceMarker {
        SnapshotWindowsAceMarker::Allow {
            flags,
            mask,
            trustee_sid: trustee_sid.to_owned(),
        }
    }

    #[test]
    fn windows_parent_safe_dacl_parser_accepts_only_the_committed_flat_ad_grammar() {
        let parsed = parse_snapshot_windows_dacl_sddl(
            "D:PAIAR(A;CIOINPIOID;0x7800003F;;;S-1-1-0)(D;;FA;;;S-1-5-32-545)",
        )
        .expect("parse committed A/D-only DACL");
        assert_eq!(
            parsed,
            [
                ParsedSafeAce {
                    kind: ParsedSafeAceKind::Allow,
                    flags: 0x1f,
                },
                ParsedSafeAce {
                    kind: ParsedSafeAceKind::Deny,
                    flags: 0,
                },
            ]
        );
        assert_eq!(
            parse_snapshot_windows_dacl_sddl("D:(A;;0x7800003F;;;S-1-1-0)")
                .expect("accept exact lowercase-rights fixture"),
            [ParsedSafeAce {
                kind: ParsedSafeAceKind::Allow,
                flags: 0,
            }]
        );
        assert!(parse_snapshot_windows_dacl_sddl("D:").is_ok());

        for rejected in [
            "",
            "D",
            "O:S-1-1-0D:",
            "D:P P",
            "D:PP",
            "D:ARA R",
            "D:AIAI",
            "D:X",
            "D:(a;;;;;S-1-1-0)",
            "D:(XA;;;;;S-1-1-0)",
            "D:(XD;;;;;S-1-1-0)",
            "D:(OA;;;;;S-1-1-0)",
            "D:(OD;;;;;S-1-1-0)",
            "D:(OU;;;;;S-1-1-0)",
            "D:(ZA;;;;;S-1-1-0)",
            "D:(AU;;;;;S-1-1-0)",
            "D:(A;ci;;;;S-1-1-0)",
            "D:(A;CICI;;;;S-1-1-0)",
            "D:(A;SA;;;;S-1-1-0)",
            "D:(A;;FA;GUID;;S-1-1-0)",
            "D:(A;;FA;;GUID;S-1-1-0)",
            "D:(A;;F_;;;;S-1-1-0)",
            "D:(A;;FA;;;s-1-1-0)",
            "D:(A;;FA;;;S-1-1-0;extra)",
            "D:(A;;FA;;S-1-1-0)",
            "D:(A;;FA;;;S-1-1-0)trailing",
            "D:((A;;FA;;;S-1-1-0))",
            "D:(A;;FA;;;S-1-(1)-0)",
            "D:(A;;FA;;;S-1-1-0",
            "D:(A;;FA;;;S-1-1-é)",
        ] {
            assert!(
                matches!(
                    parse_snapshot_windows_dacl_sddl(rejected),
                    Err(HeleosError::PolicyDenied)
                ),
                "accepted rejected DACL: {rejected:?}"
            );
        }
    }

    #[test]
    fn windows_parent_safe_dacl_parser_enforces_length_and_record_bounds() {
        assert!(matches!(
            parse_snapshot_windows_dacl_sddl("D"),
            Err(HeleosError::PolicyDenied)
        ));
        let oversized = format!("D:{}", "P".repeat(1_048_575));
        assert_eq!(oversized.len(), 1_048_577);
        assert!(matches!(
            parse_snapshot_windows_dacl_sddl(&oversized),
            Err(HeleosError::PolicyDenied)
        ));

        let maximum = format!("D:{}", "(D;;;;;S)".repeat(65_535));
        assert_eq!(
            parse_snapshot_windows_dacl_sddl(&maximum)
                .expect("accept exact ACE-count maximum")
                .len(),
            65_535
        );
        let too_many = format!("{maximum}(D;;;;;S)");
        assert!(matches!(
            parse_snapshot_windows_dacl_sddl(&too_many),
            Err(HeleosError::PolicyDenied)
        ));
    }

    #[test]
    fn windows_parent_snapshot_source_uses_one_descriptor_and_no_acl_convenience_path() {
        let source = include_str!("mod.rs");
        let implementation_start = source
            .find("enum ParsedSafeAceKind")
            .expect("Windows safe-ACE implementation start");
        let implementation_end = source[implementation_start..]
            .find("#[cfg(not(any(unix, windows)))]")
            .map(|offset| implementation_start + offset)
            .expect("Windows retained-parent implementation end");
        let implementation = &source[implementation_start..implementation_end];
        let marker_start = implementation
            .find("fn snapshot_windows_parent_marker")
            .expect("Windows retained-parent marker function");
        let marker_end = implementation[marker_start..]
            .find("fn capture_snapshot_parent_policy")
            .map(|offset| marker_start + offset)
            .expect("Windows retained-parent capture function");
        let marker = &implementation[marker_start..marker_end];

        assert_eq!(marker.matches("GetSecurityInfo(").count(), 1);
        let ordered = [
            "let descriptor = GetSecurityInfo(",
            "GetSecurityDescriptorOwner(descriptor.as_ref())",
            "ConvertSidToStringSid(borrowed_owner)",
            "ConvertSecurityDescriptorToStringSecurityDescriptor(",
            "parse_snapshot_windows_dacl_sddl(&dacl_sddl)",
            "GetSecurityDescriptorDacl(descriptor.as_ref())",
            "IsValidAcl(acl)",
            "acl.revision_level() == AclRevision::ACL_REVISION",
            "GetAclInformationSize(acl)",
            "let ace = GetAce(acl, index)",
            "let ace_type = ace.ace_type()",
            "let flags = ace.flags().bits()",
            "let mask = ace.mask().bits()",
            "let borrowed_sid = ace.sid()",
        ];
        let mut cursor = 0_usize;
        for token in ordered {
            let offset = marker[cursor..]
                .find(token)
                .unwrap_or_else(|| panic!("missing/out-of-order Windows token {token}"));
            cursor += offset + token.len();
        }
        let deny_start = marker
            .find("ParsedSafeAceKind::Deny =>")
            .expect("deny branch exists");
        let allow_start = marker[deny_start..]
            .find("ParsedSafeAceKind::Allow =>")
            .map(|offset| deny_start + offset)
            .expect("allow branch follows deny");
        let deny_branch = &marker[deny_start..allow_start];
        for forbidden in ["ace.flags()", "ace.mask()", "ace.sid()"] {
            assert!(
                !deny_branch.contains(forbidden),
                "Deny accessed non-type ACE evidence: {forbidden}"
            );
        }
        assert!(!marker.contains("ACL_REVISION_DS"));
        for forbidden in [
            "snapshot_windows_owner_dacl",
            "snapshot_windows_aces",
            "windows_acl::",
            "ACL::from_file_handle",
            ".all()",
            ".dacl()",
            ".get_ace(",
        ] {
            assert!(
                !implementation.contains(forbidden),
                "retained-parent implementation contains forbidden convenience path {forbidden}"
            );
        }

        for operation in [
            "WindowsGetSecurityInfo",
            "WindowsOwnerLookup",
            "WindowsOwnerSidConversion",
            "WindowsOwnerUnicode",
            "WindowsOwnerCanonicalization",
            "WindowsDaclSddlConversion",
            "WindowsDaclUnicode",
            "WindowsDaclBoundsAndParser",
            "WindowsDaclExtraction",
            "WindowsAclValidity",
            "WindowsAclRevision",
            "WindowsAclSizeAndCount",
            "WindowsAccessRightsMask",
            "WindowsIndexedGetAce",
            "WindowsAceType",
            "WindowsAllowFlags",
            "WindowsAllowMask",
            "WindowsAllowSidConversion",
            "WindowsAllowSidUnicode",
            "WindowsAllowSidCanonicalization",
            "WindowsParentEffectivePredicate",
            "WindowsInheritedChildPredicate",
            "WindowsMarkerComparison",
        ] {
            let token = format!("SnapshotOpenOperation::{operation}");
            assert!(
                implementation.contains(&token),
                "missing Windows retained-parent one-shot site {token}"
            );
        }
    }

    #[test]
    fn task_three_windows_governance_is_frozen_and_task_seven_is_append_only() {
        use sha2::{Digest, Sha256};

        fn record<'a>(registry: &'a str, name: &str) -> &'a str {
            let needle = format!("[[tool]]\nname = \"{name}\"");
            let start = registry
                .find(&needle)
                .unwrap_or_else(|| panic!("missing governance record {name}"));
            let rest = &registry[start..];
            let end = rest.find("\n[[tool]]").unwrap_or(rest.len());
            &rest[..end]
        }

        fn sha256(value: &str) -> String {
            Sha256::digest(value.as_bytes())
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect()
        }

        const GOVERNANCE: &str = include_str!("../../../../governance/tools.toml");
        for (name, expected) in [
            (
                "windows-acl",
                "09f4c58bc04a5de90e5ff29683a1292ce1396348fdf87b659f5ddd2f16aa8c68",
            ),
            (
                "windows-permissions",
                "682b511297fc3efd7f1f8072cccc1404fb4f225b42bbacc0437b97c3b2ac3ec2",
            ),
            (
                "stellar-agent-windows-identity",
                "56c6667348b7316815843fc682cab4901953147a9d76a0b6b5365159f33f2898",
            ),
        ] {
            assert_eq!(sha256(record(GOVERNANCE, name)), expected, "{name} drifted");
        }

        let append_names = [
            "windows-acl Task 7 child/file DACL extension",
            "windows-permissions Task 7 retained-parent/child extension",
            "stellar-agent-windows-identity Task 7 child/retained-parent SID extension",
        ];
        let append_records = append_names.map(|name| record(GOVERNANCE, name));
        for (name, append) in append_names.into_iter().zip(append_records.iter().copied()) {
            assert!(append.contains("Task 10"), "{name} lost its native owner");
            assert!(
                append.contains("rollback = \"revert this Task 7 append authority"),
                "{name} lost its Task 7 rollback"
            );
        }
        assert!(
            append_records[0] != append_records[1]
                && append_records[0] != append_records[2]
                && append_records[1] != append_records[2],
            "Task 7 Windows append authorities must remain distinct"
        );
    }

    #[cfg(windows)]
    #[test]
    fn windows_snapshot_parent_masks_cover_all_allow_variants_flags_and_trustees() {
        const PROCESS: &str = "S-1-5-21-1-2-3-1000";
        const EVERYONE: &str = "S-1-1-0";
        const USERS: &str = "S-1-5-32-545";
        const CREATOR_OWNER: &str = "S-1-3-0";
        const CREATOR_GROUP: &str = "S-1-3-1";
        const IO: u8 = 0x08;
        const CI: u8 = 0x02;
        const OI: u8 = 0x01;
        const NP: u8 = 0x04;
        const PARENT_BITS: [u32; 5] = [0x40, 0x1_0000, 0x4_0000, 0x8_0000, 0x1000_0000];
        const CHILD_BITS: [u32; 12] = [
            0x4000_0000,
            0x0012_0116,
            0x2,
            0x4,
            0x10,
            0x40,
            0x100,
            0x1_0000,
            0x4_0000,
            0x8_0000,
            0x1000_0000,
            0x500d_0156,
        ];
        for trustee in [EVERYONE, USERS, CREATOR_GROUP] {
            for mask in PARENT_BITS {
                assert!(!snapshot_windows_parent_policy_accepts(
                    PROCESS,
                    PROCESS,
                    &[windows_parent_ace(trustee, 0, mask)]
                ));
            }
            for flags in [CI, OI, CI | IO, CI | IO | NP, OI | IO, OI | NP] {
                for mask in CHILD_BITS {
                    assert!(!snapshot_windows_parent_policy_accepts(
                        PROCESS,
                        PROCESS,
                        &[windows_parent_ace(trustee, flags, mask)]
                    ));
                }
            }
        }
        assert!(snapshot_windows_parent_policy_accepts(
            PROCESS,
            PROCESS,
            &[windows_parent_ace(EVERYONE, IO, 0x500d_0156)]
        ));
        assert!(snapshot_windows_parent_policy_accepts(
            PROCESS,
            PROCESS,
            &[windows_parent_ace(CREATOR_OWNER, IO | CI, 0x500d_0156)]
        ));
        assert!(!snapshot_windows_parent_policy_accepts(
            PROCESS,
            PROCESS,
            &[windows_parent_ace(CREATOR_OWNER, CI, 0x2)]
        ));
        assert!(snapshot_windows_parent_policy_accepts(
            PROCESS,
            PROCESS,
            &[windows_parent_ace(CREATOR_OWNER, IO, 0x40)]
        ));
        assert!(!snapshot_windows_parent_policy_accepts(
            PROCESS,
            PROCESS,
            &[windows_parent_ace(CREATOR_OWNER, 0, 0x40)]
        ));
        for trusted in [PROCESS, "S-1-5-18", "S-1-5-32-544"] {
            assert!(snapshot_windows_parent_policy_accepts(
                PROCESS,
                PROCESS,
                &[windows_parent_ace(trusted, CI | OI, u32::MAX)]
            ));
        }
        assert!(snapshot_windows_parent_policy_accepts(
            PROCESS,
            PROCESS,
            &[SnapshotWindowsAceMarker::Deny]
        ));
        assert!(!snapshot_windows_parent_policy_accepts(
            EVERYONE,
            PROCESS,
            &[]
        ));
        assert!(snapshot_windows_parent_policy_accepts(
            "S-1-5-18",
            PROCESS,
            &[]
        ));
    }

    #[cfg(windows)]
    #[test]
    fn windows_snapshot_parent_open_is_read_controlled_no_delete_share_and_never_write_dac() {
        assert_eq!(
            retained_directory_windows_open_masks(PermissionPolicy::VerifyOnly),
            (0x8002_0080, 0x3, 0x0220_0000)
        );
        assert_eq!(
            retained_directory_windows_open_masks(PermissionPolicy::ApplyAndVerify),
            (0x8006_0080, 0x3, 0x0220_0000)
        );
    }

    #[cfg(windows)]
    #[test]
    fn windows_default_snapshot_parent_is_conditional_and_never_namespace_mutated() {
        let selected = fs::canonicalize(tempfile::env::temp_dir())
            .expect("canonicalize native Windows temp parent");
        let before_marker = FileMarker::from_metadata(
            &fs::symlink_metadata(&selected).expect("native temp metadata before"),
        );
        let before_children = snapshot_children(&selected).expect("native temp inventory before");

        match ReadOnlySnapshotParent::retain_default() {
            Ok(parent) => {
                assert_eq!(parent.path(), selected);
                let scratch = ReadSnapshotDirectory::create(&parent)
                    .expect("admitted native temp parent creates scratch");
                scratch.close().expect("close native temp scratch");
            }
            Err(HeleosError::PolicyDenied) => {
                // A real unsupported/non-revision-2 parent is required to fail before creation.
            }
            Err(other) => panic!("unexpected native temp parent result: {other:?}"),
        }

        assert_eq!(
            FileMarker::from_metadata(
                &fs::symlink_metadata(&selected).expect("native temp metadata after")
            ),
            before_marker
        );
        assert_eq!(
            snapshot_children(&selected).expect("native temp inventory after"),
            before_children
        );
    }

    #[cfg(windows)]
    #[test]
    fn windows_live_snapshot_handles_block_parent_child_and_database_replacement() {
        let (_root, database) = migrated_reader_fixture("heleos-windows-handle-lifetime-");
        let (_snapshot_root, parent) = isolated_snapshot_parent("heleos-windows-handle-parent-");
        let reader = Store::open_read_only_with_snapshot_parent(&database, &parent)
            .expect("open native Windows reader");
        let scratch = reader
            ._snapshot_directory
            .as_ref()
            .expect("reader owns native Windows scratch")
            .path()
            .to_owned();
        let snapshot_database = scratch.join("snapshot.sqlite3");

        assert!(
            fs::rename(&snapshot_database, scratch.join("database-decoy-target")).is_err(),
            "retained database handle allowed replacement"
        );
        assert!(
            fs::rename(&scratch, scratch.with_extension("child-decoy-target")).is_err(),
            "retained child handle allowed replacement"
        );
        assert!(
            fs::rename(
                parent.path(),
                parent.path().with_extension("parent-decoy-target")
            )
            .is_err(),
            "retained parent handle allowed replacement"
        );

        reader
            .close_read_only()
            .expect("checked-close native reader");
        assert!(!scratch.exists());
    }

    fn snapshot_children(parent: &Path) -> io::Result<Vec<OsString>> {
        let mut names = fs::read_dir(parent)?
            .filter_map(|entry| entry.ok())
            .map(|entry| entry.file_name())
            .filter(|name| name.to_string_lossy().starts_with("heleos-read-snapshot-"))
            .collect::<Vec<_>>();
        names.sort();
        Ok(names)
    }
}
