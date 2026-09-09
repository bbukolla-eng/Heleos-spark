mod common;

use common::{CANONICAL_TASK, bytes, handoff, task};
use heleos_worker_protocol::{validate_handoff_json, validate_task_json};
use serde_json::{Value, json};

fn reject_task(value: Value) {
    assert!(
        validate_task_json(&bytes(&value)).is_err(),
        "accepted invalid task: {value}"
    );
}

fn reject_handoff(value: Value) {
    let original = validate_task_json(&bytes(&task())).unwrap();
    assert!(
        validate_handoff_json(&bytes(&value), &original).is_err(),
        "accepted invalid handoff: {value}"
    );
}

#[test]
fn canonical_identity_ignores_object_order_and_whitespace() {
    let document = task();
    let validated = validate_task_json(&serde_json::to_vec_pretty(&document).unwrap()).unwrap();
    assert_eq!(validated.canonical_json(), CANONICAL_TASK.as_bytes());
    // Independent SHA-256 of the literal JCS fixture, computed with shasum.
    assert_eq!(
        validated.digest(),
        "688d224c29cbd81267681fe61efd20eef39a99ababbde7b61049f18658efb903"
    );
    assert_eq!(validated.digest().len(), 64);
    assert!(
        validated
            .digest()
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    );
    assert_eq!(
        validated.digest(),
        validate_task_json(CANONICAL_TASK.as_bytes())
            .unwrap()
            .digest()
    );
    let mut changed = document;
    changed["objective"] = json!("A different public task.");
    assert_ne!(
        validated.digest(),
        validate_task_json(&bytes(&changed)).unwrap().digest()
    );
}

#[test]
fn rejects_unknown_fields_at_each_task_level() {
    for pointer in [
        "/unexpected",
        "/limits/unexpected",
        "/instruction_sha256/../escape",
    ] {
        let mut value = task();
        let (parent, key) = pointer.rsplit_once('/').unwrap();
        if pointer.contains("../") {
            value["instruction_sha256"]["../escape"] = json!("a".repeat(64));
        } else {
            value.pointer_mut(parent).unwrap()[key] = json!(1);
        }
        reject_task(value);
    }
}

#[test]
fn rejects_duplicate_json_keys_in_structs_and_instruction_map() {
    let raw = CANONICAL_TASK.replace("\"task_id\":", "\"task_id\":\"duplicate\",\"task_id\":");
    assert!(validate_task_json(raw.as_bytes()).is_err());
    let raw = CANONICAL_TASK.replace(
        "\"AGENTS.md\":",
        &format!("\"AGENTS.md\":\"{}\",\"AGENTS.md\":", "c".repeat(64)),
    );
    assert!(validate_task_json(raw.as_bytes()).is_err());
}

#[test]
fn rejects_missing_fields_wrong_types_trailing_json_and_oversized_input() {
    let baseline = task();
    for field in baseline.as_object().unwrap().keys() {
        let mut value = baseline.clone();
        value.as_object_mut().unwrap().remove(field);
        reject_task(value);
    }
    for raw in [b"[]".as_slice(), b"null", b"{} {}", b"not json", b"\xff"] {
        assert!(validate_task_json(raw).is_err());
    }
    assert!(validate_task_json(&vec![b' '; 1_048_577]).is_err());
}

#[test]
fn rejects_invalid_schema_ids_base_commits_objectives_and_instruction_hashes() {
    for (field, invalid) in [
        ("schema", json!("heleos.worker-task/v2")),
        ("task_id", json!("")),
        ("task_id", json!("task id")),
        ("task_id", json!("x".repeat(129))),
        (
            "base_commit",
            json!("ABCDEF0123456789abcdef0123456789abcdef0123"),
        ),
        ("base_commit", json!("a".repeat(39))),
        ("base_commit", json!("g".repeat(40))),
        ("objective", json!(" \n ")),
        ("objective", json!("x".repeat(4097))),
        ("objective", json!("text\u{0000}other")),
        ("instruction_sha256", json!({})),
        ("instruction_sha256", json!({"AGENTS.md": "A".repeat(64)})),
        ("instruction_sha256", json!({"AGENTS.md": "a".repeat(63)})),
    ] {
        let mut value = task();
        value[field] = invalid;
        reject_task(value);
    }
}

#[test]
fn provider_mode_and_data_egress_policy_fail_closed() {
    for provider in [
        "codex",
        "claude_code",
        "kimi",
        "grok",
        "cursor",
        "notebook_lm",
        "grok_bots",
    ] {
        for mode in ["implementation", "research"] {
            let mut value = task();
            value["provider"] = json!(provider);
            value["mode"] = json!(mode);
            let allowed = mode == "research" || !["notebook_lm", "grok_bots"].contains(&provider);
            assert_eq!(
                validate_task_json(&bytes(&value)).is_ok(),
                allowed,
                "{provider}/{mode}"
            );
        }
    }
    for class in [
        "PUBLIC",
        "INTERNAL",
        "PROJECT_CONFIDENTIAL",
        "SECRET",
        "unknown",
    ] {
        for policy in ["local_only", "approved_external", "unrestricted"] {
            let mut value = task();
            value["input_data_class"] = json!(class);
            value["egress_policy"] = json!(policy);
            let allowed = class == "PUBLIC"
                && ["local_only", "approved_external"].contains(&policy)
                || ["INTERNAL", "PROJECT_CONFIDENTIAL"].contains(&class) && policy == "local_only";
            assert_eq!(
                validate_task_json(&bytes(&value)).is_ok(),
                allowed,
                "{class}/{policy}"
            );
        }
    }
    for (field, invalid) in [("provider", "any"), ("provider", ""), ("mode", "review")] {
        let mut value = task();
        value[field] = json!(invalid);
        reject_task(value);
    }
}

