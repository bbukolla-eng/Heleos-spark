"""Record one external submission made by a GitHub Actions run (spec section 5).

The seven fields of spec section 5 are the whole record: provider, purpose, data_class,
source_hashes, policy_decision, time, result_ref. No text taken from a pull request, an
issue, a comment, a model answer, or the process environment beyond ENV_ALLOWLIST is ever
copied into it; sources are named by hash, never by content.

Subcommands:

  check  Runs before the external call. Fails the job when the policy file or the decision
         record named on the command line is missing from the checkout, or when the
         declared data class is not one this caller may send.
  write  Runs after the external call. Appends one JSON line to --out and a rendered copy
         to --summary.

Standard library only. Exit code 1 on any failure. In check mode a failure writes nothing and
stops the job before the call. In write mode the call has already happened, so a failure is
recorded as a refused decision and still written, then reported with exit code 1.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIELDS = ("provider", "purpose", "data_class", "source_hashes", "policy_decision", "time", "result_ref")
DATA_CLASSES = ("PUBLIC", "INTERNAL", "PROJECT_CONFIDENTIAL", "SECRET")
ENV_ALLOWLIST = (
    "GITHUB_SERVER_URL",
    "GITHUB_REPOSITORY",
    "GITHUB_RUN_ID",
    "GITHUB_RUN_ATTEMPT",
    "GITHUB_JOB",
    "GITHUB_SHA",
    "GITHUB_EVENT_PATH",
    "GITHUB_WORKFLOW_REF",
)
SECRET_PATTERNS = (
    ("github token", re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}")),
    ("github pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("anthropic key or oauth token", re.compile(r"sk-ant-[A-Za-z0-9_-]{16,}")),
    ("api key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("aws access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)


def read_env(env):
    """Only these variables are ever read. A secret in the environment cannot reach a record."""
    return {name: env[name] for name in ENV_ALLOWLIST if env.get(name)}


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def git_tree(root):
    """SHA-1 of the tree that was on disk: the upper bound of what the provider could be shown.

    Never raises. In write mode the submission has already happened, so a git that fails or is
    absent must degrade the record, not prevent it: an unrecorded submission is the one outcome
    this module exists to make impossible. A record carrying "unavailable" is honest and visible.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=root, capture_output=True, text=True, check=True
        )
    except (subprocess.CalledProcessError, OSError):
        return "unavailable"
    return result.stdout.strip() or "unavailable"


def source_hashes(root, env, prompt_source=None):
    """Labelled hashes of what the run made available, not of what the provider chose to read."""
    hashes = ["checkout-tree:sha1:" + git_tree(root)]
    if env.get("GITHUB_SHA"):
        hashes.append("checkout-commit:sha1:" + env["GITHUB_SHA"])
    event = env.get("GITHUB_EVENT_PATH")
    if event and os.path.exists(event):
        hashes.append("event-payload:sha256:" + sha256_file(event))
    if prompt_source:
        hashes.append("prompt-source:sha256:" + sha256_file(os.path.join(root, prompt_source)))
    return hashes


def sha256_or_absent(root, relative):
    """A missing file is recorded as absent rather than crashing, so no submission goes unrecorded."""
    try:
        return sha256_file(os.path.join(root, relative))
    except OSError:
        return "absent"


def policy_decision(root, policy, decision, rule, outcome="allow"):
    return {
        "outcome": outcome,
        "rule": rule,
        "policy": policy,
        "policy_sha256": sha256_or_absent(root, policy),
        "decision": decision,
        "decision_sha256": sha256_or_absent(root, decision),
    }


def result_ref(env, session_id=None, conclusion=None, entity_url=None):
    server = env.get("GITHUB_SERVER_URL", "")
    repository = env.get("GITHUB_REPOSITORY", "")
    run_id = env.get("GITHUB_RUN_ID", "")
    attempt = env.get("GITHUB_RUN_ATTEMPT", "1")
    reference = {
        "run_url": f"{server}/{repository}/actions/runs/{run_id}/attempts/{attempt}",
        "job": env.get("GITHUB_JOB", ""),
        "workflow_ref": env.get("GITHUB_WORKFLOW_REF", ""),
    }
    if entity_url:
        reference["entity_url"] = entity_url
    reference["provider_session_id"] = session_id or "not reported"
    reference["conclusion"] = conclusion or "not reported"
    return reference


