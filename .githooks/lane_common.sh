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

# Prints the validated lane name; prints nothing (and returns 1) when the
# file is missing or the name is not a plain branch name git accepts.
lane_branch_name() {
  local root name
  root="$(lane_root)" || return 1
  name="$(grep -Ev '^\s*(#|$)' "$root/.claude/work-branch" 2>/dev/null | head -n 1 | tr -d '[:space:]')"
  case "$name" in
    ""|-*) return 1 ;;
  esac
  git check-ref-format --branch "$name" >/dev/null 2>&1 || return 1
  printf '%s\n' "$name"
}

lane_current_branch() {
  git symbolic-ref --quiet --short HEAD 2>/dev/null || echo "(detached HEAD)"
}
