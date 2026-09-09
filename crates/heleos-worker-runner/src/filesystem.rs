use crate::{FailureCode, HARD_BYTE_LIMIT, RunError, RunnerConfig};
use heleos_worker_protocol::{Task, Validated};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::os::unix::fs::MetadataExt;
use std::path::{Component, Path, PathBuf};
use std::time::Instant;

const MAX_SNAPSHOT_BYTES: u64 = 268_435_456;
const MAX_SNAPSHOT_FILES: usize = 100_000;

pub(crate) struct OwnedWorkspace {
    directory: Option<tempfile::TempDir>,
    path: PathBuf,
    identity: (u64, u64),
    handle: File,
}
impl OwnedWorkspace {
    pub fn create(root: &Path) -> Result<Self, RunError> {
        let directory = tempfile::Builder::new()
            .prefix("heleos-worker-")
            .tempdir_in(root)
            .map_err(|_| RunError::new(FailureCode::Io))?;
        let path = directory.path().to_path_buf();
        let metadata = fs::symlink_metadata(&path).map_err(|_| RunError::new(FailureCode::Io))?;
        let handle = File::open(&path).map_err(|_| RunError::new(FailureCode::Io))?;
        Ok(Self {
            directory: Some(directory),
            path,
            identity: (metadata.dev(), metadata.ino()),
            handle,
        })
    }
    pub fn path(&self) -> &Path {
        &self.path
    }
    pub fn ownership_record(&self) -> Result<Vec<u8>, RunError> {
        #[derive(serde::Serialize)]
        struct Record<'a> {
            schema: &'static str,
            run_directory: &'a Path,
            device: u64,
            inode: u64,
        }
        serde_json::to_vec(&Record {
            schema: "heleos.worker-run-ownership/v1",
            run_directory: &self.path,
            device: self.identity.0,
            inode: self.identity.1,
        })
        .map_err(|_| RunError::new(FailureCode::InvalidConfiguration))
    }
    pub fn retain(mut self) {
        if let Some(directory) = self.directory.take() {
            let _ = directory.keep();
        }
    }
    pub fn validate(&self) -> Result<(), RunError> {
        let metadata =
            fs::symlink_metadata(&self.path).map_err(|_| RunError::new(FailureCode::UnsafePath))?;
        if !metadata.is_dir()
            || (metadata.dev(), metadata.ino()) != self.identity
            || fs::canonicalize(&self.path).map_err(|_| RunError::new(FailureCode::UnsafePath))?
                != self.path
        {
            return Err(RunError::new(FailureCode::UnsafePath));
        }
        Ok(())
    }
    pub fn write(&self, name: &str, bytes: &[u8]) -> Result<(), RunError> {
        use rustix::fs::{Mode, OFlags, openat};
        self.validate()?;
        // Fixed basenames, anchored directory handle, no replacement or symlink
        // following: provider-created evidence names cannot redirect our writes.
        if Path::new(name).components().count() != 1 {
            return Err(RunError::new(FailureCode::UnsafePath));
        }
        let descriptor = openat(
            &self.handle,
            name,
            OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW,
            Mode::RUSR | Mode::WUSR,
        )
        .map_err(|_| RunError::new(FailureCode::UnsafePath))?;
        let mut file = File::from(descriptor);
        file.write_all(bytes)
            .map_err(|_| RunError::new(FailureCode::Io))
    }
    pub fn verify(&self, name: &str, expected: &[u8]) -> Result<(), RunError> {
        use rustix::fs::{Mode, OFlags, openat};
        self.validate()?;
        let mismatch = || RunError::new(FailureCode::RepositoryMutation);
        let descriptor = openat(
            &self.handle,
            name,
            OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK,
            Mode::empty(),
        )
        .map_err(|_| mismatch())?;
        let file = File::from(descriptor);
        let metadata = file.metadata().map_err(|_| mismatch())?;
        if !metadata.is_file() || metadata.len() != expected.len() as u64 {
            return Err(mismatch());
        }
        let mut bytes = Vec::new();
        file.take(expected.len() as u64 + 1)
            .read_to_end(&mut bytes)
            .map_err(|_| mismatch())?;
        if bytes != expected {
            return Err(mismatch());
        }
        Ok(())
    }
    pub fn cleanup(mut self) -> Result<(), RunError> {
        self.validate()?;
        if let Some(directory) = self.directory.take() {
            directory
                .close()
                .map_err(|_| RunError::new(FailureCode::Io))?;
        }
        Ok(())
    }
}
// Early returns retain evidence. Only explicit identity-checked cleanup removes it.
impl Drop for OwnedWorkspace {
    fn drop(&mut self) {
        if let Some(directory) = self.directory.take() {
            let _ = directory.keep();
        }
    }
}

