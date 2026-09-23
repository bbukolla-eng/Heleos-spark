#![allow(dead_code)]

use heleos_worker_protocol::{Provider, Task, Validated, validate_task_json};
use heleos_worker_runner::{ProviderCommand, RunnerConfig};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;

pub struct Fixture {
    pub root: tempfile::TempDir,
    pub source: PathBuf,
    pub workspace: PathBuf,
    pub provider: PathBuf,
    pub base: String,
}

pub fn git(root: &Path, args: &[&str]) -> String {
    let output = Command::new("/usr/bin/git")
        .args(["-c", "core.hooksPath=/dev/null"])
        .args(args)
        .env("GIT_CONFIG_NOSYSTEM", "1")
        .env("GIT_CONFIG_GLOBAL", "/dev/null")
        .current_dir(root)
        .output()
        .unwrap();
    assert!(output.status.success(), "{:?}", output);
    String::from_utf8(output.stdout).unwrap().trim().to_owned()
}

impl Fixture {
    pub fn new(script: &str) -> Self {
        let root = tempfile::tempdir().unwrap();
        let source = root.path().join("source with spaces");
        let workspace = root.path().join("workspace with spaces");
        fs::create_dir(&source).unwrap();
        fs::create_dir(&workspace).unwrap();
        git(&source, &["init", "--quiet"]);
        git(
            &source,
            &["config", "user.email", "fixture@example.invalid"],
        );
        git(&source, &["config", "user.name", "Fixture"]);
        fs::create_dir(source.join("allowed")).unwrap();
        fs::write(source.join("AGENTS.md"), "Fixture instruction.\n").unwrap();
        fs::write(source.join("allowed/base.txt"), "base bytes\n").unwrap();
        fs::write(source.join("protected.txt"), "protected bytes\n").unwrap();
        fs::write(source.join(".gitignore"), "ignored.txt\n").unwrap();
        git(&source, &["add", "."]);
        git(&source, &["commit", "--quiet", "-m", "base"]);
        let base = git(&source, &["rev-parse", "HEAD"]);
        let provider = root.path().join("fake provider; literal");
        fs::write(&provider, format!("#!/bin/sh\nset -eu\n{script}\n")).unwrap();
        fs::set_permissions(&provider, fs::Permissions::from_mode(0o700)).unwrap();
        Self {
            root,
            source,
            workspace,
            provider,
            base,
        }
    }

    pub fn packet(&self) -> Value {
        let digest: String = Sha256::digest(b"Fixture instruction.\n")
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        json!({
            "schema": "heleos.worker-task/v1", "task_id": "runner-test",
            "provider": "claude_code", "mode": "implementation", "base_commit": self.base,
            "objective": "Create a small local proposal.",
            "allowed_paths": ["allowed"], "forbidden_paths": ["allowed/secret"],
            "input_data_class": "PUBLIC", "egress_policy": "local_only",
            "instruction_sha256": {"AGENTS.md": digest},
            "acceptance_commands": ["touch MUST_NOT_RUN"],
            "limits": {"max_actions": 1, "max_duration_seconds": 10}
        })
    }

    pub fn task(&self) -> Validated<Task> {
        validate(&self.packet())
    }

    pub fn config(&self) -> RunnerConfig {
        let mut config = RunnerConfig::new(
            self.source.clone(),
            self.workspace.clone(),
            PathBuf::from("/usr/bin/git"),
            ProviderCommand::new(Provider::ClaudeCode, self.provider.clone()),
        );
        config.cleanup_on_failure = true;
        config
    }

    pub fn assert_cleaned(&self) {
        assert_eq!(fs::read_dir(&self.workspace).unwrap().count(), 0);
    }
}

pub fn validate(packet: &Value) -> Validated<Task> {
    validate_task_json(&serde_json::to_vec(packet).unwrap()).unwrap()
}
