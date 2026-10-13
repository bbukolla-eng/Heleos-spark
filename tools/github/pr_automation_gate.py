"""Gate helper for automation-driven auto-merge label decisions.

Evaluates check-runs on a pull request head and reports whether the required
signals are green. Standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys

ALIASES = {
    "checks": ("checks",),
    "codex": ("ai-reviewers", "codex"),
    "copilot": ("copilot-pull-request-reviewer", "copilot"),
    "ecc": ("ECC Tools Review", "ecc", "ecc-tools"),
    "amazon-q": ("Amazon Q Developer", "amazon q", "amazon-q", "amazonq"),
    "ai-reviewers": ("ai-reviewers",),
}
DEFAULT_REQUIRED = ("checks", "codex", "copilot", "ecc", "amazon-q")


def _norm(text):
    return re.sub(r"[^a-z0-9]+", "", (text or "").strip().lower())


def _as_items(payload):
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        runs = payload.get("check_runs")
        if isinstance(runs, list):
            return runs
    return []


def _recency(run, index):
    """Rank a check run so a later rerun outranks a stale one.

    GitHub assigns a new integer id on each rerun. started_at covers a
    pending rerun that has no completed_at yet. List order is the last resort.
    """
    run_id = run.get("id")
    if type(run_id) is int:
        return (3, run_id)
    if isinstance(run_id, str) and run_id.isdigit():
        return (3, int(run_id))
    started = run.get("started_at")
    if isinstance(started, str) and started.strip():
        return (2, started.strip())
    completed = run.get("completed_at")
    if isinstance(completed, str) and completed.strip():
        return (1, completed.strip())
    return (0, index)


def _successful_names(check_runs):
    latest = {}
    for index, run in enumerate(_as_items(check_runs)):
        name = run.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        key = name.strip()
        rank = _recency(run, index)
        current = latest.get(key)
        if current is None or rank > current[0]:
            latest[key] = (rank, run)
    names = []
    for _rank, run in latest.values():
        if run.get("conclusion") == "success":
            name = run.get("name")
            names.append(name.strip())
    return names


def evaluate(check_runs, required):
    ok_names = _successful_names(check_runs)
    ok_norm = {_norm(name) for name in ok_names}
    missing = []
    for signal in required:
        probes = ALIASES.get(signal, (signal,))
        accepted = {_norm(probe) for probe in probes}
        accepted.discard("")
        if ok_norm.isdisjoint(accepted):
            missing.append(signal)
    return {
        "required": list(required),
        "missing": missing,
        "all_ok": not missing,
        "successful_checks": ok_names,
    }


def _load_json(path):
    with open(path, encoding="utf-8") as handle:
        text = handle.read().strip()
    return json.loads(text) if text else []


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("evaluate",))
    parser.add_argument("--check-runs", required=True)
    parser.add_argument(
        "--required",
        action="append",
        default=[],
        help="Required signal; can be repeated. Defaults to checks,codex,copilot,ecc,amazon-q",
    )
    args = parser.parse_args(argv)

    required = tuple(args.required) if args.required else DEFAULT_REQUIRED
    result = evaluate(_load_json(args.check_runs), required)
    print(json.dumps(result, sort_keys=True))
    if result["all_ok"]:
        print("all required signals are green")
        return 0
    print("missing: " + ", ".join(result["missing"]))
    return 2


if __name__ == "__main__":
    sys.exit(main())
