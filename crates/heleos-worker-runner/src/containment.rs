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
    #[cfg(windows)]
    return if mode == ContainmentMode::WindowsRestrictedTokenJob {
        Ok(())
    } else {
        Err(RunError::new(FailureCode::ContainmentUnavailable))
    };
    #[cfg(not(windows))]
    if mode == ContainmentMode::WindowsRestrictedTokenJob {
        return Err(RunError::new(FailureCode::ContainmentUnavailable));
    }
    #[cfg(not(windows))]
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
    #[cfg(not(any(target_os = "macos", windows)))]
    Err(RunError::new(FailureCode::ContainmentUnavailable))
}

#[cfg(unix)]
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

#[cfg(windows)]
pub(crate) fn prepare_windows_roots(
    run: &Path,
) -> Result<heleos_worker_windows::WritableRoots, RunError> {
    let checkout = run.join("checkout");
    let home = run.join("home");
    let tmp = run.join("tmp");
    for path in [&checkout, &home, &tmp] {
        std::fs::create_dir(path).map_err(|_| RunError::new(FailureCode::Io))?;
    }
    // ACLs must precede clone/materialization; the evidence parent is excluded.
    heleos_worker_windows::WritableRoots::prepare(&checkout, &home, &tmp).map_err(windows_error)
}

#[cfg(windows)]
pub(crate) fn windows_error(error: heleos_worker_windows::Error) -> RunError {
    use heleos_worker_windows::Error;
    RunError::new(match error {
        Error::InvalidInput => FailureCode::InvalidConfiguration,
        Error::DeadlineExpired => FailureCode::Timeout,
        Error::Io(_) | Error::ReaderPanicked => FailureCode::Io,
        Error::Unavailable | Error::Setup { .. } | Error::Verification(_) => {
            FailureCode::ContainmentUnavailable
        }
    })
}

