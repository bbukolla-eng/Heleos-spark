#![cfg(unix)]

mod common;
use common::Fixture;
use std::fs;
use std::process::Command;

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
