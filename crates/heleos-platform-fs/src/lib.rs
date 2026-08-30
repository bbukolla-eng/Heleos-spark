#![deny(unsafe_code)]

//! Retained-handle filesystem publication primitives.

use std::ffi::{OsStr, OsString};
use std::fmt;
use std::io;
use std::path::{Component, Path};

/// A validated, losslessly retained filesystem name containing one component.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct OneComponentName(OsString);

/// The supplied name was not exactly one safe platform component.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct InvalidOneComponentName;

impl fmt::Display for InvalidOneComponentName {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("name must be exactly one valid filesystem component")
    }
}

impl std::error::Error for InvalidOneComponentName {}

impl TryFrom<OsString> for OneComponentName {
    type Error = InvalidOneComponentName;

    fn try_from(value: OsString) -> Result<Self, Self::Error> {
        let mut components = Path::new(&value).components();
        let is_exact_normal_component = matches!(
            (components.next(), components.next()),
            (Some(Component::Normal(component)), None) if component == value.as_os_str()
        );
        let encoded_length_is_safe = value
            .as_encoded_bytes()
            .len()
            .checked_add(1)
            .is_some_and(|length| length <= isize::MAX as usize);

        if !is_exact_normal_component
            || !encoded_length_is_safe
            || value.as_encoded_bytes().contains(&0)
        {
            return Err(InvalidOneComponentName);
        }

        #[cfg(windows)]
        {
            use std::os::windows::ffi::OsStrExt;

            let utf16_length = value.as_os_str().encode_wide().count();
            if value
                .as_os_str()
                .encode_wide()
                .any(|unit| unit == b':' as u16)
                || windows_rename_buffer_layout(utf16_length).is_none()
            {
                return Err(InvalidOneComponentName);
            }
        }

        Ok(Self(value))
    }
}

impl OneComponentName {
    fn as_os_str(&self) -> &OsStr {
        &self.0
    }
}

/// A retained regular file could not be published without replacement.
#[derive(Debug)]
pub enum PublishFileNoreplaceError {
    /// The final name already exists.
    Collision,
    /// The platform does not provide the frozen no-replace primitive.
    Unsupported,
    /// A retained identity, source policy, or name binding did not match.
    IdentityMismatch,
    /// The platform primitive failed for another reason.
    Io(io::Error),
}

impl fmt::Display for PublishFileNoreplaceError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Collision => formatter.write_str("final file name already exists"),
            Self::Unsupported => formatter.write_str("file no-replace publication is unsupported"),
            Self::IdentityMismatch => {
                formatter.write_str("retained file publication identity does not match")
            }
            Self::Io(error) => write!(formatter, "file no-replace publication failed: {error}"),
        }
    }
}

impl std::error::Error for PublishFileNoreplaceError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Io(error) => Some(error),
            Self::Collision | Self::Unsupported | Self::IdentityMismatch => None,
        }
    }
}

/// A retained directory could not be published without replacement.
#[derive(Debug)]
pub enum PublishDirectoryNoreplaceError {
    /// The final name already exists.
    Collision,
    /// The platform does not provide the frozen no-replace primitive.
    Unsupported,
    /// A retained identity, source policy, or name binding did not match.
    IdentityMismatch,
    /// The platform primitive failed for another reason.
    Io(io::Error),
}

impl fmt::Display for PublishDirectoryNoreplaceError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Collision => formatter.write_str("final directory name already exists"),
            Self::Unsupported => {
                formatter.write_str("directory no-replace publication is unsupported")
            }
            Self::IdentityMismatch => {
                formatter.write_str("retained directory publication identity does not match")
            }
            Self::Io(error) => {
                write!(
                    formatter,
                    "directory no-replace publication failed: {error}"
                )
            }
        }
    }
}

impl std::error::Error for PublishDirectoryNoreplaceError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Io(error) => Some(error),
            Self::Collision | Self::Unsupported | Self::IdentityMismatch => None,
        }
    }
}

/// Publishes a retained regular file under an absent sibling name.
///
/// The source and parent handles are borrowed and remain owned by the caller.
pub fn publish_file_noreplace(
    parent: &std::fs::File,
    staged_file: &std::fs::File,
    staged_name: &OneComponentName,
    final_name: &OneComponentName,
) -> Result<(), PublishFileNoreplaceError> {
    #[cfg(target_os = "macos")]
    {
        publish_file_noreplace_macos(parent, staged_file, staged_name, final_name)
    }

    #[cfg(windows)]
    {
        publish_file_noreplace_windows(parent, staged_file, staged_name, final_name)
    }

    #[cfg(not(any(target_os = "macos", windows)))]
    {
        let _ = (parent, staged_file, staged_name, final_name);
        Err(PublishFileNoreplaceError::Unsupported)
    }
}

/// Publishes a retained directory under an absent sibling name.
///
/// The source and parent handles are borrowed and remain owned by the caller.
pub fn publish_directory_noreplace(
    parent: &std::fs::File,
    staged_directory: &std::fs::File,
    staged_name: &OneComponentName,
    final_name: &OneComponentName,
) -> Result<(), PublishDirectoryNoreplaceError> {
    #[cfg(target_os = "macos")]
    {
        publish_directory_noreplace_macos(parent, staged_directory, staged_name, final_name)
    }

    #[cfg(windows)]
    {
        publish_directory_noreplace_windows(parent, staged_directory, staged_name, final_name)
    }

    #[cfg(not(any(target_os = "macos", windows)))]
    {
        let _ = (parent, staged_directory, staged_name, final_name);
        Err(PublishDirectoryNoreplaceError::Unsupported)
    }
}

#[cfg(target_os = "macos")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct MacOsIdentity {
    device: u64,
    inode: u64,
}

#[cfg(target_os = "macos")]
#[derive(Clone, Copy)]
enum PublicationSourceKind {
    File,
    Directory,
}

#[cfg(target_os = "macos")]
struct MacOsPreflight {
    _parent: cap_std::fs::Dir,
    _bound_source: std::fs::File,
}

#[cfg(target_os = "macos")]
enum MacOsPreflightError {
    IdentityMismatch,
    Io(io::Error),
}

#[cfg(target_os = "macos")]
fn macos_identity(metadata: &std::fs::Metadata) -> MacOsIdentity {
    use std::os::unix::fs::MetadataExt;

    MacOsIdentity {
        device: metadata.dev(),
        inode: metadata.ino(),
    }
}

#[cfg(target_os = "macos")]
fn macos_source_matches(metadata: &std::fs::Metadata, kind: PublicationSourceKind) -> bool {
    use std::os::unix::fs::MetadataExt;

    match kind {
        PublicationSourceKind::File => {
            metadata.is_file() && !metadata.file_type().is_symlink() && metadata.nlink() == 1
        }
        PublicationSourceKind::Directory => metadata.is_dir() && !metadata.file_type().is_symlink(),
    }
}

