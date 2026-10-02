//! Native Windows/NTFS end-to-end evidence. Cross-compilation is compile-only.
#![cfg(windows)]

use heleos_worker_protocol::{Provider, validate_task_json};
use heleos_worker_runner::{ContainmentMode, FailureCode, ProviderCommand, RunnerConfig, run};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
    time::Duration,
};

struct Fixture {
    root: tempfile::TempDir,
    source: PathBuf,
    workspace: PathBuf,
    git: PathBuf,
    provider: PathBuf,
    packet: Value,
}

impl Fixture {
    fn new(mode: &str) -> (Self, RunnerConfig) {
        let root = tempfile::tempdir().unwrap();
        let source = root.path().join("source ; literal");
        let workspace = root.path().join("workspace ; (literal)");
        fs::create_dir(&source).unwrap();
        fs::create_dir(&workspace).unwrap();
        let git = PathBuf::from(
            std::env::var_os("HELEOS_NATIVE_GIT")
                .expect("native gate must provide an absolute HELEOS_NATIVE_GIT executable"),
        );
        assert!(git.is_absolute() && git.is_file());
        let provider = root.path().join("provider ; (literal).exe");
        fs::copy(
            env!("CARGO_BIN_EXE_heleos-worker-runner-test-provider"),
            &provider,
        )
        .unwrap();
        let git_run = |args: &[&str]| {
            let output = Command::new(&git)
                .current_dir(&source)
                .args(["-c", "core.hooksPath=NUL", "-c", "core.autocrlf=false"])
                .args(args)
                .env("GIT_CONFIG_NOSYSTEM", "1")
                .env("GIT_CONFIG_GLOBAL", "NUL")
                .output()
                .unwrap();
            assert!(output.status.success(), "{output:?}");
            String::from_utf8(output.stdout).unwrap().trim().to_owned()
        };
        git_run(&["init", "--quiet"]);
        git_run(&["config", "user.name", "Native Fixture"]);
        git_run(&["config", "user.email", "fixture@example.invalid"]);
        fs::create_dir(source.join("allowed")).unwrap();
        fs::write(source.join("AGENTS.md"), b"Synthetic native instruction.\n").unwrap();
        fs::write(source.join("allowed/base.txt"), b"base\n").unwrap();
        fs::write(source.join("sentinel.txt"), b"source sentinel\n").unwrap();
        git_run(&["add", "."]);
        git_run(&["commit", "--quiet", "-m", "synthetic base"]);
        let base = git_run(&["rev-parse", "HEAD"]);
        let instruction: String = Sha256::digest(b"Synthetic native instruction.\n")
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        let packet = json!({"schema":"heleos.worker-task/v1", "task_id":"native-runner-probe",
            "provider":"claude_code", "mode":"implementation", "base_commit":base,
            "objective":"Create only the synthetic native proposal.", "allowed_paths":["allowed"],
            "forbidden_paths":[], "input_data_class":"PUBLIC", "egress_policy":"local_only",
            "instruction_sha256":{"AGENTS.md":instruction}, "acceptance_commands":["synthetic check"],
            "limits":{"max_actions":1,"max_duration_seconds":30}});
        let fixture = Self {
            root,
            source,
            workspace,
            git,
            provider,
            packet,
        };
        let mut command = ProviderCommand::new(Provider::ClaudeCode, fixture.provider.clone());
        command.args.push(mode.into());
        let mut config = RunnerConfig::new(
            fixture.source.clone(),
            fixture.workspace.clone(),
            fixture.git.clone(),
            command,
        );
        config.containment = ContainmentMode::WindowsRestrictedTokenJob;
        (fixture, config)
    }

    fn task(&self) -> heleos_worker_protocol::Validated<heleos_worker_protocol::Task> {
        validate_task_json(&serde_json::to_vec(&self.packet).unwrap()).unwrap()
    }
}

