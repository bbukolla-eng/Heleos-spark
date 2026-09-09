#![cfg(unix)]

mod common;

use common::{Fixture, git, validate};
use heleos_worker_protocol::{Provider, TerminalState, validate_handoff_json};
use heleos_worker_runner::{FailureCode, run};
use std::fs;
use std::time::{Duration, Instant};

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

#[test]
fn kimi_is_admitted() {
    let f = Fixture::new("printf kimi > allowed/new");
    let mut config = f.config();
    config.provider.provider = Provider::Kimi;
    let mut packet = f.packet();
    packet["provider"] = "kimi".into();
    assert!(run(&validate(&packet), &config).is_ok());
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