def check_preconditions(root, policy, decision, data_class, allowed_classes):
    failures = []
    if data_class not in DATA_CLASSES:
        failures.append(f"check: data class not one of {', '.join(DATA_CLASSES)}: {data_class}")
    if data_class == "SECRET":
        failures.append("check: SECRET may never be submitted to any provider")
    if data_class not in allowed_classes:
        failures.append(f"check: this caller may send {', '.join(allowed_classes)}, not {data_class}")
    for label, relative in (("policy", policy), ("decision", decision)):
        if not os.path.exists(os.path.join(root, relative)):
            failures.append(f"check: {label} file missing from the checkout: {relative}")
    return failures


def secret_shapes(text):
    return [label for label, pattern in SECRET_PATTERNS if pattern.search(text)]


def build_record(root, env, provider, purpose, data_class, policy, decision, rule,
                 prompt_source=None, session_id=None, conclusion=None, entity_url=None,
                 outcome="allow"):
    return {
        "provider": provider,
        "purpose": purpose,
        "data_class": data_class,
        "source_hashes": source_hashes(root, env, prompt_source),
        "policy_decision": policy_decision(root, policy, decision, rule, outcome),
        "time": utc_now(),
        "result_ref": result_ref(env, session_id, conclusion, entity_url),
    }


def serialise(record):
    """One JSON line. Refuses a record whose field set is not the seven, or that looks like a secret."""
    if tuple(record) != FIELDS:
        raise ValueError(f"record fields must be exactly {FIELDS}, got {tuple(record)}")
    line = json.dumps(record, sort_keys=True, separators=(",", ":"))
    shapes = secret_shapes(line)
    if shapes:
        raise ValueError("record refused, it contains a value shaped like a " + " and a ".join(shapes))
    return line


def render(record):
    rows = ["| Field | Value |", "|---|---|"]
    for field in FIELDS:
        value = record[field]
        if isinstance(value, list):
            text = "<br>".join(value)
        elif isinstance(value, dict):
            text = "<br>".join(f"{key}: {item}" for key, item in value.items())
        else:
            text = str(value)
        rows.append(f"| `{field}` | {text} |")
    return "### External submission record\n\n" + "\n".join(rows) + "\n"


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("check", "write"))
    parser.add_argument("--provider", required=True)
    parser.add_argument("--purpose", required=True)
    parser.add_argument("--data-class", required=True)
    parser.add_argument("--allowed-classes", default="PUBLIC,INTERNAL")
    parser.add_argument("--policy", default="docs/policies/egress.md")
    parser.add_argument("--decision", required=True)
    parser.add_argument("--rule", required=True)
    parser.add_argument("--prompt-source", default=None)
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--conclusion", default=None)
    parser.add_argument("--entity-url", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--summary", default=None)
    return parser.parse_args(argv)


def main(argv=None, env=None, root=ROOT):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    allowed = tuple(name.strip() for name in args.allowed_classes.split(",") if name.strip())
    failures = check_preconditions(root, args.policy, args.decision, args.data_class, allowed)
    if args.mode == "check":
        for failure in failures:
            print(failure)
        if failures:
            return 1
        print(f"check: {args.provider} may receive {args.data_class} under {args.decision}")
        return 0
    # In write mode the submission has already happened, so a failed precondition is recorded
    # as a refused decision and reported, never turned into a missing line.
    record = build_record(
        root, read_env(env if env is not None else os.environ),
        args.provider, args.purpose, args.data_class, args.policy, args.decision, args.rule,
        args.prompt_source, args.session_id, args.conclusion, args.entity_url,
        outcome="allow" if not failures else "refused",
    )
    try:
        line = serialise(record)
    except ValueError as err:
        print(f"write: {err}")
        return 1
    if args.out:
        with open(args.out, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write(render(record))
    print(line)
    for failure in failures:
        print(failure)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
