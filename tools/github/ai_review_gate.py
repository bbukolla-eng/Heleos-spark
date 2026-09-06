"""Decide whether Codex and Copilot have acknowledged a pull request head.

ECC Tools is detected and printed as advisory; it never fails the job. Used by
.github/workflows/ai-review-gate.yml. Standard library only. Talks to GitHub only
through JSON the workflow already fetched with gh; it never opens a network
connection and never reads a secret.
"""
from __future__ import annotations

import argparse
import json
import sys

CODEX_BOT = "chatgpt-codex-connector[bot]"
ECC_BOT = "ecc-tools[bot]"
COPILOT_BOT = "copilot-pull-request-reviewer[bot]"
AMAZON_Q_BOT = "amazon-q-developer[bot]"
AQ_PING = "@amazon-q review"
AMAZON_Q_CHECK = "Amazon Q Developer"
COPILOT_CHECK = "copilot-pull-request-reviewer"
CODEX_PING = "@codex review"

REVIEWER_KEYS = (("codex", CODEX_BOT), ("ecc", ECC_BOT), ("copilot", COPILOT_BOT), ("amazon_q", AMAZON_Q_BOT))
REQUIRED = ("codex", "copilot")


def login_matches(login, expected):
    left = (login or "").strip().lower()
    right = (expected or "").strip().lower()
    if not left or not right:
        return False
    if left == right:
        return True
    return left + "[bot]" == right or right + "[bot]" == left


def _login(item):
    user = item.get("user") or item.get("actor") or {}
    if isinstance(user, dict):
        return user.get("login") or ""
    return ""


def _created_at(item):
    return item.get("created_at") or item.get("submitted_at") or ""


def _commit_ids(item):
    ids = []
    for key in ("commit_id", "original_commit_id", "head_sha", "sha"):
        value = item.get(key)
        if value:
            ids.append(value)
    after = item.get("after")
    if after:
        ids.append(after)
    commit = item.get("commit")
    if isinstance(commit, dict) and commit.get("sha"):
        ids.append(commit["sha"])
    return ids


def is_for_head(item, head_sha, head_since):
    if head_sha and head_sha in _commit_ids(item):
        return True
    created = _created_at(item)
    return bool(head_since and created and created >= head_since)


