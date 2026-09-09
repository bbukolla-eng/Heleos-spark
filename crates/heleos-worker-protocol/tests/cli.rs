mod common;

use common::{CANONICAL_TASK, bytes, handoff, task};
use serde_json::{Value, json};
use std::fs;
use std::path::Path;
use std::process::{Command, Output};

fn run(root: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_heleos-worker-protocol"))
        .current_dir(root)
        .args(args)
        .output()
        .unwrap()
}

fn envelope(output: &Output) -> Value {
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(output.stderr.is_empty());
    assert_eq!(
        output.stdout.iter().filter(|byte| **byte == b'\n').count(),
        1
    );
    let result: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(result["schema"], "heleos.worker-validation/v1");
    assert_eq!(result["valid"], true);
    assert_eq!(result["digest"].as_str().unwrap().len(), 64);
    result
}

fn rejected(output: Output) {
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert!(!output.stderr.is_empty());
    assert!(output.stderr.len() <= 512);
    assert_eq!(
        output.stderr.iter().filter(|byte| **byte == b'\n').count(),
        1
    );
}

#[test]
fn task_and_handoff_identities_are_stable_across_two_physical_roots() {
    let left = tempfile::tempdir().unwrap();
    let right = tempfile::tempdir().unwrap();
    fs::write(left.path().join("task.json"), CANONICAL_TASK).unwrap();
    fs::write(
        right.path().join("task.json"),
        serde_json::to_vec_pretty(&task()).unwrap(),
    )
    .unwrap();
    let left_task = run(left.path(), &["task", "task.json"]);
    let task_result = envelope(&left_task);
    assert_eq!(task_result["document_schema"], "heleos.worker-task/v1");
    assert_eq!(
        left_task.stdout,
        run(right.path(), &["task", "task.json"]).stdout
    );
    let packet = bytes(&handoff(task_result["digest"].as_str().unwrap()));
    for root in [left.path(), right.path()] {
        fs::write(root.join("handoff.json"), &packet).unwrap();
    }
    let left_handoff = run(
        left.path(),
        &["handoff", "--task", "task.json", "handoff.json"],
    );
    let result = envelope(&left_handoff);
    assert_eq!(result["document_schema"], "heleos.worker-handoff/v1");
    assert_eq!(
        left_handoff.stdout,
        run(
            right.path(),
            &["handoff", "--task", "task.json", "handoff.json"]
        )
        .stdout
    );
}

#[test]
fn rejects_invalid_files_mismatch_and_arguments_without_echoing_input() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("task.json"), CANONICAL_TASK).unwrap();
    let mut invalid = task();
    invalid["unexpected_private_text"] = json!("DO_NOT_ECHO_ME");
    fs::write(root.path().join("bad.json"), bytes(&invalid)).unwrap();
    let output = run(root.path(), &["task", "bad.json"]);
    assert!(!String::from_utf8_lossy(&output.stderr).contains("DO_NOT_ECHO_ME"));
    rejected(output);
    fs::write(
        root.path().join("handoff.json"),
        bytes(&handoff(&"0".repeat(64))),
    )
    .unwrap();
    for args in [
        vec!["task", "missing.json"],
        vec!["task", "."],
        vec!["handoff", "--task", "task.json", "handoff.json"],
        vec!["task"],
        vec!["unsupported"],
        vec!["task", "task.json", "extra"],
    ] {
        rejected(run(root.path(), &args));
    }
    rejected(run(root.path(), &[&"x".repeat(20_000)]));
}

#[test]
fn rejects_oversized_documents_with_bounded_diagnostics() {
    let root = tempfile::tempdir().unwrap();
    fs::write(root.path().join("big.json"), vec![b' '; 1_048_577]).unwrap();
    rejected(run(root.path(), &["task", "big.json"]));
}

#[test]
fn acceptance_commands_and_input_paths_are_never_executed() {
    let root = tempfile::tempdir().unwrap();
    let mut packet = task();
    packet["acceptance_commands"] = json!(["touch COMMAND_EXECUTED"]);
    let filename = "task;touch PATH_EXECUTED.json";
    let original = bytes(&packet);
    fs::write(root.path().join(filename), &original).unwrap();
    envelope(&run(root.path(), &["task", filename]));
    assert_eq!(fs::read(root.path().join(filename)).unwrap(), original);
    assert!(!root.path().join("COMMAND_EXECUTED").exists());
    assert!(!root.path().join("PATH_EXECUTED.json").exists());
    assert_eq!(fs::read_dir(root.path()).unwrap().count(), 1);
}