#[test]
fn paths_are_sorted_unique_relative_and_portable() {
    for paths in [
        json!(["b", "a"]),
        json!(["a", "a"]),
        json!([""]),
        json!(["."]),
        json!(["../a"]),
        json!(["a/../b"]),
        json!(["a/./b"]),
        json!(["/a"]),
        json!(["a//b"]),
        json!(["a/"]),
        json!(["C:/a"]),
        json!(["a\\b"]),
        json!(["a/*"]),
        json!(["a/\n"]),
        json!(["a\u{0000}b"]),
        json!(["a "]),
        json!(["a."]),
        json!(["CON"]),
        json!(["aux.txt"]),
        json!(["x".repeat(1025)]),
    ] {
        for field in ["allowed_paths", "forbidden_paths"] {
            let mut value = task();
            value[field] = paths.clone();
            reject_task(value);
        }
    }
    let mut value = task();
    value["allowed_paths"] = json!([]);
    reject_task(value);
    let mut value = task();
    value["forbidden_paths"] = json!([]);
    assert!(validate_task_json(&bytes(&value)).is_ok());
}

#[test]
fn limits_and_acceptance_commands_are_bounded_and_nonzero() {
    for field in ["max_actions", "max_duration_seconds"] {
        for invalid in [json!(0), json!(-1), json!(1.5), json!(4_294_967_296_u64)] {
            let mut value = task();
            value["limits"][field] = invalid;
            reject_task(value);
        }
    }
    for invalid in [
        json!([]),
        json!([""]),
        json!(["same", "same"]),
        json!(["x".repeat(4097)]),
        json!(["bad\ncommand"]),
    ] {
        let mut value = task();
        value["acceptance_commands"] = invalid;
        reject_task(value);
    }
    let mut value = task();
    value["allowed_paths"] = json!(vec!["a"; 257]);
    reject_task(value);
}

#[test]
fn handoff_binds_task_provider_and_exact_path_boundaries() {
    let original = validate_task_json(&bytes(&task())).unwrap();
    let valid = handoff(original.digest());
    let checked = validate_handoff_json(&bytes(&valid), &original).unwrap();
    assert_eq!(checked.digest().len(), 64);
    for (field, invalid) in [
        ("schema", json!("heleos.worker-handoff/v2")),
        ("task_digest", json!("a".repeat(64))),
        ("provider", json!("kimi")),
        ("terminal_state", json!("running")),
        ("changed_paths", json!(["crates/examples/lib.rs"])),
        ("changed_paths", json!(["crates/example/secrets/key.txt"])),
        ("changed_paths", json!(["crates/example/secrets"])),
        ("changed_paths", json!(["crates/example/../outside"])),
        (
            "changed_paths",
            json!(["crates/example/z", "crates/example/a"]),
        ),
        (
            "changed_paths",
            json!(["crates/example/a", "crates/example/a"]),
        ),
        ("candidate_commit", json!("HEAD")),
    ] {
        let mut value = valid.clone();
        value[field] = invalid;
        reject_handoff(value);
    }
    let mut value = valid;
    value["changed_paths"] = json!(["crates/example/secrets-public.txt"]);
    assert!(validate_handoff_json(&bytes(&value), &original).is_ok());
}

#[test]
fn completed_handoffs_require_successful_declared_checks_and_no_unresolved_items() {
    let original = validate_task_json(&bytes(&task())).unwrap();
    let valid = handoff(original.digest());
    for invalid in [
        json!([]),
        json!([valid["checks"][0].clone(), valid["checks"][0].clone()]),
    ] {
        let mut value = valid.clone();
        value["checks"] = invalid;
        reject_handoff(value);
    }
    for (field, invalid) in [
        ("command", json!("unassigned check")),
        ("exit_code", json!(1)),
        ("output_sha256", json!("invalid")),
    ] {
        let mut value = valid.clone();
        value["checks"][0][field] = invalid;
        reject_handoff(value);
    }
    let mut value = valid.clone();
    value["unresolved_items"] = json!(["Still unfinished"]);
    reject_handoff(value);
    for state in ["blocked", "failed", "cancelled"] {
        let mut value = valid.clone();
        value["terminal_state"] = json!(state);
        value["checks"] = json!([]);
        value["candidate_commit"] = Value::Null;
        value["unresolved_items"] = json!(["Public explanation"]);
        assert!(validate_handoff_json(&bytes(&value), &original).is_ok());
        value["unresolved_items"] = json!([]);
        reject_handoff(value);
    }
    let mut value = valid;
    value.as_object_mut().unwrap().remove("candidate_commit");
    assert!(validate_handoff_json(&bytes(&value), &original).is_ok());
}

#[test]
fn handoff_unknown_fields_duplicates_and_unbounded_text_fail_closed() {
    let original = validate_task_json(&bytes(&task())).unwrap();
    let valid = handoff(original.digest());
    let mut value = valid.clone();
    value["extra"] = json!(true);
    reject_handoff(value);
    let mut value = valid.clone();
    value["checks"][0]["extra"] = json!(true);
    reject_handoff(value);
    let mut value = valid.clone();
    value["terminal_state"] = json!("failed");
    value["unresolved_items"] = json!(["x".repeat(4097)]);
    reject_handoff(value);
    let raw = String::from_utf8(bytes(&valid))
        .unwrap()
        .replace("\"provider\":", "\"provider\":\"kimi\",\"provider\":");
    assert!(validate_handoff_json(raw.as_bytes(), &original).is_err());
    for field in valid
        .as_object()
        .unwrap()
        .keys()
        .filter(|key| key.as_str() != "candidate_commit")
    {
        let mut value = valid.clone();
        value.as_object_mut().unwrap().remove(field);
        reject_handoff(value);
    }
}