def _as_items(payload):
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("check_runs", "reviews", "comments", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def bot_acknowledged(items, bot, head_sha, head_since):
    for item in _as_items(items):
        if item.get("state") == "PENDING":
            continue
        if login_matches(_login(item), bot) and is_for_head(item, head_sha, head_since):
            return True
    return False


def copilot_check_success(check_runs, head_sha):
    for run in _as_items(check_runs):
        if run.get("name") != COPILOT_CHECK:
            continue
        if run.get("conclusion") != "success":
            continue
        run_sha = run.get("head_sha")
        if not head_sha or not run_sha or run_sha == head_sha:
            return True
    return False


def evaluate(
    *,
    reviews,
    issue_comments,
    review_comments,
    check_runs,
    head_sha,
    head_since,
):
    spoken = list(_as_items(reviews)) + list(_as_items(issue_comments)) + list(
        _as_items(review_comments)
    )
    flags = {}
    for name, bot in REVIEWER_KEYS:
        flags[name] = bot_acknowledged(spoken, bot, head_sha, head_since)
    flags["copilot"] = flags["copilot"] or copilot_check_success(check_runs, head_sha)
    # Amazon Q check-run success counts as acknowledgement even without a review comment.
    for run in _as_items(check_runs):
        name = (run.get("name") or "").strip()
        if name == AMAZON_Q_CHECK and (run.get("conclusion") or "").lower() == "success":
            flags["amazon_q"] = True
            break
    missing = [name for name in REQUIRED if not flags[name]]
    flags["missing"] = missing
    flags["all_ok"] = not missing
    return flags


def should_ping_codex(
    *,
    reviews,
    issue_comments,
    review_comments,
    head_sha,
    head_since,
):
    spoken = list(_as_items(reviews)) + list(_as_items(issue_comments)) + list(
        _as_items(review_comments)
    )
    if bot_acknowledged(spoken, CODEX_BOT, head_sha, head_since):
        return False
    for item in _as_items(issue_comments):
        body = item.get("body") or ""
        if CODEX_PING in body and is_for_head(item, head_sha, head_since):
            return False
    return True


def should_ping_amazon_q(
    *,
    reviews,
    issue_comments,
    review_comments,
    check_runs,
    head_sha,
    head_since,
):
    """Ping once per head when Amazon Q has not acknowledged and its check is not success."""
    spoken = list(_as_items(reviews)) + list(_as_items(issue_comments)) + list(
        _as_items(review_comments)
    )
    if bot_acknowledged(spoken, AMAZON_Q_BOT, head_sha, head_since):
        return False
    for run in _as_items(check_runs):
        name = (run.get("name") or "").strip()
        status = (run.get("status") or "").lower()
        conclusion = (run.get("conclusion") or "").lower()
        if name != AMAZON_Q_CHECK:
            continue
        if conclusion == "success":
            return False
        if status == "in_progress":
            return False
    for item in _as_items(issue_comments):
        body = item.get("body") or ""
        if AQ_PING in body and is_for_head(item, head_sha, head_since):
            return False
    return True


def resolve_head_since(timeline, commit, head_sha, fallback=""):
    matches = []
    for event in _as_items(timeline):
        kind = event.get("event")
        if kind not in ("committed", "head_ref_force_pushed"):
            continue
        event_shas = _commit_ids(event)
        if head_sha and head_sha in event_shas:
            created = event.get("created_at")
            if created:
                matches.append(created)
    if matches:
        return max(matches)
    if isinstance(commit, dict):
        committer = (commit.get("commit") or {}).get("committer") or {}
        date = committer.get("date")
        if date:
            return date
    return fallback


def _load_json(path):
    if not path:
        return []
    with open(path, encoding="utf-8") as handle:
        text = handle.read().strip()
    if not text:
        return []
    return json.loads(text)


def _print_result(result):
    print(json.dumps(result, sort_keys=True))
    missing = result.get("missing") or []
    if missing:
        print("missing: " + ", ".join(missing))
    else:
        print("Codex OK, Copilot OK")
    print("ECC advisory: present" if result.get("ecc") else "ECC advisory: absent")
    print("Amazon Q advisory: present" if result.get("amazon_q") else "Amazon Q advisory: absent")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    evaluate_cmd = sub.add_parser("evaluate")
    ping_cmd = sub.add_parser("should-ping-codex")
    aq_ping_cmd = sub.add_parser("should-ping-amazon-q")
    since_cmd = sub.add_parser("head-since")
    for cmd in (evaluate_cmd, ping_cmd, aq_ping_cmd):
        cmd.add_argument("--head-sha", required=True)
        cmd.add_argument("--head-since", required=True)
        cmd.add_argument("--reviews", required=True)
        cmd.add_argument("--issue-comments", required=True)
        cmd.add_argument("--review-comments", required=True)
    evaluate_cmd.add_argument("--check-runs", required=True)
    aq_ping_cmd.add_argument("--check-runs", required=True)
    since_cmd.add_argument("--head-sha", required=True)
    since_cmd.add_argument("--timeline", required=True)
    since_cmd.add_argument("--commit", required=True)

    args = parser.parse_args(argv)

    if args.command == "head-since":
        since = resolve_head_since(
            _load_json(args.timeline), _load_json(args.commit), args.head_sha
        )
        print(since)
        return 0

    common = {
        "reviews": _load_json(args.reviews),
        "issue_comments": _load_json(args.issue_comments),
        "review_comments": _load_json(args.review_comments),
        "head_sha": args.head_sha,
        "head_since": args.head_since,
    }

    if args.command == "should-ping-codex":
        ping = should_ping_codex(**common)
        print("ping" if ping else "skip")
        return 0 if ping else 2
    if args.command == "should-ping-amazon-q":
        aq_common = dict(common)
        aq_common["check_runs"] = _load_json(args.check_runs)
        ping = should_ping_amazon_q(**aq_common)
        print("ping" if ping else "skip")
        return 0 if ping else 2

    result = evaluate(check_runs=_load_json(args.check_runs), **common)
    _print_result(result)
    return 0 if result["all_ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