#[cfg(target_os = "macos")]
fn preflight_macos(
    parent: &std::fs::File,
    source: &std::fs::File,
    staged_name: &OneComponentName,
    kind: PublicationSourceKind,
) -> Result<MacOsPreflight, MacOsPreflightError> {
    use cap_fs_ext::{DirExt, FollowSymlinks, OpenOptionsFollowExt};

    let parent_metadata = parent
        .metadata()
        .map_err(|_| MacOsPreflightError::IdentityMismatch)?;
    if !parent_metadata.is_dir() || parent_metadata.file_type().is_symlink() {
        return Err(MacOsPreflightError::IdentityMismatch);
    }
    let parent_identity = macos_identity(&parent_metadata);

    let source_metadata = source
        .metadata()
        .map_err(|_| MacOsPreflightError::IdentityMismatch)?;
    if !macos_source_matches(&source_metadata, kind) {
        return Err(MacOsPreflightError::IdentityMismatch);
    }
    let source_identity = macos_identity(&source_metadata);

    let cloned_parent = parent.try_clone().map_err(MacOsPreflightError::Io)?;
    let cloned_parent_metadata = cloned_parent
        .metadata()
        .map_err(|_| MacOsPreflightError::IdentityMismatch)?;
    if !cloned_parent_metadata.is_dir()
        || cloned_parent_metadata.file_type().is_symlink()
        || macos_identity(&cloned_parent_metadata) != parent_identity
    {
        return Err(MacOsPreflightError::IdentityMismatch);
    }
    let parent_directory = cap_std::fs::Dir::from_std_file(cloned_parent);

    let bound_source = match kind {
        PublicationSourceKind::File => {
            let mut options = cap_std::fs::OpenOptions::new();
            options.read(true).follow(FollowSymlinks::No);
            parent_directory
                .open_with(Path::new(staged_name.as_os_str()), &options)
                .map_err(|_| MacOsPreflightError::IdentityMismatch)?
                .into_std()
        }
        PublicationSourceKind::Directory => parent_directory
            .open_dir_nofollow(Path::new(staged_name.as_os_str()))
            .map_err(|_| MacOsPreflightError::IdentityMismatch)?
            .into_std_file(),
    };

    let bound_metadata = bound_source
        .metadata()
        .map_err(|_| MacOsPreflightError::IdentityMismatch)?;
    let source_recheck = source
        .metadata()
        .map_err(|_| MacOsPreflightError::IdentityMismatch)?;
    let parent_recheck = parent
        .metadata()
        .map_err(|_| MacOsPreflightError::IdentityMismatch)?;
    if !macos_source_matches(&bound_metadata, kind)
        || !macos_source_matches(&source_recheck, kind)
        || macos_identity(&bound_metadata) != source_identity
        || macos_identity(&source_recheck) != source_identity
        || !parent_recheck.is_dir()
        || parent_recheck.file_type().is_symlink()
        || macos_identity(&parent_recheck) != parent_identity
    {
        return Err(MacOsPreflightError::IdentityMismatch);
    }

    Ok(MacOsPreflight {
        _parent: parent_directory,
        _bound_source: bound_source,
    })
}

#[cfg(target_os = "macos")]
fn map_macos_file_preflight(error: MacOsPreflightError) -> PublishFileNoreplaceError {
    match error {
        MacOsPreflightError::IdentityMismatch => PublishFileNoreplaceError::IdentityMismatch,
        MacOsPreflightError::Io(error) => PublishFileNoreplaceError::Io(error),
    }
}

#[cfg(target_os = "macos")]
fn map_macos_directory_preflight(error: MacOsPreflightError) -> PublishDirectoryNoreplaceError {
    match error {
        MacOsPreflightError::IdentityMismatch => PublishDirectoryNoreplaceError::IdentityMismatch,
        MacOsPreflightError::Io(error) => PublishDirectoryNoreplaceError::Io(error),
    }
}

#[cfg(target_os = "macos")]
fn publish_file_noreplace_macos(
    parent: &std::fs::File,
    staged_file: &std::fs::File,
    staged_name: &OneComponentName,
    final_name: &OneComponentName,
) -> Result<(), PublishFileNoreplaceError> {
    if staged_name == final_name {
        return Err(PublishFileNoreplaceError::IdentityMismatch);
    }
    let _preflight = preflight_macos(
        parent,
        staged_file,
        staged_name,
        PublicationSourceKind::File,
    )
    .map_err(map_macos_file_preflight)?;

    rustix::fs::renameat_with(
        parent,
        staged_name.as_os_str(),
        parent,
        final_name.as_os_str(),
        rustix::fs::RenameFlags::NOREPLACE,
    )
    .map_err(map_macos_file_error)
}

#[cfg(target_os = "macos")]
fn publish_directory_noreplace_macos(
    parent: &std::fs::File,
    staged_directory: &std::fs::File,
    staged_name: &OneComponentName,
    final_name: &OneComponentName,
) -> Result<(), PublishDirectoryNoreplaceError> {
    if staged_name == final_name {
        return Err(PublishDirectoryNoreplaceError::IdentityMismatch);
    }
    let _preflight = preflight_macos(
        parent,
        staged_directory,
        staged_name,
        PublicationSourceKind::Directory,
    )
    .map_err(map_macos_directory_preflight)?;

    rustix::fs::renameat_with(
        parent,
        staged_name.as_os_str(),
        parent,
        final_name.as_os_str(),
        rustix::fs::RenameFlags::NOREPLACE,
    )
    .map_err(map_macos_directory_error)
}

#[cfg(target_os = "macos")]
fn macos_error_is_unsupported(error: rustix::io::Errno) -> bool {
    error == rustix::io::Errno::NOSYS
        || error == rustix::io::Errno::INVAL
        || error == rustix::io::Errno::NOTSUP
        || error == rustix::io::Errno::OPNOTSUPP
}

#[cfg(target_os = "macos")]
fn map_macos_file_error(error: rustix::io::Errno) -> PublishFileNoreplaceError {
    if error == rustix::io::Errno::EXIST {
        PublishFileNoreplaceError::Collision
    } else if macos_error_is_unsupported(error) {
        PublishFileNoreplaceError::Unsupported
    } else if error == rustix::io::Errno::XDEV || error == rustix::io::Errno::NOENT {
        PublishFileNoreplaceError::IdentityMismatch
    } else {
        PublishFileNoreplaceError::Io(io::Error::from_raw_os_error(error.raw_os_error()))
    }
}

#[cfg(target_os = "macos")]
fn map_macos_directory_error(error: rustix::io::Errno) -> PublishDirectoryNoreplaceError {
    if error == rustix::io::Errno::EXIST {
        PublishDirectoryNoreplaceError::Collision
    } else if macos_error_is_unsupported(error) {
        PublishDirectoryNoreplaceError::Unsupported
    } else if error == rustix::io::Errno::XDEV || error == rustix::io::Errno::NOENT {
        PublishDirectoryNoreplaceError::IdentityMismatch
    } else {
        PublishDirectoryNoreplaceError::Io(io::Error::from_raw_os_error(error.raw_os_error()))
    }
}

#[cfg(windows)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct WindowsIdentity {
    volume_serial_number: u32,
    file_index: u64,
}

#[cfg(windows)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum WindowsPublicationSourceKind {
    File,
    Directory,
}

#[cfg(windows)]
struct WindowsPreflight {
    _parent: cap_std::fs::Dir,
    _bound_source: std::fs::File,
}

#[cfg(windows)]
enum WindowsPreflightError {
    IdentityMismatch,
    Io(io::Error),
}

#[cfg(windows)]
fn windows_identity_from_parts(
    volume_serial_number: Option<u32>,
    file_index: Option<u64>,
) -> Option<WindowsIdentity> {
    Some(WindowsIdentity {
        volume_serial_number: volume_serial_number?,
        file_index: file_index?,
    })
}

