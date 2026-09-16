#![cfg(unix)]

mod common;
use common::Fixture;
use std::fs;
use std::process::Command;

#[cfg(target_os = "macos")]
#[test]
fn internal_claude_cli_requires_exact_controller_grant_and_containment() {
    use sha2::{Digest, Sha256};
    for scenario in [
        "missing",
        "wrong",
        "no-containment",
        "confidential",
        "approved",
        "out-of-scope",
    ] {
        let f = Fixture::new(if scenario == "out-of-scope" {
            "printf forbidden > protected.txt"
        } else {
            "printf authorized > allowed/new.txt"
        });
        let mut packet = f.packet();
        packet["input_data_class"] = if scenario == "confidential" {
            "PROJECT_CONFIDENTIAL"
        } else {
            "INTERNAL"
        }
        .into();
        packet["egress_policy"] = "approved_external".into();
        let raw = serde_json::to_vec(&packet).unwrap();
        let grant = Sha256::digest(&raw)
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>();
        let packet_path = f.root.path().join("task.json");
        fs::write(&packet_path, &raw).unwrap();
        let mut command = Command::new(env!("CARGO_BIN_EXE_heleos-worker-runner"));
        command
            .arg("--task")
            .arg(&packet_path)
            .arg("--source")
            .arg(&f.source)
            .arg("--workspace-root")
            .arg(&f.workspace)
            .args(["--provider", "claude_code", "--git", "/usr/bin/git"])
            .arg("--command")
            .arg(&f.provider)
            .args([
                "--containment",
                if scenario == "no-containment" {
                    "none"
                } else {
                    "macos_seatbelt"
                },
            ]);
        if scenario != "missing" {
            command
                .arg("--approved-internal-task-sha256")
                .arg(if scenario == "wrong" {
                    "0".repeat(64)
                } else {
                    grant.clone()
                });
        }
        let output = command.output().unwrap();
        let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
        if scenario == "approved" {
            assert!(output.status.success(), "{value}");
            assert_eq!(value["approved_internal_task_sha256"], grant);
            assert_eq!(value["containment"]["mode"], "macos_seatbelt");
            let run = std::path::Path::new(value["run_directory"].as_str().unwrap());
            assert_eq!(fs::read(run.join("task.original.json")).unwrap(), raw);
            assert_eq!(
                fs::read_to_string(run.join("approved-internal-task.sha256")).unwrap(),
                grant
            );
            assert_eq!(
                fs::read(run.join("checkout/allowed/new.txt")).unwrap(),
                b"authorized"
            );
        } else {
            assert_eq!(output.status.code(), Some(2), "{scenario}: {value}");
            if scenario == "out-of-scope" {
                assert_eq!(value["code"], "out_of_scope");
            } else {
                f.assert_cleaned(); // No checkout or provider launch on denied approval.
            }
        }
        assert_eq!(
            fs::read(f.source.join("protected.txt")).unwrap(),
            b"protected bytes\n"
        );
        assert!(!f.source.join("allowed/new.txt").exists());
    }
}

#[test]
fn prevalidated_internal_task_cannot_transfer_approval_to_another_runner_context() {
    use sha2::{Digest, Sha256};
    let f = Fixture::new("printf unexpected > allowed/new.txt");
    let mut packet = f.packet();
    packet["input_data_class"] = "INTERNAL".into();
    packet["egress_policy"] = "approved_external".into();
    let raw = serde_json::to_vec(&packet).unwrap();
    let task = heleos_worker_protocol::validate_task_json_with_internal_claude_approval(
        &raw,
        &Sha256::digest(&raw)
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>(),
    )
    .unwrap();
    let mut config = f.config();
    assert_eq!(
        heleos_worker_runner::run(&task, &config).unwrap_err().code,
        heleos_worker_runner::FailureCode::InvalidTask
    );
    config.approved_internal_task_sha256 = Some(
        Sha256::digest(task.canonical_json())
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>(),
    );
    assert_eq!(
        heleos_worker_runner::run(&task, &config).unwrap_err().code,
        heleos_worker_runner::FailureCode::InvalidConfiguration
    );
    f.assert_cleaned();
}

