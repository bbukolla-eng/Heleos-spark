//! Safe command preparation and fail-closed Windows worker containment.
#![deny(unsafe_code)]

use std::{
    io::Read,
    path::{Path, PathBuf},
    time::Instant,
};

#[cfg(windows)]
#[allow(unsafe_code)]
mod sys;

/// Per-stream retained output and stdin are capped at 64 MiB.
pub const MAX_BYTES: usize = 64 * 1024 * 1024;
const MAX_WIDE: usize = 32767;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    InvalidInput,
    Unavailable,
    DeadlineExpired,
    Io(std::io::ErrorKind),
    Setup { stage: &'static str, code: u32 },
    Verification(&'static str),
    ReaderPanicked,
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {self:?}", self.code())
    }
}
impl std::error::Error for Error {}
impl From<std::io::Error> for Error {
    fn from(value: std::io::Error) -> Self {
        Self::Io(value.kind())
    }
}

fn valid_text(value: &str) -> Result<(), Error> {
    if value.contains('\0') || value.encode_utf16().count() >= MAX_WIDE {
        return Err(Error::InvalidInput);
    }
    Ok(())
}

/// Encodes literal arguments using Microsoft CRT backslash/quote rules.
/// The first element is argv[0]; callers still pass the executable separately.
pub fn command_line(args: &[String]) -> Result<Vec<u16>, Error> {
    if args.is_empty() {
        return Err(Error::InvalidInput);
    }
    let mut encoded = Vec::new();
    for (index, arg) in args.iter().enumerate() {
        valid_text(arg)?;
        if index != 0 {
            encoded.push(32);
        }
        encoded.push(34);
        let mut slashes = 0;
        for unit in arg.encode_utf16() {
            if unit == 92 {
                slashes += 1;
                continue;
            }
            encoded.extend(std::iter::repeat_n(
                92,
                if unit == 34 { slashes * 2 + 1 } else { slashes },
            ));
            slashes = 0;
            encoded.push(unit);
        }
        encoded.extend(std::iter::repeat_n(92, slashes * 2));
        encoded.push(34);
        if encoded.len() >= MAX_WIDE {
            return Err(Error::InvalidInput);
        }
    }
    encoded.push(0);
    Ok(encoded)
}

#[cfg(not(windows))]
fn environment_key(value: &str) -> Vec<u16> {
    value.to_uppercase().encode_utf16().collect()
}

/// An explicit, sorted Unicode environment. No ambient variables are included.
pub fn environment_block(env: &[(String, String)]) -> Result<Vec<u16>, Error> {
    let mut entries = Vec::with_capacity(env.len());
    for (name, value) in env {
        valid_text(name)?;
        valid_text(value)?;
        if name.is_empty() || name.contains('=') {
            return Err(Error::InvalidInput);
        }
        entries.push((name, value));
    }
    #[cfg(windows)]
    sys::sort_environment(&mut entries)?;
    #[cfg(not(windows))]
    {
        entries.sort_by_cached_key(|(name, _)| environment_key(name));
        if entries
            .windows(2)
            .any(|pair| environment_key(pair[0].0) == environment_key(pair[1].0))
        {
            return Err(Error::InvalidInput);
        }
    }
    let mut block = Vec::new();
    for (name, value) in entries {
        block.extend(name.encode_utf16());
        block.push(61);
        block.extend(value.encode_utf16());
        block.push(0);
        if block.len() >= MAX_WIDE {
            return Err(Error::InvalidInput);
        }
    }
    if block.is_empty() {
        block.push(0);
    }
    block.push(0);
    Ok(block)
}

#[derive(Debug, PartialEq, Eq)]
pub struct Capture {
    pub bytes: Vec<u8>,
    pub total_bytes: u64,
    pub truncated: bool,
}
pub fn capture(mut reader: impl Read, limit: usize) -> Result<Capture, Error> {
    valid_limit(limit)?;
    let mut result = Capture {
        bytes: Vec::new(),
        total_bytes: 0,
        truncated: false,
    };
    let mut buffer = [0; 8192];
    loop {
        let count = match reader.read(&mut buffer) {
            Ok(0) => break,
            Ok(count) => count,
            Err(error) if error.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(error) => return Err(error.into()),
        };
        result.total_bytes = result.total_bytes.saturating_add(count as u64);
        let keep = count.min(limit - result.bytes.len());
        result.bytes.extend_from_slice(&buffer[..keep]);
        result.truncated |= keep != count;
    }
    Ok(result)
}

fn valid_limit(limit: usize) -> Result<(), Error> {
    if limit == 0 || limit > MAX_BYTES {
        Err(Error::InvalidInput)
    } else {
        Ok(())
    }
}

impl Error {
    pub fn code(&self) -> &'static str {
        match self {
            Self::InvalidInput => "invalid_input",
            Self::Unavailable => "containment_unavailable",
            Self::DeadlineExpired => "deadline_expired",
            Self::Io(_) => "io_error",
            Self::Setup { .. } => "containment_setup_failed",
            Self::Verification(_) => "containment_verification_failed",
            Self::ReaderPanicked => "pipe_thread_failed",
        }
    }
}