#[cfg(windows)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct WindowsHandleFacts {
    identity: WindowsIdentity,
    number_of_links: Option<u32>,
    is_file: bool,
    is_directory: bool,
    is_reparse: bool,
}

#[cfg(windows)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct WindowsMetadataFields {
    file_attributes: u32,
    volume_serial_number: Option<u32>,
    number_of_links: Option<u32>,
    file_index: Option<u64>,
}

#[cfg(windows)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum WindowsMetadataObservation {
    Unavailable,
    Available(WindowsMetadataFields),
}

#[cfg(windows)]
fn windows_has_one_link(number_of_links: Option<u32>) -> bool {
    number_of_links == Some(1)
}

#[cfg(windows)]
fn windows_handle_facts_from_observation(
    observation: WindowsMetadataObservation,
) -> Option<WindowsHandleFacts> {
    use windows_sys::Win32::Storage::FileSystem::{
        FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT,
    };

    let WindowsMetadataObservation::Available(fields) = observation else {
        return None;
    };
    let identity = windows_identity_from_parts(fields.volume_serial_number, fields.file_index)?;
    let is_directory = fields.file_attributes & FILE_ATTRIBUTE_DIRECTORY != 0;

    Some(WindowsHandleFacts {
        identity,
        number_of_links: fields.number_of_links,
        is_file: !is_directory,
        is_directory,
        is_reparse: fields.file_attributes & FILE_ATTRIBUTE_REPARSE_POINT != 0,
    })
}

#[cfg(windows)]
fn windows_handle_facts(file: &std::fs::File) -> Option<WindowsHandleFacts> {
    use cap_primitives::fs::{_WindowsByHandle, Metadata};

    let metadata = match Metadata::from_file(file) {
        Ok(metadata) => metadata,
        Err(_) => {
            return windows_handle_facts_from_observation(WindowsMetadataObservation::Unavailable);
        }
    };

    windows_handle_facts_from_observation(WindowsMetadataObservation::Available(
        WindowsMetadataFields {
            file_attributes: _WindowsByHandle::file_attributes(&metadata),
            volume_serial_number: _WindowsByHandle::volume_serial_number(&metadata),
            number_of_links: _WindowsByHandle::number_of_links(&metadata),
            file_index: _WindowsByHandle::file_index(&metadata),
        },
    ))
}

#[cfg(windows)]
fn windows_source_matches(facts: WindowsHandleFacts, kind: WindowsPublicationSourceKind) -> bool {
    if facts.is_reparse {
        return false;
    }
    match kind {
        WindowsPublicationSourceKind::File => {
            facts.is_file && windows_has_one_link(facts.number_of_links)
        }
        WindowsPublicationSourceKind::Directory => facts.is_directory,
    }
}

#[cfg(windows)]
fn windows_parent_marker_matches(
    current: WindowsHandleFacts,
    retained: WindowsHandleFacts,
) -> bool {
    current.is_directory
        && !current.is_reparse
        && current.identity == retained.identity
        && current.is_directory == retained.is_directory
        && current.is_file == retained.is_file
        && current.is_reparse == retained.is_reparse
}

#[cfg(windows)]
fn windows_source_marker_matches(
    current: WindowsHandleFacts,
    retained: WindowsHandleFacts,
    kind: WindowsPublicationSourceKind,
) -> bool {
    windows_source_matches(current, kind)
        && current.identity == retained.identity
        && current.is_directory == retained.is_directory
        && current.is_file == retained.is_file
        && current.is_reparse == retained.is_reparse
        && (kind == WindowsPublicationSourceKind::Directory
            || current.number_of_links == retained.number_of_links)
}

#[cfg(windows)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct WindowsOpenSpec {
    access_mode: u32,
    share_mode: u32,
    custom_flags: u32,
}

#[cfg(windows)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct WindowsBindingOptionsSpec {
    open: WindowsOpenSpec,
    follow: cap_fs_ext::FollowSymlinks,
    maybe_dir: bool,
}

#[cfg(all(windows, test))]
fn windows_file_subject_open_spec() -> WindowsOpenSpec {
    use windows_sys::Win32::Foundation::{GENERIC_READ, GENERIC_WRITE};
    use windows_sys::Win32::Storage::FileSystem::{
        DELETE, FILE_FLAG_OPEN_REPARSE_POINT, FILE_READ_ATTRIBUTES, FILE_SHARE_READ,
        FILE_SHARE_WRITE, READ_CONTROL, WRITE_DAC,
    };

    WindowsOpenSpec {
        access_mode: GENERIC_READ
            | GENERIC_WRITE
            | DELETE
            | READ_CONTROL
            | WRITE_DAC
            | FILE_READ_ATTRIBUTES,
        share_mode: FILE_SHARE_READ | FILE_SHARE_WRITE,
        custom_flags: FILE_FLAG_OPEN_REPARSE_POINT,
    }
}

#[cfg(all(windows, test))]
fn windows_directory_subject_open_spec() -> WindowsOpenSpec {
    use windows_sys::Win32::Storage::FileSystem::{
        DELETE, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ, FILE_SHARE_WRITE, READ_CONTROL,
    };

    WindowsOpenSpec {
        access_mode: DELETE | READ_CONTROL | FILE_READ_ATTRIBUTES,
        share_mode: FILE_SHARE_READ | FILE_SHARE_WRITE,
        custom_flags: FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
    }
}

#[cfg(all(windows, test))]
fn windows_parent_open_spec() -> WindowsOpenSpec {
    use windows_sys::Win32::Foundation::GENERIC_READ;
    use windows_sys::Win32::Storage::FileSystem::{
        FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ, FILE_SHARE_WRITE, READ_CONTROL,
    };

    WindowsOpenSpec {
        access_mode: GENERIC_READ | READ_CONTROL | FILE_READ_ATTRIBUTES,
        share_mode: FILE_SHARE_READ | FILE_SHARE_WRITE,
        custom_flags: FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
    }
}

#[cfg(windows)]
fn windows_binding_options_spec(kind: WindowsPublicationSourceKind) -> WindowsBindingOptionsSpec {
    use cap_fs_ext::FollowSymlinks;
    use windows_sys::Win32::Foundation::GENERIC_READ;
    use windows_sys::Win32::Storage::FileSystem::{
        FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_READ_ATTRIBUTES,
        FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE, READ_CONTROL,
    };

    WindowsBindingOptionsSpec {
        open: WindowsOpenSpec {
            access_mode: GENERIC_READ | READ_CONTROL | FILE_READ_ATTRIBUTES,
            share_mode: FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            custom_flags: match kind {
                WindowsPublicationSourceKind::File => FILE_FLAG_OPEN_REPARSE_POINT,
                WindowsPublicationSourceKind::Directory => {
                    FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT
                }
            },
        },
        follow: FollowSymlinks::No,
        maybe_dir: false,
    }
}

#[cfg(windows)]
fn windows_binding_options(kind: WindowsPublicationSourceKind) -> cap_std::fs::OpenOptions {
    use cap_fs_ext::{OpenOptionsExt, OpenOptionsFollowExt, OpenOptionsMaybeDirExt};

    let spec = windows_binding_options_spec(kind);
    let mut options = cap_std::fs::OpenOptions::new();
    options
        .read(true)
        .access_mode(spec.open.access_mode)
        .share_mode(spec.open.share_mode)
        .custom_flags(spec.open.custom_flags)
        .follow(spec.follow)
        .maybe_dir(spec.maybe_dir);
    options
}

