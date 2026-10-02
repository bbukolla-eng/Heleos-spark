//! Incremental, typed evidence from Claude's verbose JSONL stream.
//! Thinking content is never written. The raw stream is hashed while it is drained.

use crate::{FailureCode, HARD_BYTE_LIMIT, RunError};
use serde::Serialize;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub(crate) const TRACE_FILE: &str = "event-trace.jsonl";
pub(crate) const PROGRESS_FILE: &str = "trace-progress.json";

#[derive(Debug, Serialize)]
pub(crate) struct StreamTraceSummary {
    pub schema: &'static str,
    pub path: &'static str,
    pub sha256: String,
    pub retained_bytes: usize,
    pub raw_sha256: String,
    pub raw_bytes: u64,
    pub events_seen: u64,
    pub events_retained: u64,
    pub thinking_events_omitted: u64,
    pub malformed: bool,
    pub truncated: bool,
    pub complete: bool,
}

pub(crate) struct StreamTrace {
    file: File,
    progress: PathBuf,
    pending: Vec<u8>,
    oversize_line: bool,
    raw_hash: Sha256,
    trace_hash: Sha256,
    raw_bytes: u64,
    retained_bytes: usize,
    events_seen: u64,
    events_retained: u64,
    thinking_events_omitted: u64,
    malformed: bool,
    truncated: bool,
    last_type: Option<String>,
    terminal_success_seen: bool,
    last_progress: Instant,
    last_progress_events: u64,
}

fn io_error(_: std::io::Error) -> RunError {
    RunError::new(FailureCode::TraceIo)
}

