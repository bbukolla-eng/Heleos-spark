# Claude Code headless delegation

This workflow lets Codex use Claude Code for a bounded proposal while preserving
the authority and evidence boundaries in `AGENTS.md`. It reuses the implemented
`heleos-worker-runner`; the runner creates a disposable exact-base checkout and
never promotes the result.

Read the approved
[design](../superpowers/specs/2026-09-15-claude-code-headless-notebooklm-design.md),
the [worker-runner contract](guarded-worker-runner.md), and the project
`claude-code-headless` skill before dispatch.

## Prepare a task

Record these values before creating the JSON task:

| Field | Required evidence |
| --- | --- |
| Base | Exact committed SHA and source checkout |
| Role | Read-only review/research or bounded implementation |
| Scope | Sorted allowed paths, forbidden paths, and one writer per path |
| Provider | Installed Claude version, authenticated-state check, model choice |
| Data | Input classification and applicable egress decision |
| Research | Notebook/source IDs, verified passages, conflicts, and record hash, or `not_applicable` |
| Limits | Positive action and elapsed-time limits; API budget cap when applicable |
| Acceptance | Ordered commands that Codex will run independently |

For a knowledge-dependent task, follow the NotebookLM policy in `AGENTS.md` and
the research-record contract below. If the repository has a more detailed
NotebookLM operations guide, apply it as well. Commit the feature research record
at the chosen base and include it in `instruction_sha256`. The runner reads each
instruction from the exact commit, verifies its SHA-256, and includes its bytes
in the bounded prompt. This makes the source-backed packet available to Claude
without exposing the NotebookLM MCP.

Do not dispatch against the current working tree when the needed instructions or
code exist only as uncommitted changes. Commit an authorized base first or defer
the worker task.

## macOS read-only review

Use absolute paths. Replace the example values with the recorded task, source,
workspace root, Claude executable, and Git executable.

```bash
cargo run -p heleos-worker-runner --locked --offline -- \
  --task /absolute/path/to/task.json \
  --source /absolute/path/to/Heleos-spark \
  --workspace-root /absolute/path/to/worker-runs \
  --provider claude_code \
  --command /absolute/path/to/claude \
  --git /usr/bin/git \
  --containment macos_seatbelt \
  --inherit-env HOME \
  --inherit-env USER \
  --inherit-env LOGNAME \
  --inherit-env SHELL \
  -- -p --output-format json --no-session-persistence --safe-mode \
     --restricted --strict-mcp-config --no-chrome \
     --permission-mode dontAsk --permission-prompts none \
     --tools Read,Glob,Grep
```

The allowed paths still identify the material Claude may review. The exposed
tool set has no write or shell tool. Treat stdout as an untrusted review report.

## macOS bounded implementation

```bash
cargo run -p heleos-worker-runner --locked --offline -- \
  --task /absolute/path/to/task.json \
  --source /absolute/path/to/Heleos-spark \
  --workspace-root /absolute/path/to/worker-runs \
  --provider claude_code \
  --command /absolute/path/to/claude \
  --git /usr/bin/git \
  --containment macos_seatbelt \
  --inherit-env HOME \
  --inherit-env USER \
  --inherit-env LOGNAME \
  --inherit-env SHELL \
  -- -p --output-format json --no-session-persistence --safe-mode \
     --restricted --strict-mcp-config --no-chrome \
     --permission-mode acceptEdits --permission-prompts none \
     --tools Read,Glob,Grep,Edit,Write,Bash
```

Remove `Bash` when the task does not require local commands. When API billing is
in use, add an owner-authorized `--max-budget-usd` value and record it. The outer
runner enforces elapsed time, but the Claude CLI does not attest the task's
internal action count.

## Windows bounded implementation

Run the same task using absolute local Windows paths, a local Claude executable,
an absolute Git executable, and:

```text
--containment windows_restricted_token_job
```

Do not inherit a Unix home tuple on Windows. The worker-runner documentation and
native Windows evidence rules remain authoritative. Host-side cross-compilation
does not replace a native NTFS result.

## NotebookLM research record

A feature research record should contain:

- purpose, timestamp, provider, data class, and policy decision;
- notebook ID and topic;
- each selected source ID and stable source locator;
- the bounded query;
- cited answer segments;
- source-text passages with page/section when available;
- verification status for every consequential claim;
- conflicts, unsupported findings, and applicability limits;
- adopted behavior and the tests or checks it informed.

Reuse prior verified records when their source identities and requirements have
not changed. Do not refill completed notebooks or repeat queries merely because
a new Claude task begins.

## Acceptance

An exit of zero means that the runner generated and inventoried a candidate. It
does not accept the candidate. Codex must inspect the retained evidence, confirm
the containment mode and complete path inventory, run the task's acceptance
commands independently, review the diff, and record the decision.

Retain failures for inspection. Authentication failure, NotebookLM unavailability,
timeout, output truncation, instruction mismatch, repository mutation, or scope
violation must be recorded with one next action. Never silently widen scope,
disable checks, or relaunch the same task identity.