#[cfg(windows)]
fn preflight_windows_with_extractor<F>(
    parent: &std::fs::File,
    source: &std::fs::File,
    staged_name: &OneComponentName,
    kind: WindowsPublicationSourceKind,
    extractor: &mut F,
) -> Result<WindowsPreflight, WindowsPreflightError>
where
    F: FnMut(&std::fs::File) -> Option<WindowsHandleFacts>,
{
    let Some(parent_facts) = extractor(parent) else {
        return Err(WindowsPreflightError::IdentityMismatch);
    };
    if !parent_facts.is_directory || parent_facts.is_reparse {
        return Err(WindowsPreflightError::IdentityMismatch);
    }

    let Some(source_facts) = extractor(source) else {
        return Err(WindowsPreflightError::IdentityMismatch);
    };
    if !windows_source_matches(source_facts, kind) {
        return Err(WindowsPreflightError::IdentityMismatch);
    }

    let cloned_parent = parent.try_clone().map_err(WindowsPreflightError::Io)?;
    let Some(cloned_parent_facts) = extractor(&cloned_parent) else {
        return Err(WindowsPreflightError::IdentityMismatch);
    };
    if !windows_parent_marker_matches(cloned_parent_facts, parent_facts) {
        return Err(WindowsPreflightError::IdentityMismatch);
    }
    let parent_directory = cap_std::fs::Dir::from_std_file(cloned_parent);
    let bound_source = parent_directory
        .open_with(
            Path::new(staged_name.as_os_str()),
            &windows_binding_options(kind),
        )
        .map_err(|_| WindowsPreflightError::IdentityMismatch)?
        .into_std();

    let Some(bound_facts) = extractor(&bound_source) else {
        return Err(WindowsPreflightError::IdentityMismatch);
    };
    let Some(source_recheck) = extractor(source) else {
        return Err(WindowsPreflightError::IdentityMismatch);
    };
    let Some(parent_recheck) = extractor(parent) else {
        return Err(WindowsPreflightError::IdentityMismatch);
    };
    if !windows_source_marker_matches(bound_facts, source_facts, kind)
        || !windows_source_marker_matches(source_recheck, source_facts, kind)
        || !windows_parent_marker_matches(parent_recheck, parent_facts)
    {
        return Err(WindowsPreflightError::IdentityMismatch);
    }

    Ok(WindowsPreflight {
        _parent: parent_directory,
        _bound_source: bound_source,
    })
}

#[cfg(windows)]
fn map_windows_file_preflight(error: WindowsPreflightError) -> PublishFileNoreplaceError {
    match error {
        WindowsPreflightError::IdentityMismatch => PublishFileNoreplaceError::IdentityMismatch,
        WindowsPreflightError::Io(error) => PublishFileNoreplaceError::Io(error),
    }
}

#[cfg(windows)]
fn map_windows_directory_preflight(error: WindowsPreflightError) -> PublishDirectoryNoreplaceError {
    match error {
        WindowsPreflightError::IdentityMismatch => PublishDirectoryNoreplaceError::IdentityMismatch,
        WindowsPreflightError::Io(error) => PublishDirectoryNoreplaceError::Io(error),
    }
}

#[cfg(windows)]
fn publish_file_noreplace_windows_with_extractor<F>(
    parent: &std::fs::File,
    staged_file: &std::fs::File,
    staged_name: &OneComponentName,
    final_name: &OneComponentName,
    mut extractor: F,
) -> Result<(), PublishFileNoreplaceError>
where
    F: FnMut(&std::fs::File) -> Option<WindowsHandleFacts>,
{
    if staged_name == final_name {
        return Err(PublishFileNoreplaceError::IdentityMismatch);
    }
    let _preflight = preflight_windows_with_extractor(
        parent,
        staged_file,
        staged_name,
        WindowsPublicationSourceKind::File,
        &mut extractor,
    )
    .map_err(map_windows_file_preflight)?;

    set_file_information_by_handle_noreplace(staged_file, parent, final_name)
        .map_err(map_windows_file_error)
}

#[cfg(windows)]
fn publish_file_noreplace_windows(
    parent: &std::fs::File,
    staged_file: &std::fs::File,
    staged_name: &OneComponentName,
    final_name: &OneComponentName,
) -> Result<(), PublishFileNoreplaceError> {
    publish_file_noreplace_windows_with_extractor(
        parent,
        staged_file,
        staged_name,
        final_name,
        windows_handle_facts,
    )
}

#[cfg(windows)]
fn publish_directory_noreplace_windows_with_extractor<F>(
    parent: &std::fs::File,
    staged_directory: &std::fs::File,
    staged_name: &OneComponentName,
    final_name: &OneComponentName,
    mut extractor: F,
) -> Result<(), PublishDirectoryNoreplaceError>
where
    F: FnMut(&std::fs::File) -> Option<WindowsHandleFacts>,
{
    if staged_name == final_name {
        return Err(PublishDirectoryNoreplaceError::IdentityMismatch);
    }
    let _preflight = preflight_windows_with_extractor(
        parent,
        staged_directory,
        staged_name,
        WindowsPublicationSourceKind::Directory,
        &mut extractor,
    )
    .map_err(map_windows_directory_preflight)?;

    set_file_information_by_handle_noreplace(staged_directory, parent, final_name)
        .map_err(map_windows_directory_error)
}

#[cfg(windows)]
fn publish_directory_noreplace_windows(
    parent: &std::fs::File,
    staged_directory: &std::fs::File,
    staged_name: &OneComponentName,
    final_name: &OneComponentName,
) -> Result<(), PublishDirectoryNoreplaceError> {
    publish_directory_noreplace_windows_with_extractor(
        parent,
        staged_directory,
        staged_name,
        final_name,
        windows_handle_facts,
    )
}

#[cfg(windows)]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct WindowsRenameBufferLayout {
    file_name_byte_length: u32,
    buffer_byte_length: usize,
    buffer_byte_length_u32: u32,
    allocation_element_count: usize,
}

#[cfg(windows)]
fn windows_rename_buffer_layout(utf16_length: usize) -> Option<WindowsRenameBufferLayout> {
    use windows_sys::Win32::Storage::FileSystem::FILE_RENAME_INFO;

    let file_name_byte_length_usize = utf16_length.checked_mul(size_of::<u16>())?;
    let file_name_byte_length = u32::try_from(file_name_byte_length_usize).ok()?;
    let terminated_utf16_length = utf16_length.checked_add(1)?;
    let terminated_byte_length = terminated_utf16_length.checked_mul(size_of::<u16>())?;
    let buffer_byte_length =
        std::mem::offset_of!(FILE_RENAME_INFO, FileName).checked_add(terminated_byte_length)?;
    let buffer_byte_length_u32 = u32::try_from(buffer_byte_length).ok()?;
    let element_size = size_of::<FILE_RENAME_INFO>();
    let allocation_element_count =
        buffer_byte_length.checked_add(element_size.checked_sub(1)?)? / element_size;

    Some(WindowsRenameBufferLayout {
        file_name_byte_length,
        buffer_byte_length,
        buffer_byte_length_u32,
        allocation_element_count,
    })
}