pub(crate) fn validate_config(config: &RunnerConfig) -> Result<(PathBuf, PathBuf), RunError> {
    let invalid = || RunError::new(FailureCode::InvalidConfiguration);
    for path in [
        &config.source_repository,
        &config.workspace_root,
        &config.git_executable,
        &config.provider.executable,
    ] {
        if !path.is_absolute() || path.components().any(|part| part == Component::ParentDir) {
            return Err(invalid());
        }
    }
    if config.max_output_bytes == 0
        || config.max_output_bytes > HARD_BYTE_LIMIT
        || config.max_prompt_bytes == 0
        || config.max_prompt_bytes > HARD_BYTE_LIMIT
        || config.provider.args.len() > 256
        || config
            .provider
            .args
            .iter()
            .map(|value| value.len())
            .sum::<usize>()
            > HARD_BYTE_LIMIT / 2
    {
        return Err(invalid());
    }
    let source = fs::canonicalize(&config.source_repository).map_err(|_| invalid())?;
    let root = fs::canonicalize(&config.workspace_root).map_err(|_| invalid())?;
    if source.starts_with(&root)
        || root.starts_with(&source)
        || !source.is_dir()
        || !root.is_dir()
        || !config.git_executable.is_file()
        || !config.provider.executable.is_file()
    {
        return Err(invalid());
    }
    Ok((source, root))
}

#[derive(Debug, Eq, PartialEq)]
pub(crate) struct FileIdentity {
    pub sha256: String,
    pub executable: bool,
}
pub(crate) type Snapshot = BTreeMap<String, FileIdentity>;

pub(crate) fn snapshot(
    root: &Path,
    skip_git: bool,
    deadline: Instant,
) -> Result<Snapshot, RunError> {
    let mut result = BTreeMap::new();
    let mut pending = vec![root.to_path_buf()];
    let mut total_bytes = 0u64;
    let mut directories = 0;
    while let Some(directory) = pending.pop() {
        directories += 1;
        if directories > MAX_SNAPSHOT_FILES {
            return Err(RunError::new(FailureCode::InventoryLimit));
        }
        check_deadline(deadline)?;
        if !fs::symlink_metadata(&directory)
            .map_err(|_| RunError::new(FailureCode::UnsafePath))?
            .is_dir()
        {
            return Err(RunError::new(FailureCode::UnsafePath));
        }
        for entry in fs::read_dir(directory).map_err(|_| RunError::new(FailureCode::UnsafePath))? {
            check_deadline(deadline)?;
            let entry = entry.map_err(|_| RunError::new(FailureCode::UnsafePath))?;
            let path = entry.path();
            let relative = path
                .strip_prefix(root)
                .map_err(|_| RunError::new(FailureCode::UnsafePath))?;
            if skip_git && relative == Path::new(".git") {
                continue;
            }
            let metadata =
                fs::symlink_metadata(&path).map_err(|_| RunError::new(FailureCode::UnsafePath))?;
            if metadata.is_dir() {
                pending.push(path);
                continue;
            }
            if !metadata.is_file() || metadata.nlink() > 1 {
                return Err(RunError::new(FailureCode::UnsafePath));
            }
            if result.len() == MAX_SNAPSHOT_FILES {
                return Err(RunError::new(FailureCode::InventoryLimit));
            }
            let name = relative
                .to_str()
                .ok_or_else(|| RunError::new(FailureCode::UnsafePath))?
                .to_owned();
            let mut file = File::open(&path).map_err(|_| RunError::new(FailureCode::UnsafePath))?;
            let opened = file
                .metadata()
                .map_err(|_| RunError::new(FailureCode::UnsafePath))?;
            if !opened.is_file() || opened.dev() != metadata.dev() || opened.ino() != metadata.ino()
            {
                return Err(RunError::new(FailureCode::UnsafePath));
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
                total_bytes += count as u64;
                if total_bytes > MAX_SNAPSHOT_BYTES {
                    return Err(RunError::new(FailureCode::InventoryLimit));
                }
                hasher.update(&buffer[..count]);
            }
            result.insert(
                name,
                FileIdentity {
                    sha256: hex(&hasher.finalize()),
                    executable: metadata.mode() & 0o111 != 0,
                },
            );
        }
    }
    Ok(result)
}

