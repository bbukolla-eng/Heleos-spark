"""Verify one contained runner result before importing its candidate bytes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path


class HandoffError(ValueError):
    pass


class NativeChildDefects(HandoffError):
    """A candidate-bound child report explicitly records defects; it cannot be imported."""

    code = "native_child_defects_found"

    def __init__(self, count):
        self.count = count
        super().__init__(f"{self.code}: child report records {count} finding"
                         f"{'s' if count != 1 else ''}; inspect retained artifact")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def regular(path, limit=1_048_576):
    path = Path(path)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise HandoffError("symlink in runner evidence path")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
                raise HandoffError("runner evidence missing or oversized")
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            if (len(data) > limit or before.st_ino != after.st_ino
                    or before.st_dev != after.st_dev or before.st_size != after.st_size
                    or len(data) != before.st_size):
                raise HandoffError("runner evidence changed or oversized")
            return data
    except OSError as exc:
        raise HandoffError("runner evidence missing or unsafe") from exc


def inside(path, parent):
    path, parent = Path(path), Path(parent)
    return path.is_absolute() and path.resolve() == path and path.is_relative_to(parent)


def trace_events(trace, trace_meta=None):
    """Yield validated JSONL records without loading a typed trace into memory."""
    if trace_meta is None:
        try:
            for line in trace.splitlines():
                if line.strip():
                    yield json.loads(line)
        except ValueError as exc:
            raise HandoffError("required native Workflow evidence: invalid retained event stream") from exc
        return
    path = Path(trace)
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise HandoffError("symlink in typed trace path")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_size != trace_meta["retained_bytes"]):
                raise HandoffError("typed stream trace bytes differ")
            hashed, count, size = hashlib.sha256(), 0, 0
            while True:
                line = stream.readline(1_048_577)
                if not line:
                    break
                if len(line) > 1_048_576 or not line.endswith(b"\n"):
                    raise HandoffError("typed stream trace record is incomplete or oversized")
                hashed.update(line)
                size += len(line)
                count += 1
                try:
                    event = json.loads(line)
                except ValueError as exc:
                    raise HandoffError("typed stream trace JSON differs") from exc
                if not isinstance(event, dict) or event.get("type") not in ("assistant", "system", "result"):
                    raise HandoffError("typed stream trace projection differs")
                if event["type"] == "assistant":
                    message = event.get("message")
                    content = message.get("content") if isinstance(message, dict) else None
                    if (not isinstance(content, list) or not content
                            or any(not isinstance(item, dict) or item.get("type") != "tool_use"
                                   or not isinstance(item.get("name"), str)
                                   or not isinstance(item.get("id"), str)
                                   or (item.get("name") != "Workflow" and "input" in item)
                                   or (item.get("name") == "Workflow" and
                                       (not isinstance(item.get("input"), dict)
                                        or set(item["input"]) != {"script"}
                                        or not isinstance(item["input"]["script"], str)))
                                   for item in content)):
                        raise HandoffError("typed stream trace projection differs")
                if (event["type"] == "system" and event.get("subtype") not in
                        ("task_started", "task_progress", "task_notification")):
                    raise HandoffError("typed stream trace projection differs")
                yield event
            after = os.fstat(stream.fileno())
            if (before.st_dev != after.st_dev or before.st_ino != after.st_ino
                    or before.st_size != after.st_size or size != before.st_size
                    or count != trace_meta["events_retained"]
                    or hashed.hexdigest() != trace_meta["sha256"]):
                raise HandoffError("typed stream trace bytes differ")
    except OSError as exc:
        raise HandoffError("typed stream trace missing or unreadable") from exc


def native_workflow_evidence(trace, report_bytes, candidate, required, trace_meta=None):
    """Require one completed native Workflow child tied to exact candidate bytes.

    This checks retained CLI events and the named child file. Configured xhigh
    effort is checked in the actual Workflow script; hidden child effort is not
    claimed independently observable.
    """
    problem = "required native Workflow evidence missing or incomplete"
    base_fields = {"child_label", "child_report_path", "model", "effort", "script_sha256"}
    receipt_mode = isinstance(required, dict) and required.get("report_binding") == "controller_receipt"
    if (not isinstance(required, dict) or set(required) !=
            (base_fields | ({"report_binding"} if receipt_mode else set()))
            or any(not isinstance(v, str) or not v for v in required.values())
            or len(required["script_sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in required["script_sha256"])
            or required["child_report_path"] not in candidate):
        raise HandoffError(problem)
    other = {path: sha(raw) for path, raw in candidate.items()
             if path != required["child_report_path"]}
    try:
        report = json.loads(report_bytes)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HandoffError(problem) from exc
    expected_report = ({"child_label": required["child_label"], "decision": "reviewed",
                        "reviewed_paths": sorted(other), "findings": [],
                        "configured_model": required["model"],
                        "configured_effort": required["effort"]} if receipt_mode else
                       {"child_label": required["child_label"], "decision": "accepted",
                        "candidate_sha256": other, "configured_model": required["model"],
                        "configured_effort": required["effort"]})
    bound_defects = (receipt_mode and isinstance(report, dict) and report.get("decision") == "defects_found"
            and report.get("child_label") == required["child_label"]
            and report.get("reviewed_paths") == sorted(other)
            and report.get("configured_model") == required["model"]
            and report.get("configured_effort") == required["effort"]
            and isinstance(report.get("findings"), list) and report["findings"]
            and all(isinstance(finding, dict)
                    and isinstance(finding.get("id"), str) and finding["id"]
                    and isinstance(finding.get("summary"), str) and finding["summary"]
                    for finding in report["findings"]))
    if not bound_defects and (not isinstance(report, dict) or report != expected_report):
        raise HandoffError(problem + ": child report does not bind candidate")
    calls, tool, tool_id, starts, task_id = 0, None, None, 0, None
    agent_id, agent_bad, agent_start, agent_done, agent_wrote = None, False, False, False, False
    terminal_count, terminal_index, final, event_count = 0, -1, None, 0
    phase, order_bad = 0, False
    pre_call_task_ids = set()
    for event_count, event in enumerate(trace_events(trace, trace_meta), start=1):
        final = event
        if not isinstance(event, dict):
            raise HandoffError(problem + ": invalid retained event stream")
        if event.get("type") == "assistant":
            message = event.get("message")
            if isinstance(message, dict):
                for item in message.get("content", []):
                    if (isinstance(item, dict) and item.get("type") == "tool_use"
                            and item.get("name") == "Workflow"):
                        calls += 1
                        if calls == 1:
                            tool, tool_id = item, item.get("id")
                            order_bad |= tool_id in pre_call_task_ids
                            phase = 1
                        else:
                            order_bad = True
        elif event.get("type") == "system" and tool_id is None:
            if (event.get("subtype") in ("task_started", "task_progress", "task_notification")
                    and isinstance(event.get("tool_use_id"), str)):
                pre_call_task_ids.add(event["tool_use_id"])
        elif event.get("type") == "system" and event.get("tool_use_id") == tool_id and tool_id:
            subtype = event.get("subtype")
            if subtype == "task_started":
                if not isinstance(event.get("task_id"), str) or not event["task_id"]:
                    order_bad = True
                else:
                    starts += 1
                    if starts == 1:
                        task_id = event["task_id"]
                    if phase != 1:
                        order_bad = True
                    phase = 2
            elif subtype == "task_progress":
                if not task_id or event.get("task_id") != task_id:
                    order_bad = True
                    continue
                for item in event.get("workflow_progress", []):
                    if not isinstance(item, dict) or item.get("type") != "workflow_agent":
                        continue
                    current_id = item.get("agentId")
                    if not isinstance(current_id, str) or not current_id or (
                            agent_id is not None and current_id != agent_id):
                        agent_bad = True
                    elif agent_id is None:
                        agent_id = current_id
                    agent_bad |= (item.get("label") != required["child_label"]
                                  or item.get("model") != required["model"])
                    state = item.get("state")
                    if state == "start":
                        if phase != 2 or agent_start:
                            order_bad = True
                        phase = 3
                        agent_start = True
                    elif state == "done":
                        if phase != 3 or agent_done:
                            order_bad = True
                        phase = 4
                        agent_done = True
                        agent_wrote |= item.get("lastToolName") == "Write"
                    elif state == "progress":
                        if phase != 3 or not agent_start or agent_done:
                            order_bad = True
                    else:
                        order_bad = True
            elif subtype == "task_notification":
                if (not task_id or event.get("task_id") != task_id
                        or event.get("status") != "completed"
                        or not isinstance(event.get("output_file"), str)
                        or not event["output_file"] or phase != 4):
                    order_bad = True
                else:
                    phase = 5
                    terminal_count += 1
                    terminal_index = event_count
    if calls != 1 or tool is None:
        raise HandoffError(problem + ": exactly one Workflow call required")
    tool_id = tool.get("id")
    script = tool.get("input", {}).get("script") if isinstance(tool.get("input"), dict) else None
    if not isinstance(script, str) or sha(script.encode()) != required["script_sha256"]:
        raise HandoffError(problem + ": Workflow script differs from reviewed bytes")
    if (not isinstance(tool_id, str) or not tool_id
            or not re.search(r"label\s*:\s*['\"]" + re.escape(required["child_label"]) + r"['\"]", script)
            or not re.search(r"effort\s*:\s*['\"]" + re.escape(required["effort"]) + r"['\"]", script)):
        raise HandoffError(problem + ": child label or configured effort differs")
    if starts != 1 or order_bad or phase != 5:
        raise HandoffError(problem + ": Workflow start absent")
    if agent_bad or agent_id is None or not agent_start or not agent_done:
        raise HandoffError(problem + ": named child did not finish under the required model")
    if receipt_mode and not agent_wrote:
        raise HandoffError(problem + ": child report write not observed")
    if (terminal_count != 1 or terminal_index >= event_count
            or not isinstance(final, dict) or final.get("type") != "result"
            or final.get("is_error") is not False or final.get("subtype") != "success"
            or not isinstance(final.get("modelUsage"), dict)
            or set(final["modelUsage"]) != {required["model"]}):
        raise HandoffError(problem + ": Workflow terminal result absent")
    if bound_defects:
        raise NativeChildDefects(len(report["findings"]))
    return {"trace_sha256": trace_meta["sha256"] if trace_meta is not None else sha(trace),
            "child_report_sha256": sha(report_bytes),
            "candidate_sha256": other, "child_label": required["child_label"],
            "model": required["model"], "effort_configured": required["effort"],
            "script_sha256": required["script_sha256"],
            "report_binding": "controller_receipt" if receipt_mode else "child_hashes",
            "tool_use_id": tool_id, "task_id": task_id, "agent_id": agent_id}


def import_candidate(record, *, task_bytes, workspace_root, destination, expected_paths,
                     expected_selected_source_sha256=None):
    """Return retained evidence identities; refuse any untrusted path or byte drift.

    The guard is terminal before this call. A partial import can be retried with
    the same terminal guard ID: equal destination bytes are reused, mismatches
    stop without replacing them.
    """
    if record.get("state") != "succeeded" or record.get("outcome") != "succeeded":
        raise HandoffError("guard did not succeed")
    output_path = Path(record.get("stdout_path", ""))
    if not output_path.is_absolute():
        raise HandoffError("missing guard output path")
    try:
        summary = json.loads(regular(output_path))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HandoffError("invalid runner summary") from exc
    if not isinstance(summary, dict) or summary.get("schema") != "heleos.worker-run/v1":
        raise HandoffError("invalid runner summary schema")
    workspace_root = Path(workspace_root)
    run_dir = Path(summary.get("run_directory", ""))
    checkout = Path(summary.get("checkout_path", ""))
    if not inside(run_dir, workspace_root) or checkout != run_dir / "checkout" or not inside(checkout, run_dir):
        raise HandoffError("runner checkout escaped pinned workspace")
    if expected_selected_source_sha256 is not None:
        view = Path(summary.get("provider_view", ""))
        if (view != run_dir / "selected-view" or not inside(view, run_dir)
                or not view.is_dir() or (view / ".git").exists()):
            raise HandoffError("selected provider view differs")
        if summary.get("selected_source_sha256") != expected_selected_source_sha256:
            raise HandoffError("selected source identities differ")
    run_bytes = regular(run_dir / "run.json")
    if json.loads(run_bytes) != summary:
        raise HandoffError("guard output and retained runner result differ")
    handoff_bytes = regular(run_dir / "handoff.json")
    handoff = json.loads(handoff_bytes)
    if handoff != summary.get("handoff"):
        raise HandoffError("runner handoff differs")
    canonical_task = json.dumps(json.loads(task_bytes), sort_keys=True,
                                separators=(",", ":"), ensure_ascii=False).encode()
    task_digest = sha(canonical_task)
    if sha(regular(run_dir / "task.original.json")) != sha(task_bytes):
        raise HandoffError("runner task bytes differ")
    if regular(run_dir / "task.sha256").decode() != task_digest:
        raise HandoffError("runner task digest differs")
    if (handoff.get("task_digest") != task_digest or handoff.get("provider") != "claude_code"
            or handoff.get("terminal_state") != "blocked" or handoff.get("candidate_commit") is not None):
        raise HandoffError("runner handoff identity differs")
    scope = summary.get("containment", {})
    trace_meta = summary.get("stream_trace")
    data_class = json.loads(task_bytes).get("input_data_class")
    approval = sha(task_bytes) if data_class == "INTERNAL" else None
    if (data_class not in ("PUBLIC", "INTERNAL")
            or scope.get("mode") != "macos_seatbelt" or scope.get("host_path_writes_restricted") is not True
            or summary.get("provider_exit_code") != 0 or summary.get("provider_invocations") != 1
            or summary.get("approved_internal_task_sha256") != approval
            or (summary.get("stdout", {}).get("truncated") and trace_meta is None)
            or summary.get("stderr", {}).get("truncated")):
        raise HandoffError("runner containment, egress or output evidence differs")
    changed = summary.get("changed_files")
    if (not isinstance(changed, list) or len(changed) != len(expected_paths)
            or {x.get("path") for x in changed if isinstance(x, dict)} != set(expected_paths)
            or set(handoff.get("changed_paths", [])) != set(expected_paths)):
        raise HandoffError("runner candidate inventory differs from admitted paths")
    head = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    if head.returncode or head.stdout.strip() != json.loads(task_bytes)["base_commit"]:
        raise HandoffError("runner candidate base differs")
    candidate = {}
    for item in changed:
        path = item["path"]
        if not isinstance(path, str) or path.startswith("/") or any(
                x in ("", ".", "..") for x in path.split("/")):
            raise HandoffError("unsafe runner candidate path")
        raw = regular(checkout / path, limit=4_194_304)
        if item.get("sha256") != sha(raw) or item.get("executable") is not False:
            raise HandoffError("runner candidate bytes or mode differ")
        candidate[path] = raw
    workflow = None
    if record.get("required_workflow") is not None:
        required = record["required_workflow"]
        child_path = required.get("child_report_path") if isinstance(required, dict) else None
        if child_path not in candidate:
            raise HandoffError("required native Workflow child report absent")
        if trace_meta is None:
            trace = regular(run_dir / "stdout.bin")
        else:
            fields = {"schema", "path", "sha256", "retained_bytes", "raw_sha256", "raw_bytes",
                      "events_seen", "events_retained", "thinking_events_omitted",
                      "malformed", "truncated", "complete"}
            if (not isinstance(trace_meta, dict) or set(trace_meta) != fields
                    or trace_meta["schema"] != "heleos.claude-stream-trace/v1"
                    or trace_meta["path"] != "event-trace.jsonl"
                    or trace_meta["malformed"] is not False
                    or trace_meta["truncated"] is not False
                    or trace_meta["complete"] is not True
                    or summary.get("raw_stdout_omitted") is not True
                    or summary.get("stdout") != {"retained_bytes": 0, "truncated": True}
                    or regular(run_dir / "stdout.bin") != b""
                    or type(trace_meta["retained_bytes"]) is not int
                    or trace_meta["retained_bytes"] <= 0
                    or not isinstance(trace_meta["sha256"], str)
                    or len(trace_meta["sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in trace_meta["sha256"])
                    or type(trace_meta["raw_bytes"]) is not int
                    or trace_meta["raw_bytes"] <= 0
                    or type(trace_meta["events_seen"]) is not int
                    or type(trace_meta["events_retained"]) is not int
                    or trace_meta["events_retained"] <= 0
                    or type(trace_meta["thinking_events_omitted"]) is not int
                    or trace_meta["events_seen"] < trace_meta["events_retained"]
                    or not isinstance(trace_meta["raw_sha256"], str)
                    or len(trace_meta["raw_sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in trace_meta["raw_sha256"])):
                raise HandoffError("typed stream trace is incomplete")
            trace = run_dir / "event-trace.jsonl"
            try:
                progress = json.loads(regular(run_dir / "trace-progress.json"))
            except ValueError as exc:
                raise HandoffError("typed stream trace JSON differs") from exc
            if (not isinstance(progress, dict)
                    or progress.get("complete") is not True
                    or any(progress.get(key) != trace_meta[key] for key in
                           ("raw_bytes", "events_seen", "events_retained",
                            "thinking_events_omitted", "malformed", "truncated"))):
                raise HandoffError("typed stream trace projection differs")
        workflow = native_workflow_evidence(trace, candidate[child_path], candidate, required,
                                            trace_meta=trace_meta)
    destination = Path(destination)
    for path, raw in candidate.items():
        target = destination
        for part in path.split("/"):
            target /= part
            if target.is_symlink():
                raise HandoffError("destination symlink")
        if target.exists() and regular(target, limit=4_194_304) != raw:
            raise HandoffError("destination candidate differs")
    for path, raw in candidate.items():
        target = destination / path
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, scratch = tempfile.mkstemp(prefix=".loop-candidate-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(scratch, target)
            parent_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        finally:
            if os.path.exists(scratch):
                os.unlink(scratch)
    result = {"run_sha256": sha(run_bytes), "handoff_sha256": sha(handoff_bytes),
            "task_sha256": sha(task_bytes),
            "candidate_sha256": {path: sha(raw) for path, raw in candidate.items()}}
    if workflow is not None:
        result["native_workflow"] = workflow
    return result
