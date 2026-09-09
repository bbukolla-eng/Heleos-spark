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
    let checkout = std::path::Path::new(value["checkout_path"].as_str().unwrap());
    assert_eq!(fs::read(checkout.join("allowed/new.txt")).unwrap(), b"cli");
    heleos_worker_protocol::validate_handoff_json(
        &serde_json::to_vec(&value["handoff"]).unwrap(),
        &f.task(),
    )
    .unwrap();
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