pub(crate) fn validate_git_metadata(before: &Snapshot, after: &Snapshot) -> Result<(), RunError> {
    for path in before.keys().chain(after.keys()) {
        if before.get(path) == after.get(path) {
            continue;
        }
        // Permit normal staging/committing, not configuration, alternate stores,
        // hooks, replacement refs, or changes to pre-existing Git objects.
        let mutable = matches!(path.as_str(), "index" | "HEAD" | "COMMIT_EDITMSG")
            || path.starts_with("logs/")
            || path.starts_with("refs/heads/");
        let new_object = path.starts_with("objects/")
            && !path.starts_with("objects/info/")
            && !before.contains_key(path);
        if !mutable && !new_object {
            return Err(RunError::new(FailureCode::RepositoryMutation));
        }
    }
    Ok(())
}

pub(crate) fn prompt(
    task: &Validated<Task>,
    checkout: &Path,
    limit: usize,
    deadline: Instant,
) -> Result<Vec<u8>, RunError> {
    let mut bytes = b"Repository-owned worker assignment. Make only the assigned proposal. Follow the verified instruction files listed below. Do not execute acceptance commands, commit, push, merge, change Git configuration, or access data outside the authorized task. Return after this single invocation. Internal tool-call limits remain your adapter's responsibility. Task content follows as JSON data.\n".to_vec();
    append_prompt(&mut bytes, task.canonical_json(), limit)?;
    for (path, expected) in &task.document().instruction_sha256 {
        check_deadline(deadline)?;
        let file_path = checkout.join(path);
        let mut ancestor = checkout.to_path_buf();
        for component in Path::new(path).components() {
            ancestor.push(component);
            let metadata = fs::symlink_metadata(&ancestor)
                .map_err(|_| RunError::new(FailureCode::InstructionMismatch))?;
            if metadata.file_type().is_symlink() {
                return Err(RunError::new(FailureCode::InstructionMismatch));
            }
        }
        let metadata = fs::symlink_metadata(&file_path)
            .map_err(|_| RunError::new(FailureCode::InstructionMismatch))?;
        if !metadata.is_file() {
            return Err(RunError::new(FailureCode::InstructionMismatch));
        }
        let mut content = Vec::new();
        File::open(&file_path)
            .map_err(|_| RunError::new(FailureCode::InstructionMismatch))?
            .take(limit as u64 + 1)
            .read_to_end(&mut content)
            .map_err(|_| RunError::new(FailureCode::InstructionMismatch))?;
        if content.len() > limit {
            return Err(RunError::new(FailureCode::PromptTooLarge));
        }
        if hex(&Sha256::digest(&content)) != *expected || std::str::from_utf8(&content).is_err() {
            return Err(RunError::new(FailureCode::InstructionMismatch));
        }
        append_prompt(
            &mut bytes,
            format!("\nVerified instruction file: {path}\n").as_bytes(),
            limit,
        )?;
        append_prompt(&mut bytes, &content, limit)?;
    }
    Ok(bytes)
}
fn append_prompt(output: &mut Vec<u8>, bytes: &[u8], limit: usize) -> Result<(), RunError> {
    if output.len().saturating_add(bytes.len()) > limit {
        return Err(RunError::new(FailureCode::PromptTooLarge));
    }
    output.extend_from_slice(bytes);
    Ok(())
}
fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}
pub(crate) fn nul_paths(bytes: &[u8]) -> Result<Vec<String>, RunError> {
    if !bytes.is_empty() && bytes.last() != Some(&0) {
        return Err(RunError::new(FailureCode::UnsafePath));
    }
    bytes
        .split(|byte| *byte == 0)
        .filter(|path| !path.is_empty())
        .map(|path| {
            std::str::from_utf8(path)
                .map(str::to_owned)
                .map_err(|_| RunError::new(FailureCode::UnsafePath))
        })
        .collect()
}
fn check_deadline(deadline: Instant) -> Result<(), RunError> {
    if Instant::now() >= deadline {
        Err(RunError::new(FailureCode::Timeout))
    } else {
        Ok(())
    }
}