#[test]
fn cli_reads_packet_and_retains_checkout_with_protocol_handoff() {
    let f = Fixture::new("printf cli > allowed/new.txt");
    let packet_path = f.root.path().join("task.json");
    fs::write(&packet_path, serde_json::to_vec(&f.packet()).unwrap()).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_heleos-worker-runner"))
        .arg("--task")
        .arg(&packet_path)
        .arg("--source")
        .arg(&f.source)
        .arg("--workspace-root")
        .arg(&f.workspace)
        .args(["--provider", "claude_code", "--git", "/usr/bin/git"])
        .arg("--command")
        .arg(&f.provider)
        .output()
        .unwrap();
    assert!(output.status.success(), "{output:?}");
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["schema"], "heleos.worker-run/v1");
    assert_eq!(value["containment"]["mode"], "none");
    assert_eq!(value["containment"]["host_path_writes_restricted"], false);
    let checkout = std::path::Path::new(value["checkout_path"].as_str().unwrap());
    assert_eq!(fs::read(checkout.join("allowed/new.txt")).unwrap(), b"cli");
    heleos_worker_protocol::validate_handoff_json(
        &serde_json::to_vec(&value["handoff"]).unwrap(),
        &f.task(),
    )
    .unwrap();
}

// Break caught: the parser must admit the new name and execute must map it to
// Provider::Grok rather than silently using the Claude default branch.
#[test]
fn cli_grok_maps_to_implementation_provider_and_retains_handoff() {
    let f = Fixture::new("printf grok-cli-fixture > allowed/new.txt");
    let mut packet = f.packet();
    packet["provider"] = "grok".into();
    let task = common::validate(&packet);
    let packet_path = f.root.path().join("task.json");
    fs::write(&packet_path, serde_json::to_vec(&packet).unwrap()).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_heleos-worker-runner"))
        .arg("--task")
        .arg(&packet_path)
        .arg("--source")
        .arg(&f.source)
        .arg("--workspace-root")
        .arg(&f.workspace)
        .args(["--provider", "grok", "--git", "/usr/bin/git"])
        .arg("--command")
        .arg(&f.provider)
        .output()
        .unwrap();
    assert!(output.status.success(), "{output:?}");
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["handoff"]["provider"], "grok");
    let checkout = std::path::Path::new(value["checkout_path"].as_str().unwrap());
    assert_eq!(
        fs::read(checkout.join("allowed/new.txt")).unwrap(),
        b"grok-cli-fixture"
    );
    heleos_worker_protocol::validate_handoff_json(
        &serde_json::to_vec(&value["handoff"]).unwrap(),
        &task,
    )
    .unwrap();
}

#[test]
fn cli_codex_adapter_transports_prompt_and_retains_codex_handoff() {
    let f = Fixture::new("/bin/cat > allowed/prompt\nprintf codex-cli-fixture > allowed/new.txt");
    let mut packet = f.packet();
    packet["provider"] = "codex".into();
    let task = common::validate(&packet);
    let packet_path = f.root.path().join("task.json");
    fs::write(&packet_path, serde_json::to_vec(&packet).unwrap()).unwrap();
    let adapter = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../scripts/provider-adapters/codex-stdin.py")
        .canonicalize()
        .unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_heleos-worker-runner"))
        .arg("--task")
        .arg(&packet_path)
        .arg("--source")
        .arg(&f.source)
        .arg("--workspace-root")
        .arg(&f.workspace)
        .args(["--provider", "codex", "--git", "/usr/bin/git"])
        .args(["--command", "/usr/bin/python3", "--", "-B"])
        .arg(&adapter)
        .arg("--codex-executable")
        .arg(&f.provider)
        .output()
        .unwrap();
    assert!(output.status.success(), "{output:?}");
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["handoff"]["provider"], "codex");
    assert_eq!(value["handoff"]["terminal_state"], "blocked");
    let checkout = std::path::Path::new(value["checkout_path"].as_str().unwrap());
    let directory = std::path::Path::new(value["run_directory"].as_str().unwrap());
    assert_eq!(
        fs::read(checkout.join("allowed/new.txt")).unwrap(),
        b"codex-cli-fixture"
    );
    assert_eq!(
        fs::read(checkout.join("allowed/prompt")).unwrap(),
        fs::read(directory.join("prompt.txt")).unwrap()
    );
    heleos_worker_protocol::validate_handoff_json(
        &serde_json::to_vec(&value["handoff"]).unwrap(),
        &task,
    )
    .unwrap();
}

