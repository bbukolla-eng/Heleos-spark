use std::ffi::{OsStr, OsString};
use std::fs::{File, OpenOptions};
use std::path::{Component, Path, PathBuf};

use cap_fs_ext::{FollowSymlinks, OpenOptionsFollowExt, OpenOptionsMaybeDirExt};
use cap_std::fs::{Dir, File as CapFile, OpenOptions as CapOpenOptions};

use crate::store::{apply_private_permissions_to_handle, verify_private_permissions_on_handle};
use crate::{HeleosError, Result};

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub(super) struct FileMarker {
    first: u64,
    second: u64,
}

impl FileMarker {
    #[cfg(all(test, windows))]
    pub(super) const fn mismatched_for_test(self) -> Self {
        Self {
            first: self.first.wrapping_add(1),
            second: self.second,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) enum HandleKind {
    DirectoryApply,
    LockApply,
    StagingApply,
    FinalVerify,
}

pub(super) struct OpenedDir {
    pub(super) dir: Dir,
    pub(super) marker: FileMarker,
}

pub(super) struct OpenedFile {
    pub(super) file: CapFile,
    pub(super) marker: FileMarker,
}

pub(super) enum StagingCreateError {
    NotCreated(HeleosError),
    Created(HeleosError),
}

pub(super) enum ClassifiedEntry {
    Regular(CapFile),
    Directory,
    NonRegular,
    PermissionViolation { byte_length: Option<u64> },
}

impl StagingCreateError {
    #[cfg(test)]
    pub(super) fn into_error(self) -> HeleosError {
        match self {
            Self::NotCreated(error) | Self::Created(error) => error,
        }
    }
}

pub(super) struct TrustAnchor {
    pub(super) configured_root: PathBuf,
    pub(super) root_name: OsString,
    pub(super) parent: Dir,
    pub(super) parent_marker: FileMarker,
}

pub(super) fn open_trust_anchor(configured_root: &Path) -> Result<TrustAnchor> {
    validate_configured_root(configured_root)?;
    let parent_path = configured_root.parent().ok_or(HeleosError::PolicyDenied)?;
    let root_name = configured_root
        .file_name()
        .ok_or(HeleosError::PolicyDenied)?
        .to_os_string();
    let parent_file = open_ambient_directory(parent_path, false)?;
    verify_private_permissions_on_handle(&parent_file)?;
    let parent_marker = marker_from_file(&parent_file)?;
    recheck_ambient_directory(parent_path, &parent_file, parent_marker)?;
    Ok(TrustAnchor {
        configured_root: configured_root.to_owned(),
        root_name,
        parent: Dir::from_std_file(parent_file),
        parent_marker,
    })
}

pub(super) fn validate_configured_root(path: &Path) -> Result<()> {
    if !path.is_absolute() || path.parent().is_none() || contains_raw_dot_component(path) {
        return Err(HeleosError::PolicyDenied);
    }
    let mut saw_root = false;
    let mut normal_count = 0_usize;
    for component in path.components() {
        match component {
            Component::Prefix(_) => {}
            Component::RootDir => saw_root = true,
            Component::Normal(_) => {
                normal_count = normal_count
                    .checked_add(1)
                    .ok_or(HeleosError::ResourceLimit)?
            }
            Component::CurDir | Component::ParentDir => return Err(HeleosError::PolicyDenied),
        }
    }
    if !saw_root
        || normal_count == 0
        || !matches!(path.components().next_back(), Some(Component::Normal(_)))
    {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

#[cfg(unix)]
fn contains_raw_dot_component(path: &Path) -> bool {
    use std::os::unix::ffi::OsStrExt;

    path.as_os_str()
        .as_bytes()
        .split(|byte| *byte == b'/')
        .any(|component| component == b"." || component == b"..")
}

#[cfg(windows)]
fn contains_raw_dot_component(path: &Path) -> bool {
    use std::os::windows::ffi::OsStrExt;

    let units: Vec<_> = path.as_os_str().encode_wide().collect();
    units
        .split(|unit| *unit == u16::from(b'/') || *unit == u16::from(b'\\'))
        .any(|component| component == [u16::from(b'.')] || component == [u16::from(b'.'); 2])
}

#[cfg(not(any(unix, windows)))]
fn contains_raw_dot_component(path: &Path) -> bool {
    path.to_string_lossy()
        .split('/')
        .any(|component| component == "." || component == "..")
}

pub(super) fn create_directory(parent: &Dir, name: &OsStr) -> Result<OpenedDir> {
    ensure_bare_component(name)?;
    parent.create_dir(name).map_err(HeleosError::Io)?;
    open_directory(parent, name, true)
}

pub(super) fn open_directory(parent: &Dir, name: &OsStr, apply: bool) -> Result<OpenedDir> {
    ensure_bare_component(name)?;
    let options = cap_options(HandleKind::DirectoryApply, false, true, true);
    let cap_file = parent
        .open_with(name, &options)
        .map_err(map_cap_open_error)?;
    let mut file = cap_file.into_std();
    let metadata = file.metadata().map_err(HeleosError::Io)?;
    if !metadata.is_dir() {
        return Err(HeleosError::PolicyDenied);
    }
    if apply {
        apply_private_permissions_to_handle(&mut file)?;
    } else {
        verify_private_permissions_on_handle(&file)?;
    }
    let marker = marker_from_file(&file)?;
    // Keep the exact checked handle; capability operations never reopen the directory by path.
    Ok(OpenedDir {
        dir: Dir::from_std_file(file),
        marker,
    })
}

pub(super) fn open_or_create_directory(parent: &Dir, name: &OsStr) -> Result<OpenedDir> {
    match create_directory(parent, name) {
        Ok(opened) => Ok(opened),
        Err(HeleosError::Io(error)) if error.kind() == std::io::ErrorKind::AlreadyExists => {
            open_directory(parent, name, true)
        }
        Err(error) => Err(error),
    }
}

pub(super) fn open_or_create_file(
    parent: &Dir,
    name: &OsStr,
    kind: HandleKind,
) -> Result<OpenedFile> {
    ensure_bare_component(name)?;
    match open_file(parent, name, kind, true, true) {
        Ok(opened) => Ok(opened),
        Err(HeleosError::Io(error)) if error.kind() == std::io::ErrorKind::AlreadyExists => {
            open_file(parent, name, kind, false, true)
        }
        Err(error) => Err(error),
    }
}

pub(super) fn create_staging_file(
    parent: &Dir,
    name: &OsStr,
) -> std::result::Result<OpenedFile, StagingCreateError> {
    ensure_bare_component(name).map_err(StagingCreateError::NotCreated)?;
    let options = cap_options(HandleKind::StagingApply, true, false, false);
    let cap_file = parent
        .open_with(name, &options)
        .map_err(map_cap_open_error)
        .map_err(StagingCreateError::NotCreated)?;
    finish_open_file(cap_file, true).map_err(StagingCreateError::Created)
}

pub(super) fn open_final_file(parent: &Dir, name: &OsStr) -> Result<OpenedFile> {
    ensure_bare_component(name)?;
    open_file(parent, name, HandleKind::FinalVerify, false, false)
}

pub(super) fn open_unverified_entry(parent: &Dir, name: &OsStr) -> Result<CapFile> {
    ensure_bare_component(name)?;
    let options = cap_options(HandleKind::FinalVerify, false, true, false);
    parent.open_with(name, &options).map_err(map_cap_open_error)
}

pub(super) fn classify_and_open_entry(parent: &Dir, name: &OsStr) -> Result<ClassifiedEntry> {
    ensure_bare_component(name)?;
    let metadata = match parent.symlink_metadata(name) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::PermissionDenied => {
            return Ok(ClassifiedEntry::PermissionViolation { byte_length: None });
        }
        Err(error) => return Err(HeleosError::Io(error)),
    };
    if metadata.file_type().is_symlink() || cap_metadata_is_windows_reparse(&metadata) {
        return Ok(ClassifiedEntry::NonRegular);
    }
    let is_file = metadata.is_file();
    let is_directory = metadata.is_dir();
    if !is_file && !is_directory {
        return Ok(ClassifiedEntry::NonRegular);
    }
    let byte_length = is_file.then_some(metadata.len());

    #[cfg(unix)]
    if !cap_unix_metadata_is_private(&metadata) {
        return Ok(ClassifiedEntry::PermissionViolation { byte_length });
    }

    #[cfg(windows)]
    let _permission_handle = match open_windows_classification_handle(parent, name) {
        Ok(file) => match verify_private_permissions_on_handle(&file) {
            Ok(()) => file,
            Err(HeleosError::PolicyDenied) => {
                return Ok(ClassifiedEntry::PermissionViolation { byte_length });
            }
            Err(error) => return Err(error),
        },
        Err(HeleosError::Io(error)) if error.kind() == std::io::ErrorKind::PermissionDenied => {
            return Ok(ClassifiedEntry::PermissionViolation { byte_length });
        }
        Err(HeleosError::PolicyDenied) => return Ok(ClassifiedEntry::NonRegular),
        Err(error) => return Err(error),
    };

    if is_directory {
        return Ok(ClassifiedEntry::Directory);
    }
    match open_unverified_entry(parent, name) {
        Ok(file) => {
            let opened_metadata = file.metadata().map_err(HeleosError::Io)?;
            if !opened_metadata.is_file() || cap_metadata_is_windows_reparse(&opened_metadata) {
                return Ok(ClassifiedEntry::NonRegular);
            }
            match verify_private_permissions_on_handle(
                &file.try_clone().map_err(HeleosError::Io)?.into_std(),
            ) {
                Ok(()) => Ok(ClassifiedEntry::Regular(file)),
                Err(HeleosError::PolicyDenied) => {
                    Ok(ClassifiedEntry::PermissionViolation { byte_length })
                }
                Err(error) => Err(error),
            }
        }
        Err(HeleosError::Io(error)) if error.kind() == std::io::ErrorKind::PermissionDenied => {
            Ok(ClassifiedEntry::PermissionViolation { byte_length })
        }
        Err(HeleosError::PolicyDenied) => Ok(ClassifiedEntry::NonRegular),
        Err(error) => Err(error),
    }
}

#[cfg(unix)]
fn cap_unix_metadata_is_private(metadata: &cap_std::fs::Metadata) -> bool {
    use cap_std::fs::MetadataExt;

    let expected = if metadata.is_dir() { 0o700 } else { 0o600 };
    metadata.mode() & 0o777 == expected
}

#[cfg(windows)]
fn open_windows_classification_handle(parent: &Dir, name: &OsStr) -> Result<File> {
    use cap_std::fs::OpenOptionsExt;

    const READ_CONTROL: u32 = 0x0002_0000;
    const FILE_READ_ATTRIBUTES: u32 = 0x0000_0080;
    const FILE_SHARE_READ: u32 = 0x0000_0001;
    const FILE_SHARE_WRITE: u32 = 0x0000_0002;
    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;

    let mut options = CapOpenOptions::new();
    options
        .follow(FollowSymlinks::No)
        .maybe_dir(true)
        .access_mode(READ_CONTROL | FILE_READ_ATTRIBUTES)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS);
    parent
        .open_with(name, &options)
        .map(CapFile::into_std)
        .map_err(map_cap_open_error)
}

#[cfg(windows)]
fn cap_metadata_is_windows_reparse(metadata: &cap_std::fs::Metadata) -> bool {
    use cap_std::fs::MetadataExt;

    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;
    metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
}

#[cfg(not(windows))]
const fn cap_metadata_is_windows_reparse(_: &cap_std::fs::Metadata) -> bool {
    false
}

fn open_file(
    parent: &Dir,
    name: &OsStr,
    kind: HandleKind,
    create_new: bool,
    apply: bool,
) -> Result<OpenedFile> {
    let options = cap_options(kind, create_new, false, false);
    let cap_file = parent
        .open_with(name, &options)
        .map_err(map_cap_open_error)?;
    finish_open_file(cap_file, apply)
}

fn finish_open_file(cap_file: CapFile, apply: bool) -> Result<OpenedFile> {
    let mut std_file = cap_file.into_std();
    let metadata = std_file.metadata().map_err(HeleosError::Io)?;
    if !metadata.is_file() {
        return Err(HeleosError::PolicyDenied);
    }
    if apply {
        apply_private_permissions_to_handle(&mut std_file)?;
    } else {
        verify_private_permissions_on_handle(&std_file)?;
    }
    let marker = marker_from_file(&std_file)?;
    Ok(OpenedFile {
        file: CapFile::from_std(std_file),
        marker,
    })
}

fn cap_options(
    kind: HandleKind,
    create_new: bool,
    maybe_dir: bool,
    require_directory: bool,
) -> CapOpenOptions {
    let mut options = CapOpenOptions::new();
    match kind {
        HandleKind::DirectoryApply | HandleKind::FinalVerify => {
            options.read(true);
        }
        HandleKind::LockApply | HandleKind::StagingApply => {
            options.read(true).write(true);
        }
    }
    if create_new {
        options.create_new(true);
    }
    options.follow(FollowSymlinks::No).maybe_dir(maybe_dir);
    configure_cap_platform_options(&mut options, kind, maybe_dir, require_directory);
    options
}

fn map_cap_open_error(error: std::io::Error) -> HeleosError {
    #[cfg(unix)]
    if matches!(error.raw_os_error(), Some(code) if code == libc::ELOOP || code == libc::ENOTDIR) {
        return HeleosError::PolicyDenied;
    }
    HeleosError::Io(error)
}

#[cfg(unix)]
fn configure_cap_platform_options(
    options: &mut CapOpenOptions,
    _: HandleKind,
    _: bool,
    require_directory: bool,
) {
    use cap_std::fs::OpenOptionsExt;

    let mut flags = libc::O_NOFOLLOW | libc::O_NONBLOCK;
    if require_directory {
        flags |= libc::O_DIRECTORY;
    }
    options.custom_flags(flags).mode(0o600);
}

#[cfg(windows)]
fn configure_cap_platform_options(
    options: &mut CapOpenOptions,
    kind: HandleKind,
    maybe_dir: bool,
    _: bool,
) {
    use cap_std::fs::OpenOptionsExt;

    const FILE_SHARE_READ: u32 = 0x0000_0001;
    const FILE_SHARE_WRITE: u32 = 0x0000_0002;
    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;
    options
        .access_mode(windows_access_mask(kind))
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(
            FILE_FLAG_OPEN_REPARSE_POINT
                | if maybe_dir {
                    FILE_FLAG_BACKUP_SEMANTICS
                } else {
                    0
                },
        );
}

#[cfg(not(any(unix, windows)))]
fn configure_cap_platform_options(_: &mut CapOpenOptions, _: HandleKind, _: bool, _: bool) {}

#[cfg(windows)]
pub(super) const fn windows_access_mask(kind: HandleKind) -> u32 {
    const GENERIC_READ: u32 = 0x8000_0000;
    const GENERIC_WRITE: u32 = 0x4000_0000;
    const READ_CONTROL: u32 = 0x0002_0000;
    const WRITE_DAC: u32 = 0x0004_0000;
    const FILE_READ_ATTRIBUTES: u32 = 0x0000_0080;
    match kind {
        HandleKind::DirectoryApply => {
            GENERIC_READ | READ_CONTROL | WRITE_DAC | FILE_READ_ATTRIBUTES
        }
        HandleKind::LockApply | HandleKind::StagingApply => {
            GENERIC_READ | GENERIC_WRITE | READ_CONTROL | WRITE_DAC | FILE_READ_ATTRIBUTES
        }
        HandleKind::FinalVerify => GENERIC_READ | READ_CONTROL | FILE_READ_ATTRIBUTES,
    }
}

#[cfg(unix)]
fn open_ambient_directory(path: &Path, apply: bool) -> Result<File> {
    use std::os::unix::fs::OpenOptionsExt;

    let mut options = OpenOptions::new();
    options
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_DIRECTORY | libc::O_NONBLOCK);
    let mut file = options.open(path).map_err(map_ambient_nofollow_error)?;
    if apply {
        apply_private_permissions_to_handle(&mut file)?;
    }
    Ok(file)
}

#[cfg(unix)]
fn map_ambient_nofollow_error(error: std::io::Error) -> HeleosError {
    if matches!(error.raw_os_error(), Some(code) if code == libc::ELOOP || code == libc::ENOTDIR) {
        HeleosError::PolicyDenied
    } else {
        HeleosError::Io(error)
    }
}

#[cfg(windows)]
fn open_ambient_directory(path: &Path, apply: bool) -> Result<File> {
    use std::os::windows::fs::OpenOptionsExt;

    const GENERIC_READ: u32 = 0x8000_0000;
    const READ_CONTROL: u32 = 0x0002_0000;
    const WRITE_DAC: u32 = 0x0004_0000;
    const FILE_READ_ATTRIBUTES: u32 = 0x0000_0080;
    const FILE_SHARE_READ: u32 = 0x0000_0001;
    const FILE_SHARE_WRITE: u32 = 0x0000_0002;
    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;
    let mut file = OpenOptions::new()
        .access_mode(
            GENERIC_READ | READ_CONTROL | FILE_READ_ATTRIBUTES | if apply { WRITE_DAC } else { 0 },
        )
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS)
        .open(path)
        .map_err(HeleosError::Io)?;
    if apply {
        apply_private_permissions_to_handle(&mut file)?;
    }
    Ok(file)
}

