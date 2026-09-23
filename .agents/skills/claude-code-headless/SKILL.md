---
name: claude-code-headless
description: Use when Codex should delegate a bounded Heleos review, research, or implementation task to the installed Claude Code CLI, including NotebookLM-backed technical or mechanical research.
---

# Claude Code Headless Delegation

Use this skill when a scoped task benefits from an independent Claude review or
implementation candidate. Codex remains the coordinator, NotebookLM researcher,
source verifier, acceptance runner, and integrator.

## Do not dispatch when

- The task depends on uncommitted bytes that are absent from the exact base.
- Allowed paths, data class, egress basis, limits, or acceptance commands are
  missing or conflict with current owner decisions.
- The prompt would expose secrets, private bid data, or unapproved confidential
  material.
- The same task identity is already running or has a terminal result.
- Claude authentication or the required host containment is unavailable.

Safe read-only preparation may continue while one of these conditions is being
resolved.

## Preflight

1. Run `python3 scripts/active-build-status.py --human` when available. Record
   the exact checkout, branch, HEAD, and dirty paths. A failed authority check is
   a routing problem; do not guess another base or include working-tree dirt.
2. Verify `claude --version` and `claude --help` without opening authentication
   files. Record the CLI version and the selected model or configured default.
3. Read `AGENTS.md`, `CLAUDE.md`,
   `docs/operations/guarded-worker-runner.md`, and this task's plan or issue.
4. Select one role: read-only review/research or bounded implementation. Name one
   writer for every allowed path.
5. Classify all provider input and record the applicable egress decision. Public
   repository code is not automatically public data.

## NotebookLM research gate

For technical or mechanical knowledge dependencies:

1. Reuse the recorded notebook inventory, verified findings, and implementation
   records before making a new query. The usual project location is
   `docs/research/notebooklm/`; treat a missing inventory as outstanding rather
   than as an empty inventory.
2. Select the relevant notebook by recorded topic and source identities. Use the
   connected Gemini Notebook MCP from Codex, not from Claude.
3. Ask a bounded question for requirements, exceptions, conflicts, and examples.
4. Open the supporting source bodies with `source_get_content`. Verify each
   consequential claim against the text and retain notebook ID, source ID, URL
   or document locator, edition/page/section when available, and verification
   result.
5. Record the provider submission before execution with provider, purpose, data
   class, approved source identities, policy decision, time, and result reference.
6. Write a source-backed research record under `docs/research/notebooklm/`.
   Separate adopted findings, conflicts, unsupported claims, and limitations.
7. Commit the research record before the Claude task and include its exact path
   and SHA-256 in the task's `instruction_sha256` map.

For knowledge-independent work, record NotebookLM as not applicable. When the
MCP is unavailable, record the capability gap and continue only with existing
verified sources or independent work that does not require the missing claim.

NotebookLM answers, Claude output, and research notes never override approved
rules, source evidence, or deterministic calculations.

## Build the assignment

Create a strict `heleos.worker-task/v1` JSON file. It must contain:

- a new task ID and exact 40-character base commit;
- `provider: "claude_code"`;
- `mode: "research"` for read-only analysis or `"implementation"` for edits;
- one concrete objective;
- sorted exact allowed and forbidden paths;
- the data class and egress policy;
- SHA-256 identities for every instruction and NotebookLM research record;
- ordered acceptance commands that Codex can run independently;
- positive action and duration limits.

Instruction files must exist at the exact base commit. Never point the task at
an untracked or working-tree-only research record.

## Dispatch

Use the repository-owned `heleos-worker-runner`; do not invoke a write-capable
Claude process directly in the source checkout. Start from the commands in
`docs/operations/claude-code-headless.md`.

Common Claude controls are:

```text
-p --output-format json --no-session-persistence --safe-mode --restricted
--strict-mcp-config --no-chrome --permission-prompts none
```

Review mode uses `--permission-mode dontAsk --tools Read,Glob,Grep`.
Implementation mode uses `--permission-mode acceptEdits` and only the tools the
task needs, normally `Read,Glob,Grep,Edit,Write,Bash`.

Never use `--dangerously-skip-permissions`, `--allow-dangerously-skip-permissions`,
`--bg`, `--continue`, or `--resume`. Do not give Claude NotebookLM, browser,
GitHub, deployment, messaging, billing, or account-management tools.

On macOS, select `--containment macos_seatbelt`. On Windows, select
`--containment windows_restricted_token_job`. Unsupported or failed containment
stops the dispatch without an uncontained fallback. Inherit only the identity
and home values required by the owner-authorized Claude session, normally
`HOME`, `USER`, `LOGNAME`, and `SHELL` on macOS.

## Verify and integrate

1. Wait for the existing process to reach a terminal result. Quiet output is not
   termination and never justifies a duplicate launch.
2. Inspect the retained `run.json`, `handoff.json`, stdout, stderr, prompt digest,
   provider exit, timeout/truncation flags, containment mode, and complete changed
   path inventory.
3. Reject any forbidden or unlisted change, instruction mismatch, Git metadata
   violation, unresolved mandatory contract defect, or unsupported claim.
   An expected UNKNOWN can pass its named criterion; unrelated unfinished work
   and optional suggestions do not reject the candidate. A required supported
   quantity cannot be replaced by UNKNOWN. Cite criterion IDs or the violated
   shared policy in each blocking finding.
4. Run every declared acceptance command independently on the retained candidate
   and record exact exits and output hashes. Claude's test claims are not proof.
5. Review the diff against the task and verified NotebookLM findings. Adopt only
   applicable source-backed behavior; keep approved deterministic rules
   authoritative.
6. Apply accepted bytes through the repository's authorized integration path.
   Claude never commits, pushes, merges, approves, or promotes its own work.
7. Record the terminal result, candidate identities, checks, limits, research
   references, remaining findings, and one next action. Update `CURRENT_STATUS.md`
   when a scoped build task is completed, following its single-writer rule.
