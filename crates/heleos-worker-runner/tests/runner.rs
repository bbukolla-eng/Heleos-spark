#![cfg(unix)]

mod common;

use common::{Fixture, git, validate};
use heleos_worker_protocol::{Provider, TerminalState, validate_handoff_json};
use heleos_worker_runner::{FailureCode, run};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs;
use std::thread;
use std::time::{Duration, Instant};

fn sha(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

fn selected_config(f: &Fixture) -> heleos_worker_runner::RunnerConfig {
    let mut config = f.config();
    let mut selected = BTreeMap::new();
    for path in ["AGENTS.md", "allowed/base.txt"] {
        let bytes = fs::read(f.source.join(path)).unwrap();
        selected.insert(path.to_owned(), sha(&bytes));
    }
    config.selected_source_files = Some(selected);
    config
}

#[test]
fn selected_view_contains_only_pinned_inputs_and_imports_candidate() {
    let f = Fixture::new(
        "test -f AGENTS.md\n\
         test -f allowed/base.txt\n\
         test ! -e protected.txt\n\
         test ! -e .git\n\
         test ! -e .gitignore\n\
         mkdir -p allowed\n\
         printf candidate > allowed/new.txt",
    );
    let config = selected_config(&f);
    let result = run(&f.task(), &config).unwrap();
    let summary = result.summary();
    let view = std::path::PathBuf::from(summary["provider_view"].as_str().unwrap());
    assert_eq!(view.parent(), Some(result.run_directory.as_path()));
    assert_eq!(
        summary["selected_source_sha256"]["AGENTS.md"],
        config.selected_source_files.as_ref().unwrap()["AGENTS.md"]
    );
    assert_eq!(
        fs::read(view.join("allowed/base.txt")).unwrap(),
        b"base bytes\n"
    );
    assert!(!view.join(".git").exists());
    assert!(result.checkout_path().join(".git/HEAD").is_file());
    assert_eq!(result.handoff.document().changed_paths, ["allowed/new.txt"]);
    assert_eq!(
        fs::read(result.checkout_path().join("allowed/new.txt")).unwrap(),
        b"candidate"
    );
}

#[test]
fn selected_manifest_rejects_traversal_missing_instruction_and_hash_drift() {
    let f = Fixture::new("printf launched > allowed/new.txt");
    let mut traversal = selected_config(&f);
    traversal
        .selected_source_files
        .as_mut()
        .unwrap()
        .insert("../protected.txt".into(), "a".repeat(64));
    assert_eq!(
        run(&f.task(), &traversal).unwrap_err().code,
        FailureCode::InvalidConfiguration
    );
    let mut missing = selected_config(&f);
    missing
        .selected_source_files
        .as_mut()
        .unwrap()
        .remove("AGENTS.md");
    assert_eq!(
        run(&f.task(), &missing).unwrap_err().code,
        FailureCode::InvalidConfiguration
    );
    let mut drift = selected_config(&f);
    drift
        .selected_source_files
        .as_mut()
        .unwrap()
        .insert("allowed/base.txt".into(), "a".repeat(64));
    assert_eq!(
        run(&f.task(), &drift).unwrap_err().code,
        FailureCode::InstructionMismatch
    );
    let mut forbidden_input = selected_config(&f);
    forbidden_input
        .selected_source_files
        .as_mut()
        .unwrap()
        .insert(
            "protected.txt".into(),
            sha(&fs::read(f.source.join("protected.txt")).unwrap()),
        );
    let mut packet = f.packet();
    packet["forbidden_paths"] = serde_json::json!(["protected.txt"]);
    assert_eq!(
        run(&validate(&packet), &forbidden_input).unwrap_err().code,
        FailureCode::InvalidConfiguration
    );
    assert!(!f.source.join("allowed/new.txt").exists());
}

#[test]
fn selected_view_rejects_symlinked_input_and_output() {
    let f = Fixture::new("ln -s ../../protected.txt allowed/new.txt");
    std::os::unix::fs::symlink("../protected.txt", f.source.join("allowed/link")).unwrap();
    git(&f.source, &["add", "allowed/link"]);
    git(&f.source, &["commit", "--quiet", "-m", "link"]);
    let mut config = selected_config(&f);
    config
        .selected_source_files
        .as_mut()
        .unwrap()
        .insert("allowed/link".into(), sha(b"../protected.txt"));
    let mut packet = f.packet();
    packet["base_commit"] = git(&f.source, &["rev-parse", "HEAD"]).into();
    assert_eq!(
        run(&validate(&packet), &config).unwrap_err().code,
        FailureCode::UnsafePath
    );

    let clean = Fixture::new("ln -s ../../protected.txt allowed/new.txt");
    assert_eq!(
        run(&clean.task(), &selected_config(&clean))
            .unwrap_err()
            .code,
        FailureCode::UnsafePath
    );

    let linked = Fixture::new("ln allowed/base.txt allowed/new.txt");
    assert_eq!(
        run(&linked.task(), &selected_config(&linked))
            .unwrap_err()
            .code,
        FailureCode::UnsafePath
    );
}

#[test]
fn selected_view_rejects_out_of_scope_and_oversized_candidate_without_import() {
    for (script, expected) in [
        ("printf bad > protected.txt", FailureCode::OutOfScope),
        (
            "mkdir -p allowed/secret; printf bad > allowed/secret/key",
            FailureCode::OutOfScope,
        ),
        (
            "head -c 4194305 /dev/zero > allowed/new.txt",
            FailureCode::InventoryLimit,
        ),
        (
            "for i in $(seq 1 257); do printf x > allowed/new-$i.txt; done",
            FailureCode::InventoryLimit,
        ),
    ] {
        let f = Fixture::new(script);
        let mut config = selected_config(&f);
        config.cleanup_on_failure = false;
        let error = run(&f.task(), &config).unwrap_err();
        assert_eq!(error.code, expected);
        let checkout = error.checkout_path.unwrap();
        assert!(!checkout.join("allowed/new.txt").exists());
        assert_eq!(
            fs::read(checkout.join("protected.txt")).unwrap(),
            b"protected bytes\n"
        );
    }
}

#[test]
fn selected_view_repeated_run_has_same_candidate_hash() {
    let f = Fixture::new("printf candidate > allowed/new.txt");
    let config = selected_config(&f);
    let first = run(&f.task(), &config).unwrap();
    let second = run(&f.task(), &config).unwrap();
    assert_ne!(first.run_directory, second.run_directory);
    assert_eq!(
        first.summary()["changed_files"],
        second.summary()["changed_files"]
    );
    assert_eq!(
        first.summary()["selected_source_sha256"],
        second.summary()["selected_source_sha256"]
    );
}

#[test]
fn containment_default_is_none_and_mode_round_trips() {
    use heleos_worker_runner::ContainmentMode;
    let f = Fixture::new("exit 0");
    assert_eq!(f.config().containment, ContainmentMode::None);
    assert_eq!(
        serde_json::to_string(&ContainmentMode::MacosSeatbelt).unwrap(),
        "\"macos_seatbelt\""
    );
    assert_eq!(
        serde_json::from_str::<ContainmentMode>("\"macos_seatbelt\"").unwrap(),
        ContainmentMode::MacosSeatbelt
    );
    assert!(serde_json::from_str::<ContainmentMode>("\"arbitrary_profile\"").is_err());
}

#[cfg(target_os = "macos")]
#[test]
fn containment_keeps_inventory_and_typed_failure_evidence() {
    let f = Fixture::new("printf forbidden > protected.txt");
    let mut config = f.config();
    config.containment = heleos_worker_runner::ContainmentMode::MacosSeatbelt;
    config.cleanup_on_failure = false;
    let error = run(&f.task(), &config).unwrap_err();
    assert_eq!(error.code, FailureCode::OutOfScope);
    let evidence: serde_json::Value = serde_json::from_slice(
        &fs::read(error.run_directory.unwrap().join("failure.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(evidence["code"], "out_of_scope");
    assert_eq!(
        fs::read(f.source.join("protected.txt")).unwrap(),
        b"protected bytes\n"
    );
}

#[cfg(not(target_os = "macos"))]
#[test]
fn containment_unavailable_fails_closed_before_provider() {
    let f = Fixture::new("printf launched > allowed/new.txt");
    let mut config = f.config();
    config.containment = heleos_worker_runner::ContainmentMode::MacosSeatbelt;
    let error = run(&f.task(), &config).unwrap_err();
    assert_eq!(error.code, FailureCode::ContainmentUnavailable);
    assert_eq!(error.summary()["code"], "containment_unavailable");
    f.assert_cleaned();
}

// These tests catch missing isolation, policy checks, and actual process controls.
// Every provider is an executable fixture; the runner and local Git are real.
#[test]
fn allowed_untracked_write_yields_usable_validated_pending_handoff() {
    let f = Fixture::new("/bin/cat >/dev/null\nprintf proposal > allowed/new.txt");
    let task = f.task();
    let result = run(&task, &f.config()).unwrap();
    assert_eq!(result.handoff.document().changed_paths, ["allowed/new.txt"]);
    assert_eq!(
        result.handoff.document().terminal_state,
        TerminalState::Blocked
    );
    assert!(result.handoff.document().checks.is_empty());
    assert!(result.handoff.document().candidate_commit.is_none());
    validate_handoff_json(result.handoff.canonical_json(), &task).unwrap();
    assert_eq!(
        fs::read(result.checkout_path().join("allowed/new.txt")).unwrap(),
        b"proposal"
    );
    assert!(!result.checkout_path().join("MUST_NOT_RUN").exists());
    let checkout = result.checkout_path().to_path_buf();
    drop(result);
    assert!(checkout.join("allowed/new.txt").exists());
}

#[test]
fn forbidden_write_is_rejected_and_cleaned() {
    let f = Fixture::new("mkdir -p allowed/secret\nprintf x > allowed/secret/key");
    let error = run(&f.task(), &f.config()).unwrap_err();
    assert_eq!(error.code, FailureCode::OutOfScope);
    f.assert_cleaned();
}

#[test]
fn out_of_allowlist_and_ignored_writes_are_rejected() {
    for script in [
        "printf x > protected.txt",
        "printf x > ignored.txt",
        "mkdir allowed-other; printf x > allowed-other/new",
    ] {
        let f = Fixture::new(script);
        assert_eq!(
            run(&f.task(), &f.config()).unwrap_err().code,
            FailureCode::OutOfScope
        );
        f.assert_cleaned();
    }
}

#[test]
fn timeout_terminates_provider_and_descendants_without_pipe_deadlock() {
    let f = Fixture::new("sleep 20 &\nwait");
    let mut packet = f.packet();
    packet["limits"]["max_duration_seconds"] = 1.into();
    let started = Instant::now();
    assert_eq!(
        run(&validate(&packet), &f.config()).unwrap_err().code,
        FailureCode::Timeout
    );
    assert!(started.elapsed() < Duration::from_secs(4));
    f.assert_cleaned();
}

#[test]
fn unlimited_claude_outlives_finite_comparison_and_retains_bounded_candidate() {
    let f = Fixture::new(
        "/bin/cat >/dev/null\nsleep 2\nprintf proposal > allowed/new.txt\n\
         i=0; while [ \"$i\" -lt 100 ]; do printf abcdefghij; printf klmnopqrst >&2; i=$((i+1)); done",
    );
    let mut packet = f.packet();
    packet["limits"]["max_duration_seconds"] = 1.into();
    assert_eq!(
        run(&validate(&packet), &f.config()).unwrap_err().code,
        FailureCode::Timeout
    );
    f.assert_cleaned();

    packet["limits"]["max_duration_seconds"] = serde_json::Value::Null;
    let mut config = f.config();
    config.max_output_bytes = 64;
    let started = Instant::now();
    let result = run(&validate(&packet), &config).unwrap();
    assert!(started.elapsed() >= Duration::from_secs(2));
    assert_eq!(result.handoff.document().changed_paths, ["allowed/new.txt"]);
    assert_eq!(
        result.handoff.document().terminal_state,
        TerminalState::Blocked
    );
    assert!(result.handoff.document().checks.is_empty());
    assert_eq!(
        fs::read(result.checkout_path().join("allowed/new.txt")).unwrap(),
        b"proposal"
    );
    assert!(!result.checkout_path().join("MUST_NOT_RUN").exists());
    assert_eq!(result.stdout.bytes.len(), 64);
    assert_eq!(result.stderr.bytes.len(), 64);
    assert!(result.stdout.truncated && result.stderr.truncated);
    assert_eq!(
        fs::read(f.source.join("allowed/base.txt")).unwrap(),
        b"base bytes\n"
    );
    assert!(!f.source.join("allowed/new.txt").exists());
}

#[test]
fn live_claude_trace_survives_raw_stdout_truncation_without_storing_thinking() {
    let thought = format!(
        "{{\"type\":\"system\",\"subtype\":\"thinking_tokens\",\"hidden\":\"{}\"}}",
        "private thought".repeat(30)
    );
    let script = format!(
        "printf proposal > allowed/new.txt\n\
         i=0; while [ \"$i\" -lt 4000 ]; do printf '%s\\n' '{thought}'; i=$((i+1)); done\n\
         printf '%s\\n' '{{\"type\":\"assistant\",\"message\":{{\"model\":\"claude-opus-5-5\",\"content\":[{{\"type\":\"tool_use\",\"name\":\"Workflow\",\"id\":\"w1\",\"input\":{{\"script\":\"phase()\"}}}}]}}}}'\n\
         printf '%s\\n' '{{\"type\":\"system\",\"subtype\":\"task_notification\",\"tool_use_id\":\"w1\",\"task_id\":\"t1\",\"status\":\"completed\",\"output_file\":\"audit.txt\"}}'\n\
         printf '%s\\n' '{{\"type\":\"result\",\"subtype\":\"success\",\"is_error\":false,\"modelUsage\":{{\"claude-opus-5-5\":{{}}}}}}'\n\
         sleep 2"
    );
    let f = Fixture::new(&script);
    let mut config = f.config();
    config.max_output_bytes = 1_048_576;
    config.capture_claude_stream_trace = true;
    let workspace = f.workspace.clone();
    let task = f.task();
    let running = thread::spawn(move || run(&task, &config).unwrap());
    let started = Instant::now();
    let progress = loop {
        assert!(started.elapsed() < Duration::from_secs(8));
        let entries = fs::read_dir(&workspace).unwrap();
        let found = entries.flatten().find_map(|entry| {
            let path = entry.path().join("trace-progress.json");
            if !path.is_file() {
                return None;
            }
            let raw = fs::read(path).ok()?;
            serde_json::from_slice::<serde_json::Value>(&raw).ok()
        });
        if let Some(value) = found.filter(|value| value["events_seen"].as_u64().unwrap_or(0) >= 128)
        {
            break value;
        }
        thread::sleep(Duration::from_millis(20));
    };
    assert!(!running.is_finished());
    assert_eq!(progress["complete"], false);
    let result = running.join().unwrap();
    let summary = result.summary();
    assert!(result.stdout.truncated);
    assert!(result.stdout.bytes.is_empty());
    assert_eq!(summary["raw_stdout_omitted"], true);
    assert_eq!(summary["stream_trace"]["complete"], true);
    assert_eq!(summary["stream_trace"]["thinking_events_omitted"], 4000);
    let trace = fs::read(result.run_directory.join("event-trace.jsonl")).unwrap();
    assert!(String::from_utf8_lossy(&trace).contains("\"Workflow\""));
    assert!(String::from_utf8_lossy(&trace).contains("\"result\""));
    assert!(!String::from_utf8_lossy(&trace).contains("private thought"));
}

#[test]
fn immediate_exit_preserves_terminal_event_after_large_pipe_output() {
    let script = "printf proposal > allowed/new.txt\n\
         i=0; while [ \"$i\" -lt 5000 ]; do printf '%s\\n' '{\"type\":\"system\",\"subtype\":\"thinking_tokens\"}'; i=$((i+1)); done\n\
         printf '%s\\n' '{\"type\":\"result\",\"subtype\":\"success\",\"is_error\":false}'";
    let f = Fixture::new(script);
    let mut config = f.config();
    config.capture_claude_stream_trace = true;
    let result = run(&f.task(), &config).unwrap();
    let summary = result.summary();
    assert_eq!(summary["stream_trace"]["complete"], true);
    assert_eq!(summary["stream_trace"]["thinking_events_omitted"], 5000);
    let trace = fs::read_to_string(result.run_directory.join("event-trace.jsonl")).unwrap();
    assert_eq!(trace.lines().count(), 1);
    assert!(trace.contains("\"result\""));
}

#[test]
fn unlimited_claude_preserves_scope_rejection_and_provider_failure() {
    let forbidden = Fixture::new("printf forbidden > protected.txt");
    let mut packet = forbidden.packet();
    packet["limits"]["max_duration_seconds"] = serde_json::Value::Null;
    assert_eq!(
        run(&validate(&packet), &forbidden.config())
            .unwrap_err()
            .code,
        FailureCode::OutOfScope
    );
    forbidden.assert_cleaned();
    assert_eq!(
        fs::read(forbidden.source.join("protected.txt")).unwrap(),
        b"protected bytes\n"
    );

    let failed = Fixture::new("printf 'provider failed' >&2\nexit 7");
    let mut packet = failed.packet();
    packet["limits"]["max_duration_seconds"] = serde_json::Value::Null;
    let mut config = failed.config();
    config.max_output_bytes = 4;
    let error = run(&validate(&packet), &config).unwrap_err();
    assert_eq!(error.code, FailureCode::ProviderExit);
    assert_eq!(error.exit_code, Some(7));
    assert_eq!(error.stderr.bytes, b"prov");
    assert!(error.stderr.truncated);
    failed.assert_cleaned();
}

#[test]
fn nonzero_exit_has_typed_failure_and_bounded_diagnostics() {
    let f = Fixture::new("printf 'provider failed' >&2\nexit 7");
    let error = run(&f.task(), &f.config()).unwrap_err();
    assert_eq!(error.code, FailureCode::ProviderExit);
    assert_eq!(error.exit_code, Some(7));
    assert_eq!(error.stderr.bytes, b"provider failed");
    f.assert_cleaned();
}

#[test]
fn exact_base_and_dirty_source_are_preserved() {
    let f = Fixture::new(
        "/usr/bin/git rev-parse HEAD > allowed/seen-head\n/bin/cat allowed/base.txt > allowed/seen-base",
    );
    fs::write(f.source.join("allowed/base.txt"), "later committed bytes\n").unwrap();
    git(&f.source, &["add", "."]);
    git(&f.source, &["commit", "--quiet", "-m", "later"]);
    fs::write(f.source.join("allowed/base.txt"), "source dirty bytes\n").unwrap();
    fs::write(f.source.join("untracked-source.txt"), "keep me").unwrap();
    let before_head = git(&f.source, &["rev-parse", "HEAD"]);
    let before_status = git(
        &f.source,
        &["status", "--porcelain=v1", "--untracked-files=all"],
    );
    let before_index = fs::read(f.source.join(".git/index")).unwrap();
    let before_head_bytes = fs::read(f.source.join(".git/HEAD")).unwrap();
    let before_worktrees = git(&f.source, &["worktree", "list", "--porcelain"]);
    let result = run(&f.task(), &f.config()).unwrap();
    assert_eq!(fs::read(f.source.join(".git/index")).unwrap(), before_index);
    assert_eq!(
        fs::read(f.source.join(".git/HEAD")).unwrap(),
        before_head_bytes
    );
    assert_eq!(
        git(&f.source, &["worktree", "list", "--porcelain"]),
        before_worktrees
    );
    assert_eq!(
        fs::read_to_string(result.checkout_path().join("allowed/seen-head"))
            .unwrap()
            .trim(),
        f.base
    );
    assert_eq!(
        fs::read(result.checkout_path().join("allowed/seen-base")).unwrap(),
        b"base bytes\n"
    );
    assert_eq!(git(&f.source, &["rev-parse", "HEAD"]), before_head);
    assert_eq!(
        git(
            &f.source,
            &["status", "--porcelain=v1", "--untracked-files=all"]
        ),
        before_status
    );
    assert_eq!(
        fs::read(f.source.join("allowed/base.txt")).unwrap(),
        b"source dirty bytes\n"
    );
    assert_eq!(
        fs::read(f.source.join("untracked-source.txt")).unwrap(),
        b"keep me"
    );
    assert_eq!(git(result.checkout_path(), &["remote"]), "");
}

#[test]
fn explicit_argv_is_literal_and_prompt_goes_to_stdin() {
    let f = Fixture::new("printf '%s' \"$1\" > allowed/argument\n/bin/cat > allowed/prompt");
    let mut config = f.config();
    let literal = "$(touch INJECTED); `touch ALSO_INJECTED` $HOME";
    config.provider.args.push(literal.into());
    let result = run(&f.task(), &config).unwrap();
    assert_eq!(
        fs::read_to_string(result.checkout_path().join("allowed/argument")).unwrap(),
        literal
    );
    assert!(!result.checkout_path().join("INJECTED").exists());
    assert!(!result.checkout_path().join("ALSO_INJECTED").exists());
    let prompt = fs::read_to_string(result.checkout_path().join("allowed/prompt")).unwrap();
    assert!(prompt.contains("Create a small local proposal."));
    assert!(prompt.contains("Fixture instruction."));
    assert!(prompt.len() <= config.max_prompt_bytes);
}

#[test]
fn stdout_and_stderr_are_drained_but_bounded() {
    let f = Fixture::new(
        "i=0; while [ \"$i\" -lt 20000 ]; do printf abcdefghij; printf klmnopqrst >&2; i=$((i+1)); done",
    );
    let mut config = f.config();
    config.max_output_bytes = 127;
    let result = run(&f.task(), &config).unwrap();
    assert_eq!(result.stdout.bytes.len(), 127);
    assert_eq!(result.stderr.bytes.len(), 127);
    assert!(result.stdout.truncated && result.stderr.truncated);
}

#[test]
fn oversized_prompt_stops_before_provider_launch() {
    let f = Fixture::new("printf x > allowed/new");
    let mut config = f.config();
    config.max_prompt_bytes = 32;
    assert_eq!(
        run(&f.task(), &config).unwrap_err().code,
        FailureCode::PromptTooLarge
    );
    f.assert_cleaned();
}

#[test]
fn instruction_digest_mismatch_is_rejected() {
    let f = Fixture::new("exit 99");
    let mut packet = f.packet();
    packet["instruction_sha256"]["AGENTS.md"] = "a".repeat(64).into();
    assert_eq!(
        run(&validate(&packet), &f.config()).unwrap_err().code,
        FailureCode::InstructionMismatch
    );
    f.assert_cleaned();
}

#[test]
fn unavailable_exact_base_fails_without_falling_back_to_head() {
    let f = Fixture::new("exit 99");
    let mut packet = f.packet();
    packet["base_commit"] = "a".repeat(40).into();
    assert_eq!(
        run(&validate(&packet), &f.config()).unwrap_err().code,
        FailureCode::BaseUnavailable
    );
    f.assert_cleaned();
}

#[test]
fn provider_and_mode_must_match_the_implementation_assignment() {
    let f = Fixture::new("exit 99");
    let mut config = f.config();
    config.provider.provider = Provider::Kimi;
    assert_eq!(
        run(&f.task(), &config).unwrap_err().code,
        FailureCode::ProviderMismatch
    );
    for provider in ["notebook_lm", "grok_bots"] {
        let mut packet = f.packet();
        packet["mode"] = "research".into();
        packet["provider"] = provider.into();
        assert_eq!(
            run(&validate(&packet), &f.config()).unwrap_err().code,
            FailureCode::UnsupportedMode
        );
    }
    f.assert_cleaned();
}

#[test]
fn git_metadata_tampering_and_symlink_writes_fail_closed() {
    for script in ["printf x >> .git/config", "ln -s /tmp allowed/escape"] {
        let f = Fixture::new(script);
        let code = run(&f.task(), &f.config()).unwrap_err().code;
        assert!(matches!(
            code,
            FailureCode::RepositoryMutation | FailureCode::UnsafePath
        ));
        f.assert_cleaned();
    }
}

#[test]
fn tracked_deletion_and_executable_mode_changes_are_inventoried() {
    for script in ["rm allowed/base.txt", "chmod +x allowed/base.txt"] {
        let f = Fixture::new(script);
        let result = run(&f.task(), &f.config()).unwrap();
        assert_eq!(
            result.handoff.document().changed_paths,
            ["allowed/base.txt"]
        );
    }
}

#[test]
fn default_failure_retention_keeps_the_failed_checkout() {
    let f = Fixture::new("printf x > allowed/partial\nexit 3");
    let mut config = f.config();
    config.cleanup_on_failure = false;
    let error = run(&f.task(), &config).unwrap_err();
    assert_eq!(error.code, FailureCode::ProviderExit);
    assert_eq!(
        fs::read(error.checkout_path.unwrap().join("allowed/partial")).unwrap(),
        b"x"
    );
}

#[test]
fn provider_cannot_redirect_evidence_writes_through_a_symlink() {
    let f = Fixture::new("ln -s \"$1\" ../stdout.bin\nprintf evidence");
    let protected = f.source.join("protected.txt");
    let mut config = f.config();
    config
        .provider
        .args
        .push(protected.clone().into_os_string());
    let error = run(&f.task(), &config).unwrap_err();
    assert!(matches!(
        error.code,
        FailureCode::UnsafePath | FailureCode::Io
    ));
    assert_eq!(fs::read(protected).unwrap(), b"protected bytes\n");
}

#[test]
fn staged_and_committed_changes_and_renames_are_compared_to_original_base() {
    for script in [
        "mv allowed/base.txt allowed/renamed.txt\n/usr/bin/git add allowed",
        "mv allowed/base.txt allowed/renamed.txt\n/usr/bin/git add allowed\n/usr/bin/git -c user.name=Fixture -c user.email=fixture@example.invalid commit -qm candidate",
    ] {
        let f = Fixture::new(script);
        let result = run(&f.task(), &f.config()).unwrap();
        assert_eq!(
            result.handoff.document().changed_paths,
            ["allowed/base.txt", "allowed/renamed.txt"]
        );
        assert!(result.handoff.document().candidate_commit.is_none());
    }
}

#[test]
fn assume_unchanged_index_flag_cannot_hide_a_forbidden_write() {
    let f = Fixture::new(
        "/usr/bin/git update-index --assume-unchanged protected.txt\nprintf hidden > protected.txt",
    );
    assert_eq!(
        run(&f.task(), &f.config()).unwrap_err().code,
        FailureCode::OutOfScope
    );
}

#[test]
fn same_group_background_child_is_stopped_before_success_handoff() {
    let f = Fixture::new("(sleep 1; printf late > allowed/late) &\nprintf now > allowed/now");
    let result = run(&f.task(), &f.config()).unwrap();
    std::thread::sleep(Duration::from_millis(1200));
    assert_eq!(result.handoff.document().changed_paths, ["allowed/now"]);
    assert!(!result.checkout_path().join("allowed/late").exists());
}

#[test]
fn overlapping_workspace_roots_are_rejected_before_creation() {
    let f = Fixture::new("exit 99");
    for root in [f.source.clone(), f.root.path().to_path_buf()] {
        let mut config = f.config();
        config.workspace_root = root;
        assert_eq!(
            run(&f.task(), &config).unwrap_err().code,
            FailureCode::InvalidConfiguration
        );
    }
    f.assert_cleaned();
}

// Retired provider packets remain readable as history but cannot execute.
#[test]
fn retired_kimi_rejected_before_workspace_or_provider_launch() {
    for containment in [
        heleos_worker_runner::ContainmentMode::None,
        heleos_worker_runner::ContainmentMode::MacosSeatbelt,
        heleos_worker_runner::ContainmentMode::WindowsRestrictedTokenJob,
    ] {
        let f = Fixture::new("printf launched > allowed/provider-started");
        let mut config = f.config();
        config.provider.provider = Provider::Kimi;
        config.containment = containment;
        let mut packet = f.packet();
        packet["provider"] = "kimi".into();
        let error = run(&validate(&packet), &config).unwrap_err();
        assert_eq!(error.code, FailureCode::UnsupportedProvider);
        assert!(error.run_directory.is_none());
        assert!(error.checkout_path.is_none());
        assert!(error.exit_code.is_none());
        assert!(error.stdout.bytes.is_empty());
        assert!(error.stderr.bytes.is_empty());
        f.assert_cleaned();
    }
}

#[test]
fn cli_rejects_retired_kimi_before_opening_task() {
    let f = Fixture::new("printf launched > allowed/provider-started");
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_heleos-worker-runner"))
        .arg("--task")
        .arg(f.root.path().join("missing-task.json"))
        .arg("--source")
        .arg(&f.source)
        .arg("--workspace-root")
        .arg(&f.workspace)
        .arg("--command")
        .arg(&f.provider)
        .args(["--provider", "kimi"])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(2));
    let error: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(error["code"], "invalid_configuration");
    assert!(output.stderr.is_empty());
    f.assert_cleaned();
}

// Break caught: rejecting Grok or treating it as another provider prevents the
// configured implementation command from yielding a Grok-bound handoff.
#[test]
fn grok_is_admitted_for_implementation_with_validated_handoff() {
    let f = Fixture::new("printf grok-fixture > allowed/new");
    let mut config = f.config();
    config.provider.provider = Provider::Grok;
    let mut packet = f.packet();
    packet["provider"] = "grok".into();
    let task = validate(&packet);
    let result = run(&task, &config).unwrap();
    assert_eq!(result.handoff.document().provider, Provider::Grok);
    assert_eq!(result.handoff.document().changed_paths, ["allowed/new"]);
    assert_eq!(
        fs::read(result.checkout_path().join("allowed/new")).unwrap(),
        b"grok-fixture"
    );
    validate_handoff_json(result.handoff.canonical_json(), &task).unwrap();
}

// Break caught: Codex must remain bound to its task and resulting handoff.
#[test]
fn codex_is_admitted_for_implementation_with_validated_handoff() {
    let f = Fixture::new("printf codex-fixture > allowed/new");
    let mut config = f.config();
    config.provider.provider = Provider::Codex;
    let mut packet = f.packet();
    packet["provider"] = "codex".into();
    let task = validate(&packet);
    let result = run(&task, &config).unwrap();
    assert_eq!(result.handoff.document().provider, Provider::Codex);
    assert_eq!(result.handoff.document().changed_paths, ["allowed/new"]);
    assert_eq!(
        result.handoff.document().terminal_state,
        TerminalState::Blocked
    );
    assert_eq!(
        fs::read(result.checkout_path().join("allowed/new")).unwrap(),
        b"codex-fixture"
    );
    validate_handoff_json(result.handoff.canonical_json(), &task).unwrap();
}

#[test]
fn codex_provider_mismatch_fails_before_launch_in_both_directions() {
    for (task_provider, command_provider) in [
        ("codex", Provider::ClaudeCode),
        ("claude_code", Provider::Codex),
    ] {
        let f = Fixture::new("printf unexpected > \"$1\"");
        let marker = f.root.path().join("provider-started");
        let mut config = f.config();
        config.provider.provider = command_provider;
        config.provider.args.push(marker.clone().into_os_string());
        let mut packet = f.packet();
        packet["provider"] = task_provider.into();
        assert_eq!(
            run(&validate(&packet), &config).unwrap_err().code,
            FailureCode::ProviderMismatch
        );
        assert!(!marker.exists());
        f.assert_cleaned();
    }
}

// Break caught: Cursor admission must retain exact task identity and inventory.
#[test]
fn cursor_is_admitted_for_implementation_with_validated_handoff() {
    let f = Fixture::new("printf cursor-fixture > allowed/new");
    let mut config = f.config();
    config.provider.provider = Provider::Cursor;
    let mut packet = f.packet();
    packet["provider"] = "cursor".into();
    let task = validate(&packet);
    let result = run(&task, &config).unwrap();
    assert_eq!(result.handoff.document().provider, Provider::Cursor);
    assert_eq!(result.handoff.document().changed_paths, ["allowed/new"]);
    assert_eq!(
        result.handoff.document().terminal_state,
        TerminalState::Blocked
    );
    assert_eq!(
        fs::read(result.checkout_path().join("allowed/new")).unwrap(),
        b"cursor-fixture"
    );
    validate_handoff_json(result.handoff.canonical_json(), &task).unwrap();
}

#[test]
fn cursor_provider_mismatch_fails_before_launch_in_both_directions() {
    for (task_provider, command_provider) in [
        ("cursor", Provider::ClaudeCode),
        ("claude_code", Provider::Cursor),
    ] {
        let f = Fixture::new("printf unexpected > \"$1\"");
        let marker = f.root.path().join("provider-started");
        let mut config = f.config();
        config.provider.provider = command_provider;
        config.provider.args.push(marker.clone().into_os_string());
        let mut packet = f.packet();
        packet["provider"] = task_provider.into();
        assert_eq!(
            run(&validate(&packet), &config).unwrap_err().code,
            FailureCode::ProviderMismatch
        );
        assert!(!marker.exists());
        f.assert_cleaned();
    }
}

#[test]
fn provider_cannot_rewrite_preserved_assignment_evidence() {
    let f = Fixture::new("printf tampered > ../task.original.json");
    assert_eq!(
        run(&f.task(), &f.config()).unwrap_err().code,
        FailureCode::RepositoryMutation
    );
}

#[test]
fn replaced_workspace_identity_is_retained_without_cleaning_the_replacement() {
    let f = Fixture::new(
        "run_dir=${PWD%/*}\nmv \"$run_dir\" \"$run_dir-moved\"\nmkdir \"$run_dir\"\nprintf keep > \"$run_dir/replacement\"",
    );
    let error = run(&f.task(), &f.config()).unwrap_err();
    assert_eq!(error.code, FailureCode::UnsafePath);
    assert!(error.cleanup_failed);
    assert_eq!(
        fs::read(error.run_directory.unwrap().join("replacement")).unwrap(),
        b"keep"
    );
}

#[test]
fn retained_workspace_records_its_actual_directory_identity() {
    use std::os::unix::fs::MetadataExt;
    let f = Fixture::new("printf x > allowed/new");
    let result = run(&f.task(), &f.config()).unwrap();
    let record: serde_json::Value =
        serde_json::from_slice(&fs::read(result.run_directory.join("ownership.json")).unwrap())
            .unwrap();
    let metadata = fs::metadata(&result.run_directory).unwrap();
    assert_eq!(record["device"], metadata.dev());
    assert_eq!(record["inode"], metadata.ino());
    assert_eq!(
        record["run_directory"].as_str().unwrap(),
        result.run_directory.to_str().unwrap()
    );
}