#[cfg(not(any(unix, windows)))]
fn open_ambient_directory(path: &Path, apply: bool) -> Result<File> {
    let mut file = OpenOptions::new()
        .read(true)
        .open(path)
        .map_err(HeleosError::Io)?;
    if apply {
        apply_private_permissions_to_handle(&mut file)?;
    }
    Ok(file)
}

pub(super) fn recheck_trust_anchor(anchor: &TrustAnchor) -> Result<()> {
    let parent_path = anchor
        .configured_root
        .parent()
        .ok_or(HeleosError::PolicyDenied)?;
    let retained = anchor
        .parent
        .try_clone()
        .map_err(HeleosError::Io)?
        .into_std_file();
    recheck_ambient_directory(parent_path, &retained, anchor.parent_marker)
}

fn recheck_ambient_directory(path: &Path, retained: &File, marker: FileMarker) -> Result<()> {
    verify_private_permissions_on_handle(retained)?;
    if marker_from_file(retained)? != marker {
        return Err(HeleosError::PolicyDenied);
    }
    let reopened = open_ambient_directory(path, false)?;
    verify_private_permissions_on_handle(&reopened)?;
    if marker_from_file(&reopened)? != marker {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

pub(super) fn recheck_directory_name(
    parent: &Dir,
    name: &OsStr,
    retained: &Dir,
    marker: FileMarker,
) -> Result<()> {
    let retained_file = retained
        .try_clone()
        .map_err(HeleosError::Io)?
        .into_std_file();
    verify_private_permissions_on_handle(&retained_file)?;
    if marker_from_file(&retained_file)? != marker {
        return Err(HeleosError::PolicyDenied);
    }
    let reopened = open_directory(parent, name, false)?;
    if reopened.marker != marker {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

pub(super) fn recheck_file_name(
    parent: &Dir,
    name: &OsStr,
    retained: &CapFile,
    marker: FileMarker,
    kind: HandleKind,
) -> Result<()> {
    let retained_file = retained.try_clone().map_err(HeleosError::Io)?.into_std();
    verify_private_permissions_on_handle(&retained_file)?;
    if marker_from_file(&retained_file)? != marker {
        return Err(HeleosError::PolicyDenied);
    }
    let reopened = open_file(parent, name, kind, false, false)?;
    if reopened.marker != marker {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

#[cfg(unix)]
pub(super) fn marker_from_file(file: &File) -> Result<FileMarker> {
    use std::os::unix::fs::MetadataExt;

    let metadata = file.metadata().map_err(HeleosError::Io)?;
    Ok(FileMarker {
        first: metadata.dev(),
        second: metadata.ino(),
    })
}

#[cfg(windows)]
pub(super) fn marker_from_file(file: &File) -> Result<FileMarker> {
    use std::os::windows::fs::MetadataExt;

    let metadata = file.metadata().map_err(HeleosError::Io)?;
    // No-delete sharing is the replacement-prevention primitive on Windows. These stable fields
    // add conservative change detection; they are not presented as a unique file identity.
    Ok(FileMarker {
        first: u64::from(metadata.file_attributes()),
        second: metadata.creation_time(),
    })
}

#[cfg(not(any(unix, windows)))]
pub(super) fn marker_from_file(_: &File) -> Result<FileMarker> {
    Err(HeleosError::PolicyDenied)
}

pub(super) fn ensure_bare_component(name: &OsStr) -> Result<()> {
    let path = Path::new(name);
    if matches!(path.components().next(), Some(Component::Normal(_)))
        && path.components().count() == 1
    {
        Ok(())
    } else {
        Err(HeleosError::PolicyDenied)
    }
}