fn hex(hash: Sha256) -> String {
    hash.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

impl StreamTrace {
    pub(crate) fn new(directory: &Path) -> Result<Self, RunError> {
        let file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(directory.join(TRACE_FILE))
            .map_err(io_error)?;
        let mut trace = Self {
            file,
            progress: directory.join(PROGRESS_FILE),
            pending: Vec::new(),
            oversize_line: false,
            raw_hash: Sha256::new(),
            trace_hash: Sha256::new(),
            raw_bytes: 0,
            retained_bytes: 0,
            events_seen: 0,
            events_retained: 0,
            thinking_events_omitted: 0,
            malformed: false,
            truncated: false,
            last_type: None,
            terminal_success_seen: false,
            last_progress: Instant::now(),
            last_progress_events: 0,
        };
        trace.progress(false)?;
        Ok(trace)
    }

    pub(crate) fn accept(&mut self, bytes: &[u8]) -> Result<(), RunError> {
        self.raw_hash.update(bytes);
        self.raw_bytes = self
            .raw_bytes
            .checked_add(bytes.len() as u64)
            .ok_or_else(|| RunError::new(FailureCode::TraceIo))?;
        for &byte in bytes {
            if byte == b'\n' {
                if self.oversize_line {
                    self.malformed = true;
                } else {
                    let line = std::mem::take(&mut self.pending);
                    self.accept_line(&line)?;
                }
                self.pending.clear();
                self.oversize_line = false;
            } else if !self.oversize_line {
                if self.pending.len() == HARD_BYTE_LIMIT {
                    self.pending.clear();
                    self.oversize_line = true;
                    self.malformed = true;
                } else {
                    self.pending.push(byte);
                }
            }
        }
        if self.last_progress.elapsed() >= Duration::from_secs(1) {
            self.progress(false)?;
        }
        Ok(())
    }

    fn accept_line(&mut self, line: &[u8]) -> Result<(), RunError> {
        self.events_seen = self
            .events_seen
            .checked_add(1)
            .ok_or_else(|| RunError::new(FailureCode::TraceIo))?;
        let Ok(event) = serde_json::from_slice::<Value>(line) else {
            self.malformed = true;
            return self.progress_if_due();
        };
        let kind = event.get("type").and_then(Value::as_str);
        let Some(kind) = kind else {
            self.malformed = true;
            return self.progress_if_due();
        };
        self.last_type = Some(kind.to_owned());
        if kind == "assistant"
            && event
                .get("message")
                .and_then(|message| message.get("content"))
                .and_then(Value::as_array)
                .is_some_and(|content| {
                    content.iter().any(|item| {
                        item.get("type").and_then(Value::as_str) == Some("tool_use")
                            && item.get("name").and_then(Value::as_str) == Some("Workflow")
                            && item
                                .get("input")
                                .and_then(|input| input.get("script"))
                                .and_then(Value::as_str)
                                .is_none()
                    })
                })
        {
            self.malformed = true;
        }
        if kind == "system"
            && event.get("subtype").and_then(Value::as_str) == Some("thinking_tokens")
        {
            self.thinking_events_omitted = self
                .thinking_events_omitted
                .checked_add(1)
                .ok_or_else(|| RunError::new(FailureCode::TraceIo))?;
        } else if let Some(projected) = project(&event) {
            let mut encoded =
                serde_json::to_vec(&projected).map_err(|_| RunError::new(FailureCode::TraceIo))?;
            encoded.push(b'\n');
            // A single record is bounded for parsing. The complete trace has
            // no aggregate size cutoff and is written incrementally to disk.
            if encoded.len() > HARD_BYTE_LIMIT {
                self.truncated = true;
            } else if !self.truncated {
                self.file.write_all(&encoded).map_err(io_error)?;
                self.file.sync_data().map_err(io_error)?;
                self.trace_hash.update(&encoded);
                self.retained_bytes = self
                    .retained_bytes
                    .checked_add(encoded.len())
                    .ok_or_else(|| RunError::new(FailureCode::TraceIo))?;
                self.events_retained = self
                    .events_retained
                    .checked_add(1)
                    .ok_or_else(|| RunError::new(FailureCode::TraceIo))?;
            }
        }
        if kind == "result" {
            self.terminal_success_seen = event.get("subtype").and_then(Value::as_str)
                == Some("success")
                && event.get("is_error").and_then(Value::as_bool) == Some(false);
        }
        if kind == "result"
            || (kind == "system"
                && matches!(
                    event.get("subtype").and_then(Value::as_str),
                    Some("task_started" | "task_notification")
                ))
        {
            self.progress(false)?;
            Ok(())
        } else {
            self.progress_if_due()
        }
    }

    fn progress_if_due(&mut self) -> Result<(), RunError> {
        if self.events_seen.saturating_sub(self.last_progress_events) >= 128
            || self.last_progress.elapsed() >= Duration::from_secs(1)
        {
            self.progress(false)?;
        }
        Ok(())
    }

    fn progress(&mut self, complete: bool) -> Result<(), RunError> {
        let updated_unix_seconds = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_err(|_| RunError::new(FailureCode::TraceIo))?
            .as_secs();
        let bytes = serde_json::to_vec(&json!({
            "schema": "heleos.claude-stream-progress/v1",
            "raw_bytes": self.raw_bytes,
            "events_seen": self.events_seen,
            "events_retained": self.events_retained,
            "thinking_events_omitted": self.thinking_events_omitted,
            "malformed": self.malformed,
            "truncated": self.truncated,
            "complete": complete,
            "updated_unix_seconds": updated_unix_seconds,
        }))
        .map_err(|_| RunError::new(FailureCode::TraceIo))?;
        let scratch = self.progress.with_extension("json.tmp");
        fs::write(&scratch, bytes).map_err(io_error)?;
        fs::rename(scratch, &self.progress).map_err(io_error)?;
        self.last_progress = Instant::now();
        self.last_progress_events = self.events_seen;
        Ok(())
    }

    pub(crate) fn finish(mut self) -> Result<StreamTraceSummary, RunError> {
        if !self.pending.is_empty() || self.oversize_line {
            self.malformed = true;
        }
        let complete = !self.malformed
            && !self.truncated
            && self.terminal_success_seen
            && self.last_type.as_deref() == Some("result");
        self.file.sync_data().map_err(io_error)?;
        self.progress(complete)?;
        Ok(StreamTraceSummary {
            schema: "heleos.claude-stream-trace/v1",
            path: TRACE_FILE,
            sha256: hex(self.trace_hash),
            retained_bytes: self.retained_bytes,
            raw_sha256: hex(self.raw_hash),
            raw_bytes: self.raw_bytes,
            events_seen: self.events_seen,
            events_retained: self.events_retained,
            thinking_events_omitted: self.thinking_events_omitted,
            malformed: self.malformed,
            truncated: self.truncated,
            complete,
        })
    }
}

fn project(event: &Value) -> Option<Value> {
    match event.get("type")?.as_str()? {
        "assistant" => {
            let message = event.get("message")?;
            let tools: Vec<Value> = message
                .get("content")?
                .as_array()?
                .iter()
                .filter_map(|item| {
                    if item.get("type")?.as_str()? != "tool_use" {
                        return None;
                    }
                    let name = item.get("name")?.as_str()?;
                    let mut tool = json!({"type": "tool_use", "name": name,
                                      "id": item.get("id")?});
                    if name == "Workflow" {
                        let script = item.get("input")?.get("script")?.as_str()?;
                        tool["input"] = json!({"script": script});
                    }
                    Some(tool)
                })
                .collect();
            if tools.is_empty() {
                None
            } else {
                Some(json!({"type": "assistant", "message": {
                    "model": message.get("model"), "content": tools}}))
            }
        }
        "system" => {
            let subtype = event.get("subtype")?.as_str()?;
            if !matches!(
                subtype,
                "task_started" | "task_progress" | "task_notification"
            ) {
                return None;
            }
            let agents: Vec<Value> = event
                .get("workflow_progress")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(|item| {
                    if item.get("type")?.as_str()? != "workflow_agent" {
                        return None;
                    }
                    Some(
                        json!({"type": "workflow_agent", "agentId": item.get("agentId"),
                        "label": item.get("label"), "model": item.get("model"),
                        "state": item.get("state"), "lastToolName": item.get("lastToolName")}),
                    )
                })
                .collect();
            Some(json!({"type": "system", "subtype": subtype,
                "tool_use_id": event.get("tool_use_id"), "task_id": event.get("task_id"),
                "status": event.get("status"), "output_file": event.get("output_file"),
                "workflow_progress": agents}))
        }
        "result" => Some(json!({"type": "result", "subtype": event.get("subtype"),
            "is_error": event.get("is_error"), "modelUsage": event.get("modelUsage")})),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn event(value: Value) -> Vec<u8> {
        let mut bytes = serde_json::to_vec(&value).unwrap();
        bytes.push(b'\n');
        bytes
    }

    #[test]
    fn workflow_projection_keeps_only_script_and_rejects_missing_script() {
        let dir = tempfile::tempdir().unwrap();
        let mut sink = StreamTrace::new(dir.path()).unwrap();
        sink.accept(&event(
            json!({"type":"assistant", "message":{"model":"claude-opus-5-5",
            "content":[{"type":"tool_use", "name":"Workflow", "id":"w1",
                "input":{"script":"phase('Audit')", "private_text":"secret marker"}}]}}),
        ))
        .unwrap();
        sink.accept(&event(
            json!({"type":"result", "subtype":"success", "is_error":false}),
        ))
        .unwrap();
        assert!(sink.finish().unwrap().complete);
        let trace = fs::read_to_string(dir.path().join(TRACE_FILE)).unwrap();
        assert!(trace.contains("phase('Audit')"));
        assert!(!trace.contains("secret marker"));
        assert!(!trace.contains("private_text"));

        let missing = tempfile::tempdir().unwrap();
        let mut sink = StreamTrace::new(missing.path()).unwrap();
        sink.accept(&event(json!({"type":"assistant", "message":{"content":[
            {"type":"tool_use", "name":"Workflow", "id":"w1",
                "input":{"private_text":"secret marker"}}]}})))
            .unwrap();
        sink.accept(&event(
            json!({"type":"result", "subtype":"success", "is_error":false}),
        ))
        .unwrap();
        let summary = sink.finish().unwrap();
        assert!(summary.malformed);
        assert!(!summary.complete);
        assert!(
            !fs::read_to_string(missing.path().join(TRACE_FILE))
                .unwrap()
                .contains("secret marker")
        );
    }

    #[test]
    fn large_thinking_stream_preserves_workflow_and_terminal_without_reasoning_text() {
        let dir = tempfile::tempdir().unwrap();
        let mut sink = StreamTrace::new(dir.path()).unwrap();
        let mut raw = Vec::new();
        for _ in 0..3500 {
            raw.extend(event(json!({"type":"system", "subtype":"thinking_tokens",
                "private_thinking":"do not retain this string", "padding":"x".repeat(256)})));
        }
        raw.extend(event(
            json!({"type":"assistant", "message":{"model":"claude-opus-5-5",
            "content":[{"type":"thinking", "thinking":"hidden thought"},
                {"type":"tool_use","name":"Workflow","id":"w1",
                 "input":{"script":"phase('Audit')"}}]}}),
        ));
        raw.extend(event(json!({"type":"system", "subtype":"task_started",
            "tool_use_id":"w1", "task_id":"t1"})));
        raw.extend(event(json!({"type":"system", "subtype":"task_progress",
            "tool_use_id":"w1", "task_id":"t1", "workflow_progress":[
                {"type":"workflow_agent", "agentId":"a1", "label":"audit",
                 "model":"claude-opus-5-5", "state":"done", "lastToolName":"Write"}]})));
        raw.extend(event(
            json!({"type":"system", "subtype":"task_notification",
            "tool_use_id":"w1", "task_id":"t1", "status":"completed",
            "output_file":"/tmp/audit"}),
        ));
        raw.extend(event(json!({"type":"result", "subtype":"success",
            "is_error":false, "modelUsage":{"claude-opus-5-5":{}}})));
        for chunk in raw.chunks(911) {
            sink.accept(chunk).unwrap();
        }
        let summary = sink.finish().unwrap();
        let trace = fs::read(dir.path().join(TRACE_FILE)).unwrap();
        assert!(raw.len() > HARD_BYTE_LIMIT);
        assert!(summary.complete);
        assert_eq!(summary.raw_bytes, raw.len() as u64);
        assert_eq!(summary.thinking_events_omitted, 3500);
        assert_eq!(summary.events_retained, 5);
        assert!(summary.retained_bytes < 4096);
        assert!(String::from_utf8_lossy(&trace).contains("\"Workflow\""));
        assert!(String::from_utf8_lossy(&trace).contains("\"result\""));
        assert!(!String::from_utf8_lossy(&trace).contains("hidden thought"));
        assert!(!String::from_utf8_lossy(&trace).contains("do not retain this string"));
        let mut expected = Sha256::new();
        expected.update(&raw);
        assert_eq!(summary.raw_sha256, hex(expected));
    }

    #[test]
    fn progress_is_visible_before_completion_and_malformed_or_missing_terminal_fails() {
        let dir = tempfile::tempdir().unwrap();
        let mut sink = StreamTrace::new(dir.path()).unwrap();
        let thought = event(json!({"type":"system", "subtype":"thinking_tokens"}));
        for _ in 0..128 {
            sink.accept(&thought).unwrap();
        }
        let progress: Value =
            serde_json::from_slice(&fs::read(dir.path().join(PROGRESS_FILE)).unwrap()).unwrap();
        assert_eq!(progress["events_seen"], 128);
        assert_eq!(progress["thinking_events_omitted"], 128);
        assert_eq!(progress["complete"], false);
        assert!(!sink.finish().unwrap().complete);

        let bad = tempfile::tempdir().unwrap();
        let mut sink = StreamTrace::new(bad.path()).unwrap();
        sink.accept(b"not-json\n").unwrap();
        sink.accept(&event(
            json!({"type":"result", "subtype":"success", "is_error":false}),
        ))
        .unwrap();
        let summary = sink.finish().unwrap();
        assert!(summary.malformed);
        assert!(!summary.complete);

        let cut = tempfile::tempdir().unwrap();
        let mut sink = StreamTrace::new(cut.path()).unwrap();
        sink.accept(b"{\"type\":\"result\"").unwrap();
        assert!(!sink.finish().unwrap().complete);
    }

    #[test]
    fn sparse_live_stream_updates_progress_by_time() {
        let dir = tempfile::tempdir().unwrap();
        let mut sink = StreamTrace::new(dir.path()).unwrap();
        let thought = event(json!({"type":"system", "subtype":"thinking_tokens"}));
        sink.accept(&thought).unwrap();
        std::thread::sleep(Duration::from_millis(1100));
        sink.accept(&thought).unwrap();
        let progress: Value =
            serde_json::from_slice(&fs::read(dir.path().join(PROGRESS_FILE)).unwrap()).unwrap();
        assert_eq!(progress["events_seen"], 2);
        assert_eq!(progress["complete"], false);
    }

    #[test]
    fn per_record_overflow_and_disk_write_failures_are_explicit() {
        let huge = tempfile::tempdir().unwrap();
        let mut sink = StreamTrace::new(huge.path()).unwrap();
        let oversized = event(json!({"type":"system", "subtype":"thinking_tokens",
            "padding":"x".repeat(HARD_BYTE_LIMIT)}));
        for chunk in oversized.chunks(8192) {
            sink.accept(chunk).unwrap();
        }
        sink.accept(&event(
            json!({"type":"result", "subtype":"success", "is_error":false}),
        ))
        .unwrap();
        assert!(!sink.finish().unwrap().complete);

        let write_failure = tempfile::tempdir().unwrap();
        let mut sink = StreamTrace::new(write_failure.path()).unwrap();
        sink.file = File::open(write_failure.path()).unwrap();
        let error = sink
            .accept(&event(json!({"type":"result", "subtype":"success",
            "is_error":false})))
            .unwrap_err();
        assert_eq!(error.code, FailureCode::TraceIo);
        assert_eq!(error.summary()["evidence_write_failed"], true);

        let progress_failure = tempfile::tempdir().unwrap();
        let mut sink = StreamTrace::new(progress_failure.path()).unwrap();
        fs::create_dir(progress_failure.path().join("trace-progress.json.tmp")).unwrap();
        let error = sink.progress(false).unwrap_err();
        assert_eq!(error.code, FailureCode::TraceIo);
    }

    #[test]
    fn relevant_events_over_one_megabyte_preserve_child_and_terminal_evidence() {
        let dir = tempfile::tempdir().unwrap();
        let mut sink = StreamTrace::new(dir.path()).unwrap();
        let tool = event(
            json!({"type":"assistant", "message":{"model":"claude-opus-5-5",
            "content":[{"type":"tool_use", "name":"Read", "id":"x".repeat(200)}]}}),
        );
        for _ in 0..6000 {
            sink.accept(&tool).unwrap();
        }
        sink.accept(&event(
            json!({"type":"assistant", "message":{"model":"claude-opus-5-5",
            "content":[{"type":"tool_use", "name":"Workflow", "id":"w1",
            "input":{"script":"phase('Audit')"}}]}}),
        ))
        .unwrap();
        sink.accept(&event(json!({"type":"system", "subtype":"task_started",
            "tool_use_id":"w1", "task_id":"t1"})))
            .unwrap();
        sink.accept(&event(json!({"type":"system", "subtype":"task_progress",
            "tool_use_id":"w1", "task_id":"t1", "workflow_progress":[
                {"type":"workflow_agent", "agentId":"a1", "label":"audit",
                 "model":"claude-opus-5-5", "state":"done", "lastToolName":"Write"}]})))
            .unwrap();
        sink.accept(&event(
            json!({"type":"system", "subtype":"task_notification",
            "tool_use_id":"w1", "task_id":"t1", "status":"completed", "output_file":"audit.txt"}),
        ))
        .unwrap();
        sink.accept(&event(
            json!({"type":"result", "subtype":"success", "is_error":false}),
        ))
        .unwrap();
        let summary = sink.finish().unwrap();
        assert!(summary.complete);
        assert!(!summary.truncated);
        assert!(summary.retained_bytes > HARD_BYTE_LIMIT);
        let trace = fs::read(dir.path().join(TRACE_FILE)).unwrap();
        assert_eq!(trace.len(), summary.retained_bytes);
        assert!(String::from_utf8_lossy(&trace).contains("\"Workflow\""));
        assert!(String::from_utf8_lossy(&trace).contains("\"result\""));
    }
}