/// Exactly three already-created directories; prepare before materialization.
#[derive(Debug)]
pub struct WritableRoots {
    paths: [PathBuf; 3],
    #[cfg(windows)]
    guard: sys::Roots,
}

impl WritableRoots {
    pub fn prepare(checkout: &Path, home: &Path, tmp: &Path) -> Result<Self, Error> {
        let paths = [
            validate_path(checkout, true)?,
            validate_path(home, true)?,
            validate_path(tmp, true)?,
        ];
        for (i, path) in paths.iter().enumerate() {
            if std::fs::read_dir(path)?.next().transpose()?.is_some() {
                return Err(Error::InvalidInput);
            }
            for other in &paths[i + 1..] {
                if path.starts_with(other) || other.starts_with(path) {
                    return Err(Error::InvalidInput);
                }
            }
        }
        #[cfg(windows)]
        let guard = sys::Roots::prepare(&paths)?;
        Ok(Self {
            paths,
            #[cfg(windows)]
            guard,
        })
    }
    pub fn paths(&self) -> &[PathBuf; 3] {
        &self.paths
    }
}

#[cfg_attr(not(windows), allow(dead_code))]
#[derive(Debug)]
pub struct CommandSpec {
    executable: PathBuf,
    arguments: Vec<String>,
    directory: PathBuf,
    environment: Vec<(String, String)>,
    deadline: Instant,
    output_limit: usize,
    roots: WritableRoots,
}

impl CommandSpec {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        executable: PathBuf,
        arguments: Vec<String>,
        directory: PathBuf,
        environment: Vec<(String, String)>,
        deadline: Instant,
        output_limit: usize,
        roots: WritableRoots,
    ) -> Result<Self, Error> {
        let executable = validate_path(&executable, false)?;
        if executable
            .extension()
            .and_then(|v| v.to_str())
            .is_some_and(|ext| ext.eq_ignore_ascii_case("cmd") || ext.eq_ignore_ascii_case("bat"))
        {
            return Err(Error::InvalidInput);
        }
        let directory = validate_path(&directory, true)?;
        if !directory.starts_with(&roots.paths[0]) {
            return Err(Error::InvalidInput);
        }
        valid_limit(output_limit)?;
        if deadline <= Instant::now() {
            return Err(Error::DeadlineExpired);
        }
        let mut argv = vec![executable.to_str().ok_or(Error::InvalidInput)?.into()];
        argv.extend(arguments.iter().cloned());
        command_line(&argv)?;
        environment_block(&environment)?;
        Ok(Self {
            executable,
            arguments,
            directory,
            environment,
            deadline,
            output_limit,
            roots,
        })
    }
}

fn validate_path(path: &Path, directory: bool) -> Result<PathBuf, Error> {
    if !path.is_absolute() {
        return Err(Error::InvalidInput);
    }
    valid_text(path.to_str().ok_or(Error::InvalidInput)?)?;
    for ancestor in path.ancestors() {
        let metadata = std::fs::symlink_metadata(ancestor)?;
        if metadata.file_type().is_symlink() {
            return Err(Error::InvalidInput);
        }
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if metadata.file_attributes()
                & windows_sys::Win32::Storage::FileSystem::FILE_ATTRIBUTE_REPARSE_POINT
                != 0
            {
                return Err(Error::InvalidInput);
            }
        }
    }
    let metadata = std::fs::metadata(path)?;
    if if directory {
        !metadata.is_dir()
    } else {
        !metadata.is_file()
    } {
        return Err(Error::InvalidInput);
    }
    let canonical = path.canonicalize()?;
    #[cfg(windows)]
    {
        use std::path::{Component, Prefix};
        if !matches!(canonical.components().next(), Some(Component::Prefix(prefix)) if matches!(prefix.kind(), Prefix::Disk(_) | Prefix::VerbatimDisk(_)))
        {
            return Err(Error::InvalidInput);
        }
    }
    Ok(canonical)
}

#[derive(Debug)]
pub struct Outcome {
    pub exit_code: u32,
    pub timed_out: bool,
    pub stdout: Capture,
    pub stderr: Capture,
}

/// Launches only the Windows restricted-token/Job backend. Never falls back to
/// an ordinary process. Stdin is explicit and bounded independently of output.
pub fn execute(spec: CommandSpec, stdin: Vec<u8>) -> Result<Outcome, Error> {
    if stdin.len() > MAX_BYTES {
        return Err(Error::InvalidInput);
    }
    if spec.deadline <= Instant::now() {
        return Err(Error::DeadlineExpired);
    }
    #[cfg(windows)]
    {
        sys::execute(spec, stdin)
    }
    #[cfg(not(windows))]
    {
        Err(Error::Unavailable)
    }
}
