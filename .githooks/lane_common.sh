#!/bin/bash
# Shared helpers for Heleos-spark git-layer lane hooks.
# These hooks only act when git is driven by Claude Code (CLAUDECODE or
# CLAUDE_CODE_REMOTE is set). Humans are never blocked by them.

lane_hooks_active() {
  [ -n "${CLAUDECODE:-}" ] || [ "${CLAUDE_CODE_REMOTE:-}" = "true" ]
}

lane_root() {
  git rev-parse --show-toplevel 2>/dev/null
}

lane_branch_name() {
  local root
  root="$(lane_root)" || return 1
  grep -Ev '^\s*(#|$)' "$root/.claude/work-branch" 2>/dev/null | head -n 1 | tr -d '[:space:]'
}

lane_current_branch() {
  git symbolic-ref --quiet --short HEAD 2>/dev/null || echo "(detached HEAD)"
}
