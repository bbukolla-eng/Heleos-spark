use crate::{ContainmentMode, FailureCode, RunError, RunnerConfig};
use std::path::Path;
use std::process::Command;

#[cfg(target_os = "macos")]
const BACKEND: &str = "/usr/bin/sandbox-exec";

// Task data never becomes profile syntax. Seatbelt resolves target paths, so
// symlinks within a permitted root do not grant access to outside targets.
#[cfg(target_os = "macos")]
const PROFILE: &str = r#"(version 1)
(allow default)
(deny file-write*
    (require-all
        (require-not (subpath (param "CHECKOUT")))
        (require-not (subpath (param "RUN_HOME")))
        (require-not (subpath (param "RUN_TMP")))))
"#;

pub(crate) fn validate(mode: ContainmentMode) -> Result<(), RunError> {
    if mode == ContainmentMode::None {
        return Ok(());
    }
    #[cfg(target_os = "macos")]
    {
        use std::os::unix::fs::MetadataExt;
        let unavailable = || RunError::new(FailureCode::ContainmentUnavailable);
        let path = Path::new(BACKEND);
        if path.canonicalize().map_err(|_| unavailable())? != path {
            return Err(unavailable());
        }
        let metadata = std::fs::symlink_metadata(path).map_err(|_| unavailable())?;
        if !metadata.is_file()
            || metadata.uid() != 0
            || metadata.mode() & 0o022 != 0
            || metadata.mode() & 0o111 == 0
        {
            return Err(unavailable());
        }
        Ok(())
    }
    #[cfg(not(target_os = "macos"))]
    Err(RunError::new(FailureCode::ContainmentUnavailable))
}

pub(crate) fn provider_command(
    config: &RunnerConfig,
    checkout: &Path,
    run_directory: &Path,
) -> Result<Command, RunError> {
    if config.containment == ContainmentMode::None {
        return Ok(Command::new(&config.provider.executable));
    }
    validate(config.containment)?;
    #[cfg(target_os = "macos")]
    {
        let mut command = Command::new(BACKEND);
        command.args(["-p", PROFILE]);
        for (name, path) in [
            ("CHECKOUT=", checkout.to_path_buf()),
            ("RUN_HOME=", run_directory.join("home")),
            ("RUN_TMP=", run_directory.join("tmp")),
        ] {
            let canonical = path
                .canonicalize()
                .map_err(|_| RunError::new(FailureCode::ContainmentUnavailable))?;
            let mut parameter = std::ffi::OsString::from(name);
            parameter.push(canonical);
            command.arg("-D").arg(parameter);
        }
        command.arg(&config.provider.executable);
        Ok(command)
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = (checkout, run_directory);
        Err(RunError::new(FailureCode::ContainmentUnavailable))
    }
}