#[cfg(windows)]
#[allow(unsafe_code)]
fn set_file_information_by_handle_noreplace(
    source: &std::fs::File,
    parent: &std::fs::File,
    final_name: &OneComponentName,
) -> io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Foundation::HANDLE;
    use windows_sys::Win32::Storage::FileSystem::{
        FILE_RENAME_INFO, FileRenameInfo, SetFileInformationByHandle,
    };

    let utf16_length = final_name.as_os_str().encode_wide().count();
    let layout = windows_rename_buffer_layout(utf16_length)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, InvalidOneComponentName))?;
    let mut allocation = vec![FILE_RENAME_INFO::default(); layout.allocation_element_count];
    let information = allocation.as_mut_ptr();

    // SAFETY:
    // - `allocation` is one live, suitably aligned `FILE_RENAME_INFO` allocation whose
    //   checked size covers the fixed prefix, every encoded UTF-16 unit, and one terminator.
    // - `FileNameLength` is the checked byte count excluding that terminator and both byte
    //   counts fit the Win32 `u32` ABI fields.
    // - `OneComponentName` excludes NUL, colon, traversal, roots, prefixes, and separators;
    //   `encode_wide` preserves every admitted WTF-16 unit without lossy conversion.
    // - `source` and `parent` are borrowed live handles. Safe preflight immediately above
    //   froze their type, non-reparse state, strong identity, and source-name binding.
    // - Win32 reads the buffer only for this call; no pointer, buffer, or unchecked API escapes.
    let succeeded = unsafe {
        (*information).Anonymous.ReplaceIfExists = false;
        (*information).RootDirectory = parent.as_raw_handle() as HANDLE;
        (*information).FileNameLength = layout.file_name_byte_length;
        let file_name = std::ptr::addr_of_mut!((*information).FileName).cast::<u16>();
        for (index, unit) in final_name.as_os_str().encode_wide().enumerate() {
            file_name.add(index).write(unit);
        }
        file_name.add(utf16_length).write(0);
        SetFileInformationByHandle(
            source.as_raw_handle() as HANDLE,
            FileRenameInfo,
            information.cast(),
            layout.buffer_byte_length_u32,
        )
    };

    if succeeded == 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[cfg(windows)]
fn map_windows_file_error(error: io::Error) -> PublishFileNoreplaceError {
    match error.raw_os_error() {
        Some(80 | 183) => PublishFileNoreplaceError::Collision,
        Some(1 | 50) => PublishFileNoreplaceError::Unsupported,
        _ => PublishFileNoreplaceError::Io(error),
    }
}

