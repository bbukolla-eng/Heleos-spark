use serde_json::{Value, json};

pub const CANONICAL_TASK: &str = r#"{"acceptance_commands":["cargo test -p example"],"allowed_paths":["crates/example"],"base_commit":"0123456789abcdef0123456789abcdef01234567","egress_policy":"local_only","forbidden_paths":["crates/example/secrets"],"input_data_class":"PUBLIC","instruction_sha256":{"AGENTS.md":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"limits":{"max_actions":20,"max_duration_seconds":600},"mode":"implementation","objective":"Implement the public example.","provider":"codex","schema":"heleos.worker-task/v1","task_id":"example-001"}"#;

pub fn task() -> Value {
    serde_json::from_str(CANONICAL_TASK).unwrap()
}

pub fn handoff(task_digest: &str) -> Value {
    json!({
        "schema": "heleos.worker-handoff/v1",
        "task_digest": task_digest,
        "provider": "codex",
        "terminal_state": "completed",
        "changed_paths": ["crates/example/src/lib.rs"],
        "checks": [{
            "command": "cargo test -p example",
            "exit_code": 0,
            "output_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        }],
        "candidate_commit": "abcdef0123456789abcdef0123456789abcdef01",
        "unresolved_items": []
    })
}

pub fn bytes(value: &Value) -> Vec<u8> {
    serde_json::to_vec(value).unwrap()
}
