# Claude Code Headless and NotebookLM Delegation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Codex a reusable project workflow for bounded headless Claude Code delegation with Codex-controlled NotebookLM research.

**Architecture:** Reuse the existing `heleos-worker-runner` and its validated task protocol. Add a Codex skill and an operations guide that bind NotebookLM source verification, Claude invocation, retained evidence, and independent acceptance into one workflow.

**Tech Stack:** Markdown project skill, Claude Code 2.1.261, Rust `heleos-worker-runner`, Gemini Notebook MCP.

## Global Constraints

- Preserve the current dirty checkout and commit only the new integration files.
- Use the existing worker runner; do not create a second launcher or task schema.
- Codex owns NotebookLM access, source verification, and acceptance.
- Claude never commits, pushes, merges, approves, deploys, or receives secrets.
- Use public or otherwise explicitly approved provider inputs only.

---

### Task 1: Record the approved design

**Files:**
- Create: `docs/superpowers/specs/2026-09-15-claude-code-headless-notebooklm-design.md`

**Interfaces:**
- Consumes: Existing worker-runner and NotebookLM policies.
- Produces: The authority for the skill and operations guide.

- [x] **Step 1: Describe the existing runner boundary and authority split.**
- [x] **Step 2: Define read-only and bounded-write modes.**
- [x] **Step 3: Define NotebookLM research, egress, and failure handling.**

### Task 2: Add the Codex delegation skill

**Files:**
- Create: `.agents/skills/claude-code-headless/SKILL.md`
- Create: `.agents/skills/claude-code-headless/agents/openai.yaml`

**Interfaces:**
- Consumes: `heleos.worker-task/v1`, `heleos-worker-runner`, NotebookLM MCP.
- Produces: An implicitly invokable Codex workflow for Claude delegation.

- [x] **Step 1: Specify triggers, non-triggers, and preflight checks.**
- [x] **Step 2: Specify the NotebookLM research packet workflow.**
- [x] **Step 3: Specify task construction, dispatch, verification, and handoff.**

### Task 3: Add the operator guide

**Files:**
- Create: `docs/operations/claude-code-headless.md`

**Interfaces:**
- Consumes: The approved design and existing runner CLI.
- Produces: Exact read-only and implementation command lines and required records.

- [x] **Step 1: Document task and instruction preparation.**
- [x] **Step 2: Document macOS and Windows launch forms.**
- [x] **Step 3: Document independent acceptance and failure recovery.**

### Task 4: Verify and commit

**Files:**
- Verify: All files created by Tasks 1 through 3.

**Interfaces:**
- Consumes: The complete integration diff.
- Produces: A focused conventional commit containing only this workflow.

- [x] **Step 1: Run `git diff --check` for the new files.**
- [x] **Step 2: Run the existing worker-protocol and worker-runner tests with the pinned locked offline toolchain.**
- [x] **Step 3: Inspect the staged file list and confirm no pre-existing dirty path is staged.**
- [x] **Step 4: Commit with `feat: add Claude headless delegation workflow`.**

## Verification record

- `cargo metadata --locked --offline --no-deps --format-version 1` exited 0.
- Static validation of the five new files, relative links, UTF-8/final-newline
  rules, skill metadata, and installed Claude CLI flags exited 0.
- The connected NotebookLM MCP returned the current notebook inventory with
  status `success`; no source, note, notebook, or setting was changed.
- `cargo test -p heleos-worker-protocol -p heleos-worker-runner --locked
  --offline` exited 101 before tests ran because this Mac has not accepted the
  Xcode license. Accepting that system-wide license is outside this task. The
  existing runner code was not changed.
