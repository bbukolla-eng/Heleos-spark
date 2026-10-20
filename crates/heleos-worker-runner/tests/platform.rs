use heleos_worker_protocol::{Provider, validate_task_json};
use heleos_worker_runner::{ContainmentMode, FailureCode, ProviderCommand, RunnerConfig, run};
use serde_json::json;

#[test]
fn windows_mode_round_trips_without_becoming_an_uncontained_mode() {
    let mode = serde_json::from_str::<ContainmentMode>("\"windows_restricted_token_job\"")
        .expect("explicit Windows containment mode must be admitted");
    assert_ne!(mode, ContainmentMode::None);
    assert_eq!(
        serde_json::to_string(&mode).unwrap(),
        "\"windows_restricted_token_job\""
    );
    assert!(serde_json::from_str::<ContainmentMode>("\"windows\"").is_err());
}

#[test]
fn platform_mismatch_never_launches_the_provider() {
    let root = tempfile::tempdir().unwrap();
    let source = root.path().join("source");
    let workspace = root.path().join("workspace");
    std::fs::create_dir(&source).unwrap();
    std::fs::create_dir(&workspace).unwrap();
    let executable = std::env::current_exe().unwrap();
    let mut config = RunnerConfig::new(
        source,
        workspace.clone(),
        executable.clone(),
        ProviderCommand::new(Provider::ClaudeCode, executable),
    );
    config.cleanup_on_failure = true;
    let document = json!({
        "schema":"heleos.worker-task/v1", "task_id":"platform-test", "provider":"claude_code",
        "mode":"implementation", "base_commit":"a".repeat(40), "objective":"Synthetic platform probe.",
        "allowed_paths":["allowed"], "forbidden_paths":[], "input_data_class":"PUBLIC",
        "egress_policy":"local_only", "instruction_sha256":{"AGENTS.md":"b".repeat(64)},
        "acceptance_commands":["synthetic check"], "limits":{"max_actions":1,"max_duration_seconds":5}
    });
    let task = validate_task_json(&serde_json::to_vec(&document).unwrap()).unwrap();
    #[cfg(not(windows))]
    {
        config.containment = serde_json::from_str("\"windows_restricted_token_job\"").unwrap();
        assert_eq!(
            run(&task, &config).unwrap_err().code,
            FailureCode::ContainmentUnavailable
        );
    }
    #[cfg(windows)]
    {
        assert_eq!(
            run(&task, &config).unwrap_err().code,
            FailureCode::ContainmentUnavailable
        );
        config.containment = ContainmentMode::MacosSeatbelt;
        assert_eq!(
            run(&task, &config).unwrap_err().code,
            FailureCode::ContainmentUnavailable
        );
    }
    assert_eq!(std::fs::read_dir(workspace).unwrap().count(), 0);
}