#[test]
fn cli_research_providers_fail_closed_before_command_runs() {
    for provider in ["notebook_lm", "grok_bots"] {
        let f = Fixture::new("printf unexpected > \"$1\"");
        let marker = f.root.path().join("provider-started");
        let mut packet = f.packet();
        packet["provider"] = provider.into();
        let packet_path = f.root.path().join("task.json");
        fs::write(&packet_path, serde_json::to_vec(&packet).unwrap()).unwrap();
        let output = Command::new(env!("CARGO_BIN_EXE_heleos-worker-runner"))
            .arg("--task")
            .arg(&packet_path)
            .arg("--source")
            .arg(&f.source)
            .arg("--workspace-root")
            .arg(&f.workspace)
            .args(["--provider", provider, "--git", "/usr/bin/git"])
            .arg("--command")
            .arg(&f.provider)
            .arg("--")
            .arg(&marker)
            .output()
            .unwrap();
        assert_eq!(output.status.code(), Some(2));
        let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(value["code"], "invalid_configuration");
        assert!(!marker.exists());
        f.assert_cleaned();
    }
}

// Break caught: parsing Cursor as another provider or dropping its stdin bytes.
#[test]
fn cli_cursor_adapter_transports_prompt_and_retains_cursor_handoff() {
    let f = Fixture::new("/bin/cat > allowed/prompt\nprintf cursor-cli-fixture > allowed/new.txt");
    // Cursor rejects long runtime roots to prevent its /tmp/.cursor fallback.
    let cursor_workspace = tempfile::Builder::new()
        .prefix("cursor-")
        .tempdir_in("/tmp")
        .unwrap();
    let cursor_workspace_path = cursor_workspace.path().canonicalize().unwrap();
    let mut packet = f.packet();
    packet["provider"] = "cursor".into();
    let task = common::validate(&packet);
    let packet_path = f.root.path().join("task.json");
    fs::write(&packet_path, serde_json::to_vec(&packet).unwrap()).unwrap();
    let adapter = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../scripts/provider-adapters/cursor-stdin.py");
    let entrypoint = f.root.path().join("index.js");
    fs::write(&entrypoint, b"// inert entrypoint fixture\n").unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_heleos-worker-runner"))
        .arg("--task")
        .arg(&packet_path)
        .arg("--source")
        .arg(&f.source)
        .arg("--workspace-root")
        .arg(&cursor_workspace_path)
        .args(["--provider", "cursor", "--git", "/usr/bin/git"])
        .args(["--command", "/usr/bin/python3", "--", "-B"])
        .arg(&adapter)
        .arg("--cursor-node")
        .arg(&f.provider)
        .arg("--cursor-entrypoint")
        .arg(&entrypoint)
        .output()
        .unwrap();
    assert!(output.status.success(), "{output:?}");
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["handoff"]["provider"], "cursor");
    assert_eq!(value["handoff"]["terminal_state"], "blocked");
    let checkout = std::path::Path::new(value["checkout_path"].as_str().unwrap());
    let directory = std::path::Path::new(value["run_directory"].as_str().unwrap());
    assert_eq!(
        fs::read(checkout.join("allowed/new.txt")).unwrap(),
        b"cursor-cli-fixture"
    );
    assert_eq!(
        fs::read(checkout.join("allowed/prompt")).unwrap(),
        fs::read(directory.join("prompt.txt")).unwrap()
    );
    heleos_worker_protocol::validate_handoff_json(
        &serde_json::to_vec(&value["handoff"]).unwrap(),
        &task,
    )
    .unwrap();
}

