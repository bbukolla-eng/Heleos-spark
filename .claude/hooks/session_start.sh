#!/bin/bash
# Heleos-spark SessionStart hook.
#
# 1. Reports the designated Claude Code lane branch and the current checkout.
# 2. In Claude Code on the web (CLAUDE_CODE_REMOTE=true) with a clean tree,
#    checks out the lane so every remote session starts inside it.
# 3. Points git at the versioned .githooks/ so commits and pushes made by
#    Claude Code are checked at the git layer as well.
# 4. Installs Python dependencies when a manifest exists (remote sessions).
#
# Never fails the session: every step degrades to a printed warning.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT" || exit 0

LANE="$(grep -Ev '^\s*(#|$)' "$ROOT/.claude/work-branch" 2>/dev/null | head -n 1 | tr -d '[:space:]')"
# The lane must be a branch name git accepts and must never look like an
# option (a leading dash could otherwise be read by git as a flag).
case "$LANE" in
  ""|-*) LANE="" ;;
esac
if [ -n "$LANE" ] && ! git check-ref-format --branch "$LANE" >/dev/null 2>&1; then
  LANE=""
fi
if [ -z "$LANE" ]; then
  echo "[LaneStatus] WARNING: .claude/work-branch is missing, empty, or not a valid branch name. Claude Code is read-only in this repository until the owner restores it." >&2
  exit 0
fi

current_branch() {
  git symbolic-ref --quiet --short HEAD 2>/dev/null || echo "(detached HEAD)"
}

BRANCH="$(current_branch)"

# Git-layer enforcement (pre-commit / pre-push only act for Claude Code processes).
if [ -d "$ROOT/.githooks" ]; then
  git config core.hooksPath .githooks 2>/dev/null || true
fi

if [ "$BRANCH" != "$LANE" ] && [ "${CLAUDE_CODE_REMOTE:-}" = "true" ] && [ "${HELEOS_LANE_AUTOSWITCH:-1}" != "0" ]; then
  if [ -z "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
    if git fetch --quiet origin "$LANE" 2>/dev/null; then
      if git show-ref --verify --quiet "refs/heads/$LANE"; then
        git checkout --quiet "$LANE" 2>/dev/null || true
      else
        git checkout --quiet -b "$LANE" --track "origin/$LANE" 2>/dev/null || true
      fi
    else
      # Lane does not exist on origin yet: start it from the current HEAD.
      git checkout --quiet -B "$LANE" 2>/dev/null || true
    fi
    BRANCH="$(current_branch)"
    echo "[LaneStatus] Remote session moved to the lane branch."
  else
    echo "[LaneStatus] Working tree is dirty; not switching branches automatically." >&2
  fi
fi

echo "[LaneStatus] Heleos-spark designated lane: $LANE"
echo "[LaneStatus] Current branch: $BRANCH"
if [ "$BRANCH" = "$LANE" ]; then
  echo "[LaneStatus] OK: edits, commits, and pushes are permitted (lane only)."
else
  echo "[LaneStatus] OFF-LANE: Claude Code is read-only here. Run: git checkout $LANE"
fi

DEFAULT_BRANCH="$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')"
DEFAULT_BRANCH="${DEFAULT_BRANCH:-main}"
if git rev-parse --verify --quiet "origin/$DEFAULT_BRANCH" >/dev/null 2>&1; then
  COUNTS="$(git rev-list --left-right --count "origin/$DEFAULT_BRANCH...HEAD" 2>/dev/null)"
  if [ -n "$COUNTS" ]; then
    BEHIND="${COUNTS%%[[:space:]]*}"
    AHEAD="${COUNTS##*[[:space:]]}"
    echo "[LaneStatus] Relative to origin/$DEFAULT_BRANCH: ahead $AHEAD, behind $BEHIND. Merge (never rebase) $DEFAULT_BRANCH into the lane to catch up."
  fi
fi

# Dependencies: only in remote sessions, only when a manifest exists.
if [ "${CLAUDE_CODE_REMOTE:-}" = "true" ]; then
  if [ -f "$ROOT/pyproject.toml" ] && command -v uv >/dev/null 2>&1; then
    echo "[LaneStatus] Installing dependencies with uv sync..."
    uv sync --all-extras >/dev/null 2>&1 || echo "[LaneStatus] WARNING: uv sync failed; run it manually." >&2
  elif [ -f "$ROOT/requirements.txt" ] && command -v pip3 >/dev/null 2>&1; then
    echo "[LaneStatus] Installing dependencies with pip..."
    pip3 install -q -r "$ROOT/requirements.txt" >/dev/null 2>&1 || echo "[LaneStatus] WARNING: pip install failed." >&2
  fi
fi
exit 0