#[cfg(windows)]
fn platform_environment(
    config: &RunnerConfig,
    additions: Vec<(String, String)>,
) -> Result<Vec<(String, String)>, RunError> {
    // Only these fixed platform values are read; ambient credentials, PATH,
    // profile state, and provider configuration are never inherited wholesale.
    let invalid = || RunError::new(FailureCode::InvalidConfiguration);
    let directory = |path: &Path| -> Result<String, RunError> {
        let canonical = path.canonicalize().map_err(|_| invalid())?;
        if !canonical.is_dir() {
            return Err(invalid());
        }
        let value = canonical.to_str().ok_or_else(invalid)?;
        // PATH/SystemRoot use the ordinary spelling of a canonical local path.
        Ok(value.strip_prefix(r"\\?\").unwrap_or(value).to_owned())
    };
    let system_root = directory(Path::new(
        &std::env::var("SystemRoot").map_err(|_| invalid())?,
    ))?;
    let git = config
        .git_executable
        .canonicalize()
        .map_err(|_| invalid())?;
    let provider = config
        .provider
        .executable
        .canonicalize()
        .map_err(|_| invalid())?;
    windows_environment_entries(
        &system_root,
        &directory(git.parent().ok_or_else(invalid)?)?,
        &directory(provider.parent().ok_or_else(invalid)?)?,
        additions,
    )
}

#[cfg(windows)]
pub(crate) fn controller_environment(
    config: &RunnerConfig,
    command: &mut Command,
) -> Result<(), RunError> {
    command.envs(platform_environment(config, vec![])?);
    Ok(())
}

#[cfg(windows)]
pub(crate) fn execute_windows_provider(
    config: &RunnerConfig,
    checkout: &Path,
    run: &Path,
    roots: heleos_worker_windows::WritableRoots,
    prompt: &[u8],
    deadline: std::time::Instant,
) -> Result<crate::process::ProcessOutput, RunError> {
    validate(config.containment)?;
    let invalid = || RunError::new(FailureCode::InvalidConfiguration);
    let mut environment = Vec::new();
    let home = run.join("home").to_str().ok_or_else(invalid)?.to_owned();
    let tmp = run.join("tmp").to_str().ok_or_else(invalid)?.to_owned();
    environment.extend([
        ("HOME".into(), home.clone()),
        ("USERPROFILE".into(), home),
        ("TEMP".into(), tmp.clone()),
        ("TMP".into(), tmp.clone()),
        ("TMPDIR".into(), tmp),
    ]);
    for (name, value) in &config.provider.environment {
        environment.push((
            name.to_str().ok_or_else(invalid)?.to_owned(),
            value.to_str().ok_or_else(invalid)?.to_owned(),
        ));
    }
    let environment = platform_environment(config, environment)?;
    let arguments = config
        .provider
        .args
        .iter()
        .map(|value| value.to_str().map(str::to_owned).ok_or_else(invalid))
        .collect::<Result<Vec<_>, _>>()?;
    let spec = heleos_worker_windows::CommandSpec::new(
        config.provider.executable.clone(),
        arguments,
        checkout.to_owned(),
        environment,
        deadline,
        config.max_output_bytes,
        roots,
    )
    .map_err(windows_error)?;
    let outcome = heleos_worker_windows::execute(spec, prompt.to_vec()).map_err(windows_error)?;
    crate::process::from_windows_outcome(outcome)
}

#[cfg(any(windows, test))]
fn windows_environment_entries(
    system_root: &str,
    git_parent: &str,
    provider_parent: &str,
    additions: Vec<(String, String)>,
) -> Result<Vec<(String, String)>, RunError> {
    let invalid = || RunError::new(FailureCode::InvalidConfiguration);
    let system32 = format!("{}\\System32", system_root.trim_end_matches(['\\', '/']));
    let mut path = Vec::new();
    let mut directories = std::collections::BTreeSet::new();
    for directory in [system32.as_str(), git_parent, provider_parent] {
        if directory.is_empty() || directory.contains(['\0', '"']) {
            return Err(invalid());
        }
        if directories.insert(directory.to_uppercase()) {
            path.push(if directory.contains(';') {
                format!("\"{directory}\"")
            } else {
                directory.to_owned()
            });
        }
    }
    let mut entries = vec![
        ("SystemRoot".to_owned(), system_root.to_owned()),
        ("WINDIR".to_owned(), system_root.to_owned()),
        ("PATH".to_owned(), path.join(";")),
    ];
    let mut names = [
        "SYSTEMROOT".to_owned(),
        "WINDIR".to_owned(),
        "PATH".to_owned(),
    ]
    .into_iter()
    .collect::<std::collections::BTreeSet<_>>();
    for (name, value) in additions {
        if name.is_empty()
            || name.contains(['\0', '='])
            || value.contains('\0')
            || !names.insert(name.to_uppercase())
        {
            return Err(invalid());
        }
        entries.push((name, value));
    }
    Ok(entries)
}

#[cfg(test)]
mod windows_environment_tests {
    use super::*;

    #[test]
    fn windows_fixed_path_includes_git_and_provider_directories() {
        let entries = windows_environment_entries(
            r"C:\Windows",
            r"C:\Program Files\Git\cmd",
            r"D:\Tools\Provider",
            vec![],
        )
        .unwrap();
        assert_eq!(
            entries,
            vec![
                ("SystemRoot".to_owned(), r"C:\Windows".to_owned()),
                ("WINDIR".to_owned(), r"C:\Windows".to_owned()),
                (
                    "PATH".to_owned(),
                    r"C:\Windows\System32;C:\Program Files\Git\cmd;D:\Tools\Provider".to_owned()
                ),
            ]
        );
    }

    #[test]
    fn windows_inherited_fixed_environment_aliases_are_explicitly_rejected() {
        for name in ["PATH", "Path", "path", "SystemRoot", "SYSTEMROOT", "windir"] {
            let error = windows_environment_entries(
                r"C:\Windows",
                r"C:\Git",
                r"C:\Provider",
                vec![(name.to_owned(), "untrusted inherited value".to_owned())],
            )
            .unwrap_err();
            assert_eq!(error.code, FailureCode::InvalidConfiguration);
        }
        let entries = windows_environment_entries(
            r"C:\Windows",
            r"C:\Git",
            r"C:\Provider",
            vec![(
                "APPROVED_PROVIDER_SETTING".to_owned(),
                "synthetic".to_owned(),
            )],
        )
        .unwrap();
        assert_eq!(
            entries.last().unwrap(),
            &(
                "APPROVED_PROVIDER_SETTING".to_owned(),
                "synthetic".to_owned()
            )
        );
    }
}