#[test]
fn windows_containment_allowed_roots_and_escape_denial_preserve_evidence() {
    let (f, mut config) = Fixture::new("roots");
    let outside = f.root.path().join("outside sentinel.txt");
    fs::write(&outside, b"outside sentinel\n").unwrap();
    config.provider.args.extend([
        f.source.join("sentinel.txt").into_os_string(),
        outside.clone().into_os_string(),
    ]);
    let result = run(&f.task(), &config).unwrap();
    for (path, expected) in [
        ("checkout/allowed/proposal", "checkout"),
        ("home/probe", "home"),
        ("tmp/probe", "tmp"),
    ] {
        assert_eq!(
            fs::read_to_string(result.run_directory.join(path)).unwrap(),
            expected
        );
    }
    assert_eq!(fs::read(&outside).unwrap(), b"outside sentinel\n");
    assert_eq!(
        fs::read(f.source.join("sentinel.txt")).unwrap(),
        b"source sentinel\n"
    );
    assert_eq!(
        fs::read(result.run_directory.join("task.canonical.json")).unwrap(),
        f.task().canonical_json()
    );
    assert!(!result.run_directory.join("sibling").exists());
    assert_eq!(
        result.summary()["containment"],
        json!({"mode":"windows_restricted_token_job",
        "host_path_writes_restricted":true, "process_tree_contained":true, "write_scope":["checkout","home","tmp"],
        "reads_restricted":false, "network_restricted":false, "inherited_external_authority_restricted":false})
    );
    let persisted: Value =
        serde_json::from_slice(&fs::read(result.run_directory.join("run.json")).unwrap()).unwrap();
    assert_eq!(persisted["containment"], result.summary()["containment"]);
}

#[test]
fn windows_containment_timeout_stops_parent_and_descendant() {
    let (mut f, config) = Fixture::new("timeout");
    f.packet["limits"]["max_duration_seconds"] = 4.into();
    let error = run(&f.task(), &config).unwrap_err();
    assert_eq!(error.code, FailureCode::Timeout);
    let checkout = error.checkout_path.unwrap();
    assert!(
        checkout.join("allowed/parent-started").exists(),
        "provider must actually start"
    );
    assert!(
        checkout.join("allowed/child-started").exists(),
        "descendant must actually start"
    );
    std::thread::sleep(Duration::from_secs(9));
    assert!(!checkout.join("allowed/parent-survived").exists());
    assert!(!checkout.join("allowed/child-survived").exists());
}

#[test]
fn windows_containment_literal_argv_and_bounded_output() {
    let (f, mut config) = Fixture::new("argv-output");
    let literals = [
        "",
        "two words",
        "tab\tvalue",
        "quote\"value",
        "tail\\",
        "before\\\"quote",
        "$(literal); & | %USERPROFILE%",
    ];
    config.provider.args.extend(literals.iter().map(Into::into));
    config.max_output_bytes = 63;
    let result = run(&f.task(), &config).unwrap();
    assert_eq!(result.stdout.bytes, vec![b'x'; 63]);
    assert_eq!(result.stderr.bytes, vec![b'y'; 63]);
    assert!(result.stdout.truncated && result.stderr.truncated);
    let actual: Vec<String> = serde_json::from_slice(
        &fs::read(result.checkout_path().join("allowed/argv.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(actual, literals);
    let prompt = fs::read_to_string(result.checkout_path().join("allowed/prompt")).unwrap();
    assert!(prompt.contains("Synthetic native instruction."));
}

#[test]
fn windows_containment_inventory_rejects_hardlinks_and_reparse_points() {
    for mode in ["hardlink", "symlink"] {
        let (f, config) = Fixture::new(mode);
        let error = run(&f.task(), &config).unwrap_err();
        assert_eq!(
            error.code,
            FailureCode::UnsafePath,
            "native symlink test requires Windows Developer Mode or symlink privilege"
        );
        assert_eq!(
            fs::read(f.source.join("allowed/base.txt")).unwrap(),
            b"base\n"
        );
    }
    let (mut f, config) = Fixture::new("forbidden-case");
    f.packet["forbidden_paths"] = json!(["allowed/secret"]);
    assert_eq!(
        run(&f.task(), &config).unwrap_err().code,
        FailureCode::OutOfScope
    );
}

#[test]
fn windows_containment_explicit_mode_and_cleanup_fail_closed() {
    let (f, mut config) = Fixture::new("exit7");
    for mode in [ContainmentMode::None, ContainmentMode::MacosSeatbelt] {
        config.containment = mode;
        assert_eq!(
            run(&f.task(), &config).unwrap_err().code,
            FailureCode::ContainmentUnavailable
        );
        assert_eq!(fs::read_dir(&f.workspace).unwrap().count(), 0);
    }
    config.containment = ContainmentMode::WindowsRestrictedTokenJob;
    config.cleanup_on_failure = true;
    let error = run(&f.task(), &config).unwrap_err();
    assert_eq!(error.code, FailureCode::ProviderExit);
    assert_eq!(error.exit_code, Some(7));
    assert!(!error.cleanup_failed);
    assert_eq!(fs::read_dir(&f.workspace).unwrap().count(), 0);
    config
        .provider
        .environment
        .insert("home".into(), "invalid fixed-variable alias".into());
    assert_eq!(
        run(&f.task(), &config).unwrap_err().code,
        FailureCode::InvalidConfiguration
    );
    assert!(!Path::new("invalid fixed-variable alias").exists());
}
