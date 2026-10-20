# Claude Code Headless and NotebookLM Delegation Design

**Status:** Approved by the owner on 2026-09-15

## Goal

Enable Codex to use the installed Claude Code CLI as a bounded headless worker
while keeping Codex responsible for task selection, NotebookLM research, source
verification, independent checks, integration, and acceptance.

## Existing foundation

The repository already contains `heleos-worker-runner`. It creates a disposable
exact-commit checkout, sends a validated `heleos.worker-task/v1` assignment to a
provider over standard input, bounds execution and retained output, inventories
all changed paths, and preserves a candidate without promoting it. It supports
Claude Code and has retained evidence for an authenticated public synthetic
write under macOS Seatbelt.

This integration reuses that runner. It does not add another process launcher,
filesystem sandbox, task schema, or acceptance authority.

## Control flow

1. Codex verifies the checkout, branch, base commit, dirty paths, installed
   Claude version, authenticated state, task scope, data class, and egress basis.
2. Codex determines whether the task depends on technical or mechanical
   knowledge.
3. For knowledge-dependent work, Codex reuses the recorded NotebookLM inventory
   and findings, performs a bounded query when needed, opens the supporting
   source text, verifies consequential passages, and records notebook IDs,
   source IDs, locators, conflicts, limitations, and the external submission.
4. The verified research record is committed at the worker task's exact base and
   listed by path and SHA-256 in `instruction_sha256`. Claude receives the record
   as an instruction file. Notebook answers alone are not authority.
5. Codex constructs a `heleos.worker-task/v1` assignment with exact allowed and
   forbidden paths, acceptance commands, duration and action limits.
6. Codex launches Claude through `heleos-worker-runner` in restricted, safe,
   nonpersistent print mode. Review tasks expose read tools. Implementation tasks
   expose only the tools needed for the assigned files and checks.
7. Codex reads the retained run evidence, verifies the complete changed-path
   inventory, runs the declared checks independently, and accepts or rejects the
   candidate. Claude never commits, pushes, merges, approves, deploys, or changes
   account settings.

## NotebookLM boundary

Codex owns NotebookLM access. Claude receives a bounded, source-backed research
record and does not receive ambient MCP servers. The Claude invocation uses safe
mode and strict MCP configuration so installed user or project MCP servers are
not silently exposed.

Relevant public sources and source-backed notes may be added under the standing
authorization in `AGENTS.md`. Private code, drawings, project documents, and
secrets are excluded unless the owner has approved an explicit provider and
project scope. NotebookLM failure leaves the missing research action outstanding
and does not block unrelated deterministic work.

## Modes

| Mode | Claude tools | Result |
| --- | --- | --- |
| Review or research | `Read,Glob,Grep` | Retained stdout and no accepted source mutation |
| Bounded implementation | `Read,Glob,Grep,Edit,Write,Bash` | An uncommitted candidate limited by the task allowlist and independently inventoried |

`Bash` is admitted only when the task needs declared local checks. The prompt,
runner containment, task allowlist, and post-run inventory remain separate
controls. No mode uses `--dangerously-skip-permissions` or Claude background
sessions.

## Failure handling

Authentication failure stops the Claude lane without reading credential
material. NotebookLM unavailability is recorded as a capability gap. Timeout,
provider failure, output truncation, instruction mismatch, repository metadata
mutation, or an out-of-scope path prevents acceptance and preserves the retained
run for inspection. A retry uses a new task identity and starts from the last
verified base and instructions.

## Completion evidence

The integration files are complete when the project skill contains the dispatch
and NotebookLM workflow, the operations guide gives exact commands and records,
and their static checks pass. The existing worker-runner suite remains the
runtime gate; a platform prerequisite that prevents it from running is recorded
and remains outstanding rather than being reported as a pass. A future live task
is task evidence, not a prerequisite for installing this coordination workflow
because the same Claude version and runner already have live authenticated
evidence.
