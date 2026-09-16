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
    "copilot": ("ai-reviewers", "copilot-pull-request-reviewer", "copilot"),
    "ecc": ("ecc", "ecc-tools"),
    "amazon-q": ("amazon q", "amazon-q", "amazonq"),
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


def _successful_names(check_runs):
    names = []
    for run in _as_items(check_runs):
        if run.get("conclusion") != "success":
            continue
        name = run.get("name")
        if isinstance(name, str) and name.strip():
            names.append(name)
    return names


def evaluate(check_runs, required):
    ok_names = _successful_names(check_runs)
    ok_norm = [_norm(name) for name in ok_names]
    missing = []
    for signal in required:
        probes = ALIASES.get(signal, (signal,))
        probe_norm = [_norm(p) for p in probes if _norm(p)]
        if not any(any(probe in name for probe in probe_norm) for name in ok_norm):
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
