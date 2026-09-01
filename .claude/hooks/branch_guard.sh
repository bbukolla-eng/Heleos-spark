#!/bin/bash
# Heleos-spark branch guard launcher (Claude Code PreToolUse hook).
# Finds a Python 3 interpreter and runs branch_guard.py with the hook's stdin.
# Exit 2 denies the tool call; exit 0 allows it.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
for candidate in python3 python py; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if [ "$candidate" = "py" ]; then
      exec py -3 "$HERE/branch_guard.py"
    fi
    exec "$candidate" "$HERE/branch_guard.py"
  fi
done
echo "[BranchGuard] no Python 3 interpreter found; denying to stay in lane. Install python3." >&2
exit 2