#[cfg(windows)]
fn map_windows_directory_error(error: io::Error) -> PublishDirectoryNoreplaceError {
    match error.raw_os_error() {
        Some(80 | 183) => PublishDirectoryNoreplaceError::Collision,
        Some(1 | 50) => PublishDirectoryNoreplaceError::Unsupported,
        _ => PublishDirectoryNoreplaceError::Io(error),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::{OsStr, OsString};
    use std::fs::{self, File, OpenOptions};
    use std::io::Write;
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT_TEST_DIRECTORY: AtomicU64 = AtomicU64::new(0);

    struct TestDirectory(PathBuf);

    impl TestDirectory {
        fn new() -> Self {
            let nonce = NEXT_TEST_DIRECTORY.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir()
                .join(format!("heleos-platform-fs-{}-{nonce}", std::process::id()));
            fs::create_dir(&path).expect("create private test directory");
            Self(path)
        }

        fn path(&self) -> &Path {
            &self.0
        }

        fn parent_handle(&self) -> File {
            #[cfg(windows)]
            {
                windows_open_with_spec(&self.0, windows_parent_open_spec(), false)
                    .expect("open retained parent")
            }

            #[cfg(not(windows))]
            {
                File::open(&self.0).expect("open retained parent")
            }
        }

        fn create_file(&self, name: &str, bytes: &[u8]) -> File {
            #[cfg(windows)]
            let mut file =
                windows_open_with_spec(&self.0.join(name), windows_file_subject_open_spec(), true)
                    .expect("create retained staged file");

            #[cfg(not(windows))]
            let mut file = {
                let mut options = OpenOptions::new();
                options.read(true).write(true).create_new(true);
                options
                    .open(self.0.join(name))
                    .expect("create retained staged file")
            };

            file.write_all(bytes).expect("write staged file");
            file.sync_all().expect("sync staged file");
            file
        }

        fn create_directory(&self, name: &str) -> File {
            let path = self.0.join(name);
            fs::create_dir(&path).expect("create retained staged directory");

            #[cfg(windows)]
            {
                windows_open_with_spec(&path, windows_directory_subject_open_spec(), false)
                    .expect("open retained staged directory")
            }

            #[cfg(not(windows))]
            {
                File::open(path).expect("open retained staged directory")
            }
        }
    }

    #[cfg(windows)]
    fn windows_open_with_spec(
        path: &Path,
        spec: WindowsOpenSpec,
        create_new: bool,
    ) -> io::Result<File> {
        use std::os::windows::fs::OpenOptionsExt;

        let mut options = OpenOptions::new();
        options
            .access_mode(spec.access_mode)
            .share_mode(spec.share_mode)
            .custom_flags(spec.custom_flags);
        if create_new {
            options.write(true).create_new(true);
        }
        options.open(path)
    }

    impl Drop for TestDirectory {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.0).expect("remove private test directory");
        }
    }

    fn component(value: &str) -> OneComponentName {
        OneComponentName::try_from(OsString::from(value)).expect("valid one-component name")
    }

    #[test]
    fn one_component_name_accepts_exactly_one_normal_component() {
        let name = OneComponentName::try_from(OsString::from("ciphertext.partial"))
            .expect("normal component is valid");

        assert_eq!(name.as_os_str(), OsStr::new("ciphertext.partial"));
    }

    #[test]
    fn one_component_name_rejects_empty_rooted_and_traversal_spellings() {
        let invalid = ["", ".", "..", "/rooted", "a/b"];

        for value in invalid {
            assert!(
                OneComponentName::try_from(OsString::from(value)).is_err(),
                "{value:?} must be rejected"
            );
        }
    }

    #[test]
    fn one_component_name_rejects_embedded_nul() {
        assert!(OneComponentName::try_from(OsString::from("bad\0name")).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn one_component_name_preserves_non_utf8_unix_bytes() {
        use std::os::unix::ffi::{OsStrExt, OsStringExt};

        let raw = vec![b'n', 0xff, b'm'];
        let name = OneComponentName::try_from(OsString::from_vec(raw.clone()))
            .expect("non-UTF-8 normal component is valid");

        assert_eq!(name.as_os_str().as_bytes(), raw);
    }

    #[cfg(any(target_os = "macos", windows))]
    #[test]
    fn file_publication_moves_the_bound_regular_file_without_replacement() {
        let directory = TestDirectory::new();
        let parent = directory.parent_handle();
        let staged = directory.create_file("backup.partial", b"ciphertext");

        publish_file_noreplace(
            &parent,
            &staged,
            &component("backup.partial"),
            &component("backup.age"),
        )
        .expect("publish absent final name");

        assert!(!directory.path().join("backup.partial").exists());
        assert_eq!(
            fs::read(directory.path().join("backup.age")).expect("read published file"),
            b"ciphertext"
        );
    }

    #[cfg(any(target_os = "macos", windows))]
    #[test]
    fn file_publication_collision_preserves_both_names() {
        let directory = TestDirectory::new();
        let parent = directory.parent_handle();
        let staged = directory.create_file("backup.partial", b"candidate");
        let _existing = directory.create_file("backup.age", b"winner");

        let result = publish_file_noreplace(
            &parent,
            &staged,
            &component("backup.partial"),
            &component("backup.age"),
        );

        assert!(matches!(result, Err(PublishFileNoreplaceError::Collision)));
        assert_eq!(
            fs::read(directory.path().join("backup.partial")).expect("read staged file"),
            b"candidate"
        );
        assert_eq!(
            fs::read(directory.path().join("backup.age")).expect("read existing winner"),
            b"winner"
        );
    }

    #[cfg(any(target_os = "macos", windows))]
    #[test]
    fn file_publication_rejects_name_to_handle_mismatch() {
        let directory = TestDirectory::new();
        let parent = directory.parent_handle();
        let staged = directory.create_file("actual.partial", b"actual");
        let _other = directory.create_file("claimed.partial", b"other");

        let result = publish_file_noreplace(
            &parent,
            &staged,
            &component("claimed.partial"),
            &component("backup.age"),
        );

        assert!(matches!(
            result,
            Err(PublishFileNoreplaceError::IdentityMismatch)
        ));
        assert!(!directory.path().join("backup.age").exists());
        assert_eq!(
            fs::read(directory.path().join("actual.partial")).expect("read actual source"),
            b"actual"
        );
        assert_eq!(
            fs::read(directory.path().join("claimed.partial")).expect("read claimed source"),
            b"other"
        );
    }

    #[cfg(any(target_os = "macos", windows))]
    #[test]
    fn file_publication_rejects_directory_source() {
        let directory = TestDirectory::new();
        let parent = directory.parent_handle();
        let staged = directory.create_directory("tree.partial");

        let result = publish_file_noreplace(
            &parent,
            &staged,
            &component("tree.partial"),
            &component("backup.age"),
        );

        assert!(matches!(
            result,
            Err(PublishFileNoreplaceError::IdentityMismatch)
        ));
        assert!(directory.path().join("tree.partial").is_dir());
        assert!(!directory.path().join("backup.age").exists());
    }

    #[cfg(any(target_os = "macos", windows))]
    #[test]
    fn file_publication_rejects_equal_names_before_mutation() {
        let directory = TestDirectory::new();
        let parent = directory.parent_handle();
        let staged = directory.create_file("backup.partial", b"candidate");
        let name = component("backup.partial");

        let result = publish_file_noreplace(&parent, &staged, &name, &name);

        assert!(matches!(
            result,
            Err(PublishFileNoreplaceError::IdentityMismatch)
        ));
        assert_eq!(
            fs::read(directory.path().join("backup.partial")).expect("read unchanged source"),
            b"candidate"
        );
    }

    #[cfg(any(target_os = "macos", windows))]
    #[test]
    fn file_publication_rejects_second_hard_link() {
        let directory = TestDirectory::new();
        let parent = directory.parent_handle();
        let staged = directory.create_file("backup.partial", b"candidate");
        std::fs::hard_link(
            directory.path().join("backup.partial"),
            directory.path().join("hostile-alias"),
        )
        .expect("create hostile second link");

        let result = publish_file_noreplace(
            &parent,
            &staged,
            &component("backup.partial"),
            &component("backup.age"),
        );

        assert!(matches!(
            result,
            Err(PublishFileNoreplaceError::IdentityMismatch)
        ));
        assert!(directory.path().join("backup.partial").is_file());
        assert!(directory.path().join("hostile-alias").is_file());
        assert!(!directory.path().join("backup.age").exists());
    }

    #[cfg(any(target_os = "macos", windows))]
    #[test]
    fn directory_publication_moves_the_bound_directory_without_replacement() {
        let directory = TestDirectory::new();
        let parent = directory.parent_handle();
        let staged = directory.create_directory("restore.partial");
        fs::write(
            directory.path().join("restore.partial/foundation.sqlite3"),
            b"database",
        )
        .expect("write staged child");

        publish_directory_noreplace(
            &parent,
            &staged,
            &component("restore.partial"),
            &component("restored"),
        )
        .expect("publish absent final directory");

        assert!(!directory.path().join("restore.partial").exists());
        assert_eq!(
            fs::read(directory.path().join("restored/foundation.sqlite3"))
                .expect("read published child"),
            b"database"
        );
    }

    #[cfg(any(target_os = "macos", windows))]
    #[test]
    fn directory_publication_collision_preserves_both_directories() {
        let directory = TestDirectory::new();
        let parent = directory.parent_handle();
        let staged = directory.create_directory("restore.partial");
        let _existing = directory.create_directory("restored");
        fs::write(directory.path().join("restored/marker"), b"winner")
            .expect("write collision marker");

        let result = publish_directory_noreplace(
            &parent,
            &staged,
            &component("restore.partial"),
            &component("restored"),
        );

        assert!(matches!(
            result,
            Err(PublishDirectoryNoreplaceError::Collision)
        ));
        assert!(directory.path().join("restore.partial").is_dir());
        assert_eq!(
            fs::read(directory.path().join("restored/marker")).expect("read winner marker"),
            b"winner"
        );
    }

    #[cfg(any(target_os = "macos", windows))]
    #[test]
    fn directory_publication_rejects_file_source() {
        let directory = TestDirectory::new();
        let parent = directory.parent_handle();
        let staged = directory.create_file("restore.partial", b"not a directory");

        let result = publish_directory_noreplace(
            &parent,
            &staged,
            &component("restore.partial"),
            &component("restored"),
        );

        assert!(matches!(
            result,
            Err(PublishDirectoryNoreplaceError::IdentityMismatch)
        ));
        assert!(directory.path().join("restore.partial").is_file());
        assert!(!directory.path().join("restored").exists());
    }

    #[cfg(any(target_os = "macos", windows))]
    #[test]
    fn directory_publication_rejects_equal_names_before_mutation() {
        let directory = TestDirectory::new();
        let parent = directory.parent_handle();
        let staged = directory.create_directory("restore.partial");
        let name = component("restore.partial");

        let result = publish_directory_noreplace(&parent, &staged, &name, &name);

        assert!(matches!(
            result,
            Err(PublishDirectoryNoreplaceError::IdentityMismatch)
        ));
        assert!(directory.path().join("restore.partial").is_dir());
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn macos_raw_errors_map_to_the_frozen_typed_outcomes() {
        use rustix::io::Errno;

        assert!(matches!(
            map_macos_file_error(Errno::EXIST),
            PublishFileNoreplaceError::Collision
        ));
        assert!(matches!(
            map_macos_file_error(Errno::NOSYS),
            PublishFileNoreplaceError::Unsupported
        ));
        assert!(matches!(
            map_macos_file_error(Errno::XDEV),
            PublishFileNoreplaceError::IdentityMismatch
        ));
        assert!(matches!(
            map_macos_file_error(Errno::IO),
            PublishFileNoreplaceError::Io(_)
        ));

        assert!(matches!(
            map_macos_directory_error(Errno::EXIST),
            PublishDirectoryNoreplaceError::Collision
        ));
        assert!(matches!(
            map_macos_directory_error(Errno::INVAL),
            PublishDirectoryNoreplaceError::Unsupported
        ));
        assert!(matches!(
            map_macos_directory_error(Errno::XDEV),
            PublishDirectoryNoreplaceError::IdentityMismatch
        ));
        assert!(matches!(
            map_macos_directory_error(Errno::IO),
            PublishDirectoryNoreplaceError::Io(_)
        ));
    }

    #[cfg(windows)]
    #[test]
    fn windows_name_rejects_ads_and_preserves_other_wtf16() {
        use std::os::windows::ffi::{OsStrExt, OsStringExt};

        assert!(OneComponentName::try_from(OsString::from("stream:name")).is_err());

        let raw = [b'n' as u16, 0xd800, b'm' as u16];
        let name = OneComponentName::try_from(OsString::from_wide(&raw))
            .expect("non-NUL non-colon WTF-16 is valid");
        assert_eq!(name.as_os_str().encode_wide().collect::<Vec<_>>(), raw);
    }

    #[cfg(windows)]
    #[test]
    fn windows_buffer_layout_counts_one_terminator_and_excludes_it_from_name_length() {
        use windows_sys::Win32::Storage::FileSystem::FILE_RENAME_INFO;

        let layout = windows_rename_buffer_layout(3).expect("three-unit layout");
        let offset = std::mem::offset_of!(FILE_RENAME_INFO, FileName);

        assert_eq!(layout.file_name_byte_length, 6);
        assert_eq!(layout.buffer_byte_length, offset + 8);
        assert!(layout.allocation_element_count >= 1);
    }

    #[cfg(windows)]
    #[test]
    fn windows_strong_identity_rejects_each_missing_field() {
        assert!(windows_identity_from_parts(None, None).is_none());
        assert!(windows_identity_from_parts(Some(7), None).is_none());
        assert!(windows_identity_from_parts(None, Some(9)).is_none());
        assert_eq!(
            windows_identity_from_parts(Some(7), Some(9)),
            Some(WindowsIdentity {
                volume_serial_number: 7,
                file_index: 9,
            })
        );
    }

    #[cfg(windows)]
    #[test]
    fn windows_file_link_count_requires_an_available_single_link() {
        assert!(!windows_has_one_link(None));
        assert!(!windows_has_one_link(Some(0)));
        assert!(windows_has_one_link(Some(1)));
        assert!(!windows_has_one_link(Some(2)));
    }

    #[cfg(windows)]
    fn windows_observation_sequence(
        kind: WindowsPublicationSourceKind,
    ) -> Vec<WindowsMetadataObservation> {
        use windows_sys::Win32::Storage::FileSystem::FILE_ATTRIBUTE_DIRECTORY;

        let parent = WindowsMetadataObservation::Available(WindowsMetadataFields {
            file_attributes: FILE_ATTRIBUTE_DIRECTORY,
            volume_serial_number: Some(7),
            number_of_links: Some(1),
            file_index: Some(11),
        });
        let source = WindowsMetadataObservation::Available(WindowsMetadataFields {
            file_attributes: match kind {
                WindowsPublicationSourceKind::File => 0,
                WindowsPublicationSourceKind::Directory => FILE_ATTRIBUTE_DIRECTORY,
            },
            volume_serial_number: Some(7),
            number_of_links: Some(1),
            file_index: Some(13),
        });

        vec![parent, source, parent, source, source, parent]
    }

    #[cfg(windows)]
    fn set_windows_observation_identity(
        observation: &mut WindowsMetadataObservation,
        volume_serial_number: Option<u32>,
        file_index: Option<u64>,
    ) {
        let WindowsMetadataObservation::Available(fields) = observation else {
            panic!("test observation must contain fields");
        };
        fields.volume_serial_number = volume_serial_number;
        fields.file_index = file_index;
    }

    #[cfg(windows)]
    fn set_windows_observation_links(
        observation: &mut WindowsMetadataObservation,
        number_of_links: Option<u32>,
    ) {
        let WindowsMetadataObservation::Available(fields) = observation else {
            panic!("test observation must contain fields");
        };
        fields.number_of_links = number_of_links;
    }

    #[cfg(windows)]
    fn set_windows_observation_reparse(observation: &mut WindowsMetadataObservation) {
        use windows_sys::Win32::Storage::FileSystem::FILE_ATTRIBUTE_REPARSE_POINT;

        let WindowsMetadataObservation::Available(fields) = observation else {
            panic!("test observation must contain fields");
        };
        fields.file_attributes |= FILE_ATTRIBUTE_REPARSE_POINT;
    }

    #[cfg(windows)]
    fn flip_windows_observation_type(observation: &mut WindowsMetadataObservation) {
        use windows_sys::Win32::Storage::FileSystem::FILE_ATTRIBUTE_DIRECTORY;

        let WindowsMetadataObservation::Available(fields) = observation else {
            panic!("test observation must contain fields");
        };
        fields.file_attributes ^= FILE_ATTRIBUTE_DIRECTORY;
    }

    #[cfg(windows)]
    fn assert_windows_file_observations_rejected(observations: Vec<WindowsMetadataObservation>) {
        let directory = TestDirectory::new();
        let parent = directory.parent_handle();
        let staged = directory.create_file("backup.partial", b"candidate");
        let mut observations = observations.into_iter();

        let result = publish_file_noreplace_windows_with_extractor(
            &parent,
            &staged,
            &component("backup.partial"),
            &component("backup.age"),
            |_| {
                windows_handle_facts_from_observation(
                    observations
                        .next()
                        .expect("one observation per inspected handle"),
                )
            },
        );

        assert!(matches!(
            result,
            Err(PublishFileNoreplaceError::IdentityMismatch)
        ));
        assert!(directory.path().join("backup.partial").is_file());
        assert!(!directory.path().join("backup.age").exists());
    }

    #[cfg(windows)]
    fn assert_windows_directory_observations_rejected(
        observations: Vec<WindowsMetadataObservation>,
    ) {
        let directory = TestDirectory::new();
        let parent = directory.parent_handle();
        let staged = directory.create_directory("restore.partial");
        let mut observations = observations.into_iter();

        let result = publish_directory_noreplace_windows_with_extractor(
            &parent,
            &staged,
            &component("restore.partial"),
            &component("restored"),
            |_| {
                windows_handle_facts_from_observation(
                    observations
                        .next()
                        .expect("one observation per inspected handle"),
                )
            },
        );

        assert!(matches!(
            result,
            Err(PublishDirectoryNoreplaceError::IdentityMismatch)
        ));
        assert!(directory.path().join("restore.partial").is_dir());
        assert!(!directory.path().join("restored").exists());
    }

    #[cfg(windows)]
    #[test]
    fn windows_metadata_query_failure_is_identity_mismatch_for_every_inspected_handle() {
        for position in 0..6 {
            let mut file = windows_observation_sequence(WindowsPublicationSourceKind::File);
            file[position] = WindowsMetadataObservation::Unavailable;
            assert_windows_file_observations_rejected(file);

            let mut directory =
                windows_observation_sequence(WindowsPublicationSourceKind::Directory);
            directory[position] = WindowsMetadataObservation::Unavailable;
            assert_windows_directory_observations_rejected(directory);
        }
    }

    #[cfg(windows)]
    #[test]
    fn windows_missing_identity_fields_are_rejected_for_every_inspected_handle() {
        for position in 0..6 {
            for (volume_serial_number, file_index) in
                [(None, None), (None, Some(13)), (Some(7), None)]
            {
                let mut file = windows_observation_sequence(WindowsPublicationSourceKind::File);
                set_windows_observation_identity(
                    &mut file[position],
                    volume_serial_number,
                    file_index,
                );
                assert_windows_file_observations_rejected(file);

                let mut directory =
                    windows_observation_sequence(WindowsPublicationSourceKind::Directory);
                set_windows_observation_identity(
                    &mut directory[position],
                    volume_serial_number,
                    file_index,
                );
                assert_windows_directory_observations_rejected(directory);
            }
        }
    }

    #[cfg(windows)]
    #[test]
    fn windows_reparse_state_is_rejected_for_every_inspected_handle() {
        for position in 0..6 {
            let mut file = windows_observation_sequence(WindowsPublicationSourceKind::File);
            set_windows_observation_reparse(&mut file[position]);
            assert_windows_file_observations_rejected(file);

            let mut directory =
                windows_observation_sequence(WindowsPublicationSourceKind::Directory);
            set_windows_observation_reparse(&mut directory[position]);
            assert_windows_directory_observations_rejected(directory);
        }
    }

    #[cfg(windows)]
    #[test]
    fn windows_type_state_is_rejected_for_every_inspected_handle() {
        for position in 0..6 {
            let mut file = windows_observation_sequence(WindowsPublicationSourceKind::File);
            flip_windows_observation_type(&mut file[position]);
            assert_windows_file_observations_rejected(file);

            let mut directory =
                windows_observation_sequence(WindowsPublicationSourceKind::Directory);
            flip_windows_observation_type(&mut directory[position]);
            assert_windows_directory_observations_rejected(directory);
        }
    }

    #[cfg(windows)]
    #[test]
    fn windows_file_link_policy_rejects_missing_zero_and_multiple_links() {
        for position in [1, 3, 4] {
            for number_of_links in [None, Some(0), Some(2)] {
                let mut observations =
                    windows_observation_sequence(WindowsPublicationSourceKind::File);
                set_windows_observation_links(&mut observations[position], number_of_links);
                assert_windows_file_observations_rejected(observations);
            }
        }
    }

    #[cfg(windows)]
    #[test]
    fn windows_file_link_drift_from_one_to_two_is_rejected_before_mutation() {
        let mut observations = windows_observation_sequence(WindowsPublicationSourceKind::File);
        set_windows_observation_links(&mut observations[4], Some(2));

        assert_windows_file_observations_rejected(observations);
    }

    #[cfg(windows)]
    #[test]
    fn windows_open_specs_freeze_exact_subject_parent_and_binding_masks() {
        use cap_fs_ext::FollowSymlinks;
        use windows_sys::Win32::Foundation::{GENERIC_READ, GENERIC_WRITE};
        use windows_sys::Win32::Storage::FileSystem::{
            DELETE, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_READ_ATTRIBUTES,
            FILE_SHARE_DELETE, FILE_SHARE_READ, FILE_SHARE_WRITE, READ_CONTROL, WRITE_DAC,
        };

        let file = windows_file_subject_open_spec();
        assert_eq!(
            file.access_mode,
            GENERIC_READ | GENERIC_WRITE | DELETE | READ_CONTROL | WRITE_DAC | FILE_READ_ATTRIBUTES
        );
        assert_eq!(file.share_mode, FILE_SHARE_READ | FILE_SHARE_WRITE);
        assert_eq!(file.custom_flags, FILE_FLAG_OPEN_REPARSE_POINT);

        let directory = windows_directory_subject_open_spec();
        assert_eq!(
            directory.access_mode,
            DELETE | READ_CONTROL | FILE_READ_ATTRIBUTES
        );
        assert_eq!(directory.share_mode, FILE_SHARE_READ | FILE_SHARE_WRITE);
        assert_eq!(
            directory.custom_flags,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT
        );

        let parent = windows_parent_open_spec();
        assert_eq!(
            parent.access_mode,
            GENERIC_READ | READ_CONTROL | FILE_READ_ATTRIBUTES
        );
        assert_eq!(parent.share_mode, FILE_SHARE_READ | FILE_SHARE_WRITE);
        assert_eq!(
            parent.custom_flags,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT
        );

        let file_binding = windows_binding_options_spec(WindowsPublicationSourceKind::File);
        assert_eq!(
            file_binding.open.access_mode,
            GENERIC_READ | READ_CONTROL | FILE_READ_ATTRIBUTES
        );
        assert_eq!(
            file_binding.open.share_mode,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
        );
        assert_eq!(file_binding.open.custom_flags, FILE_FLAG_OPEN_REPARSE_POINT);
        assert_eq!(file_binding.follow, FollowSymlinks::No);
        assert!(!file_binding.maybe_dir);

        let directory_binding =
            windows_binding_options_spec(WindowsPublicationSourceKind::Directory);
        assert_eq!(
            directory_binding.open.access_mode,
            GENERIC_READ | READ_CONTROL | FILE_READ_ATTRIBUTES
        );
        assert_eq!(
            directory_binding.open.share_mode,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE
        );
        assert_eq!(
            directory_binding.open.custom_flags,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT
        );
        assert_eq!(directory_binding.follow, FollowSymlinks::No);
        assert!(!directory_binding.maybe_dir);
    }

    #[cfg(windows)]
    #[test]
    fn windows_raw_errors_map_exactly_for_both_error_types() {
        for code in [80, 183] {
            assert!(matches!(
                map_windows_file_error(std::io::Error::from_raw_os_error(code)),
                PublishFileNoreplaceError::Collision
            ));
            assert!(matches!(
                map_windows_directory_error(std::io::Error::from_raw_os_error(code)),
                PublishDirectoryNoreplaceError::Collision
            ));
        }
        for code in [1, 50] {
            assert!(matches!(
                map_windows_file_error(std::io::Error::from_raw_os_error(code)),
                PublishFileNoreplaceError::Unsupported
            ));
            assert!(matches!(
                map_windows_directory_error(std::io::Error::from_raw_os_error(code)),
                PublishDirectoryNoreplaceError::Unsupported
            ));
        }
        assert!(matches!(
            map_windows_file_error(std::io::Error::from_raw_os_error(87)),
            PublishFileNoreplaceError::Io(_)
        ));
        assert!(matches!(
            map_windows_directory_error(std::io::Error::from_raw_os_error(87)),
            PublishDirectoryNoreplaceError::Io(_)
        ));
    }

    #[cfg(not(any(target_os = "macos", windows)))]
    #[test]
    fn unsupported_targets_return_typed_unsupported_without_mutation() {
        let directory = TestDirectory::new();
        let parent = directory.parent_handle();
        let staged_file = directory.create_file("backup.partial", b"candidate");
        let staged_directory = directory.create_directory("restore.partial");

        assert!(matches!(
            publish_file_noreplace(
                &parent,
                &staged_file,
                &component("backup.partial"),
                &component("backup.age"),
            ),
            Err(PublishFileNoreplaceError::Unsupported)
        ));
        assert!(matches!(
            publish_directory_noreplace(
                &parent,
                &staged_directory,
                &component("restore.partial"),
                &component("restored"),
            ),
            Err(PublishDirectoryNoreplaceError::Unsupported)
        ));
        assert!(directory.path().join("backup.partial").is_file());
        assert!(directory.path().join("restore.partial").is_dir());
        assert!(!directory.path().join("backup.age").exists());
        assert!(!directory.path().join("restored").exists());
    }
}
