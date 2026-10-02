//! Safe Windows opened-handle identity and reparse-aware inventory.
use super::{FileIdentity, MAX_SNAPSHOT_BYTES, MAX_SNAPSHOT_FILES, Snapshot, check_deadline, hex};
use crate::{FailureCode, RunError};
use cap_primitives::fs::{_WindowsByHandle, Metadata as CapMetadata};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::Read,
    os::windows::fs::{MetadataExt, OpenOptionsExt},
    path::{Component, Path, Prefix},
    time::Instant,
};
use windows_sys::Win32::Storage::FileSystem::{
    FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT,
    FILE_SHARE_READ, FILE_SHARE_WRITE,
};

fn unsafe_path() -> RunError {
    RunError::new(FailureCode::UnsafePath)
}

pub(super) fn no_reparse_components(path: &Path) -> Result<(), RunError> {
    let mut components = path.components();
    if !matches!(components.next(), Some(Component::Prefix(prefix))
        if matches!(prefix.kind(), Prefix::Disk(_) | Prefix::VerbatimDisk(_)))
        || !path.is_absolute()
    {
        return Err(unsafe_path());
    }
    let mut current = std::path::PathBuf::new();
    for component in path.components() {
        if matches!(component, Component::CurDir | Component::ParentDir) {
            return Err(unsafe_path());
        }
        current.push(component);
        if matches!(component, Component::Prefix(_)) {
            continue;
        }
        let metadata = fs::symlink_metadata(&current).map_err(|_| unsafe_path())?;
        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return Err(unsafe_path());
        }
    }
    Ok(())
}

pub(super) fn open(path: &Path, directory: bool) -> Result<File, RunError> {
    no_reparse_components(path)?;
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(
            FILE_FLAG_OPEN_REPARSE_POINT
                | if directory {
                    FILE_FLAG_BACKUP_SEMANTICS
                } else {
                    0
                },
        )
        // Retained handles deny deletion/renaming while an object is inspected.
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .open(path)
        .map_err(|_| unsafe_path())?;
    let facts = facts(&file)?;
    if facts.directory != directory || !directory && !facts.file {
        return Err(unsafe_path());
    }
    Ok(file)
}

struct Facts {
    identity: (u64, u64),
    directory: bool,
    file: bool,
    links: u32,
}
fn facts(file: &File) -> Result<Facts, RunError> {
    let metadata = CapMetadata::from_file(file).map_err(|_| unsafe_path())?;
    if _WindowsByHandle::file_attributes(&metadata) & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return Err(unsafe_path());
    }
    let volume = _WindowsByHandle::volume_serial_number(&metadata).ok_or_else(unsafe_path)?;
    let index = _WindowsByHandle::file_index(&metadata).ok_or_else(unsafe_path)?;
    let links = _WindowsByHandle::number_of_links(&metadata).ok_or_else(unsafe_path)?;
    Ok(Facts {
        identity: (u64::from(volume), index),
        directory: metadata.is_dir(),
        file: metadata.is_file(),
        links,
    })
}

pub(super) fn identity(file: &File) -> Result<(u64, u64), RunError> {
    Ok(facts(file)?.identity)
}

pub(super) fn regular(file: &File) -> Result<(), RunError> {
    let facts = facts(file)?;
    if !facts.file || facts.directory || facts.links != 1 {
        return Err(unsafe_path());
    }
    Ok(())
}

pub(super) fn create_evidence(root: &Path, name: &str) -> Result<File, RunError> {
    if !matches!(
        Path::new(name).components().next(),
        Some(Component::Normal(_))
    ) || Path::new(name).components().count() != 1
        || name.contains(':')
    {
        return Err(unsafe_path());
    }
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .share_mode(FILE_SHARE_READ)
        .open(root.join(name))
        .map_err(|_| unsafe_path())
}

pub(super) fn snapshot(
    root: &Path,
    skip_git: bool,
    deadline: Instant,
) -> Result<Snapshot, RunError> {
    let root_handle = open(root, true)?;
    let root_identity = identity(&root_handle)?;
    let mut result = Snapshot::new();
    let mut pending = vec![root.to_path_buf()];
    let mut total_bytes = 0u64;
    let mut directory_count = 0;
    while let Some(directory) = pending.pop() {
        check_deadline(deadline)?;
        directory_count += 1;
        if directory_count > MAX_SNAPSHOT_FILES {
            return Err(RunError::new(FailureCode::InventoryLimit));
        }
        let held_directory = open(&directory, true)?;
        let directory_identity = identity(&held_directory)?;
        for entry in fs::read_dir(&directory).map_err(|_| unsafe_path())? {
            check_deadline(deadline)?;
            let path = entry.map_err(|_| unsafe_path())?.path();
            let relative = path.strip_prefix(root).map_err(|_| unsafe_path())?;
            if skip_git && relative == Path::new(".git") {
                continue;
            }
            let name = relative_name(relative)?;
            let metadata = fs::symlink_metadata(&path).map_err(|_| unsafe_path())?;
            if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
                return Err(unsafe_path());
            }
            if metadata.is_dir() {
                pending.push(path);
                continue;
            }
            if !metadata.is_file() {
                return Err(unsafe_path());
            }
            if result.len() == MAX_SNAPSHOT_FILES {
                return Err(RunError::new(FailureCode::InventoryLimit));
            }
            let mut file = open(&path, false)?;
            regular(&file)?;
            let before = identity(&file)?;
            if identity(&open(&path, false)?)? != before {
                return Err(unsafe_path());
            }
            let mut hasher = Sha256::new();
            let mut buffer = [0u8; 16_384];
            loop {
                check_deadline(deadline)?;
                let count = file
                    .read(&mut buffer)
                    .map_err(|_| RunError::new(FailureCode::Io))?;
                if count == 0 {
                    break;
                }
                total_bytes = total_bytes.saturating_add(count as u64);
                if total_bytes > MAX_SNAPSHOT_BYTES {
                    return Err(RunError::new(FailureCode::InventoryLimit));
                }
                hasher.update(&buffer[..count]);
            }
            regular(&file)?;
            if identity(&file)? != before || identity(&open(&path, false)?)? != before {
                return Err(unsafe_path());
            }
            result.insert(
                name,
                FileIdentity {
                    sha256: hex(&hasher.finalize()),
                    executable: false,
                },
            );
        }
        if identity(&open(&directory, true)?)? != directory_identity {
            return Err(unsafe_path());
        }
    }
    if identity(&open(root, true)?)? != root_identity {
        return Err(unsafe_path());
    }
    Ok(result)
}

fn relative_name(path: &Path) -> Result<String, RunError> {
    let mut names = Vec::new();
    for component in path.components() {
        let Component::Normal(name) = component else {
            return Err(unsafe_path());
        };
        let name = name.to_str().ok_or_else(unsafe_path)?;
        if name.is_empty()
            || name.trim() != name
            || name.ends_with('.')
            || name
                .chars()
                .any(|ch| ch.is_control() || ":*?\"<>|\\/".contains(ch))
        {
            return Err(unsafe_path());
        }
        names.push(name);
    }
    let name = names.join("/");
    if name.is_empty() || name.len() > heleos_worker_protocol::MAX_PATH_BYTES {
        return Err(unsafe_path());
    }
    Ok(name)
}