// Omitting the kernel wrapper must fail these tests: the fixture deliberately
// attempts real host writes and refuses success when any outside write works.
#[cfg(target_os = "macos")]
#[test]
fn macos_containment_allows_roots_denies_host_and_descendant_writes_with_literal_paths() {
    let mut f = Fixture::new(
        r#"
printf checkout > allowed/new.txt
printf home > "$HOME/probe"
printf tmp > "$TMPDIR/probe"
printf '%s' "$3" > allowed/literal
if (printf forbidden > "$1") 2>allowed/denial; then exit 71; fi
if /bin/sh -c 'printf forbidden > "$1"' child "$2" 2>allowed/child-denial; then exit 72; fi
if (printf forbidden > ../sibling) 2>allowed/sibling-denial; then exit 73; fi
/bin/ln -s "$1" allowed/escape
if (printf forbidden > allowed/escape) 2>allowed/symlink-denial; then exit 74; fi
/bin/rm allowed/escape
"#,
    );
    let literal_root = f
        .root
        .path()
        .join("workspace ; $(touch INJECTED) ' \" literal");
    fs::rename(&f.workspace, &literal_root).unwrap();
    f.workspace = literal_root;
    let packet = f.root.path().join("task.json");
    fs::write(&packet, serde_json::to_vec(&f.packet()).unwrap()).unwrap();
    let outside = f.source.join("outside ; literal");
    let descendant = f.root.path().join("descendant");
    let literal = "$(touch INJECTED); `touch ALSO_INJECTED` $HOME";
    let output = Command::new(env!("CARGO_BIN_EXE_heleos-worker-runner"))
        .arg("--task")
        .arg(&packet)
        .arg("--source")
        .arg(&f.source)
        .arg("--workspace-root")
        .arg(&f.workspace)
        .args([
            "--provider",
            "claude_code",
            "--git",
            "/usr/bin/git",
            "--containment",
            "macos_seatbelt",
        ])
        .arg("--command")
        .arg(&f.provider)
        .arg("--")
        .arg(&outside)
        .arg(&descendant)
        .arg(literal)
        .output()
        .unwrap();
    assert!(output.status.success(), "{output:?}");
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["containment"]["mode"], "macos_seatbelt");
    assert_eq!(value["containment"]["host_path_writes_restricted"], true);
    assert_eq!(value["containment"]["reads_restricted"], false);
    assert_eq!(value["containment"]["network_restricted"], false);
    let directory = std::path::Path::new(value["run_directory"].as_str().unwrap());
    for (path, bytes) in [
        ("checkout/allowed/new.txt", "checkout"),
        ("home/probe", "home"),
        ("tmp/probe", "tmp"),
        ("checkout/allowed/literal", literal),
    ] {
        assert_eq!(fs::read_to_string(directory.join(path)).unwrap(), bytes);
    }
    assert!(!outside.exists());
    assert!(!descendant.exists());
    assert!(!directory.join("sibling").exists());
    assert!(!directory.join("checkout/INJECTED").exists());
    let retained: serde_json::Value =
        serde_json::from_slice(&fs::read(directory.join("run.json")).unwrap()).unwrap();
    assert_eq!(retained["containment"], value["containment"]);
    assert_eq!(value["handoff"]["terminal_state"], "blocked");
}

#[test]
fn cli_rejects_invalid_packet_without_echoing_untrusted_content() {
    let f = Fixture::new("exit 99");
    let packet_path = f.root.path().join("task.json");
    fs::write(&packet_path, b"PRIVATE_SENTINEL invalid JSON").unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_heleos-worker-runner"))
        .arg("--task")
        .arg(&packet_path)
        .arg("--source")
        .arg(&f.source)
        .arg("--workspace-root")
        .arg(&f.workspace)
        .args(["--provider", "claude_code", "--git", "/usr/bin/git"])
        .arg("--command")
        .arg(&f.provider)
        .output()
        .unwrap();
    assert!(!output.status.success());
    let value: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["code"], "invalid_task");
    assert!(!String::from_utf8_lossy(&output.stdout).contains("PRIVATE_SENTINEL"));
    assert!(!String::from_utf8_lossy(&output.stderr).contains("PRIVATE_SENTINEL"));
    f.assert_cleaned();
}
