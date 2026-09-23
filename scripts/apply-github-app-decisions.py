#!/usr/bin/env python3
"""Validate an owner decision packet; atomically update only the main registry.

Dry-run is the default. This local operation does not change GitHub Apps, fetch
evidence, commit, push, publish workflows, or grant release acceptance. Evidence
references are owner assertions and are never followed. Concurrent writers must
remain quiescent: Git refs and filesystem replacement are not one transaction.
"""

import argparse
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile


sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location(
    "foundation_release_status", Path(__file__).with_name("foundation-release-status.py"))
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)
StateError = release.StateError
require = release.require
state = release.state
BASE_FIELDS = tuple("name origin version_or_digest license_or_rights data_class owner "
                    "permissions egress evaluation rollback disposition".split())
DECISION_FIELDS = ("decision_owner", "decision_date", "decision_evidence", "installation_evidence")
REPLACED_FIELDS = ("version_or_digest", "permissions", "egress", "evaluation", "disposition")
APP_FIELDS = ("name", *REPLACED_FIELDS, "decision_evidence", "installation_evidence")
PACKET_FIELDS = ("schema", "repository", "expected_head", "expected_registry_sha256",
                 "decision_owner", "decision_date", "apps")
FIELD_LIMITS = {"version_or_digest": 4096, "permissions": 8192, "egress": 4096,
                "evaluation": 8192, "decision_evidence": 2048, "installation_evidence": 2048}
ASSIGNMENT = re.compile(
    r'([ \t]*)([A-Za-z0-9_]+)([ \t]*=[ \t]*)("(?:[^"\\\x00-\x1f]|\\["\\bfnrt]|\\u[0-9a-fA-F]{4})*")([ \t]*)')


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes unrecognized arguments, possibly packet data.
        raise StateError("invalid_arguments", "Invalid arguments; use --help for the command contract.")


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def reject_git_overrides():
    overrides = {
        "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
        "GIT_NAMESPACE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
    }
    require(not any(name in overrides for name in os.environ),
            "git_environment_override", "Unset Git routing and configuration overrides before inspection.")


def exact_shape(value, fields):
    require(type(value) is dict and set(value) == set(fields),
            "invalid_packet", "Decision packet fields do not match the contract.")


def printable(value, limit):
    require(type(value) is str and 0 < len(value) <= limit and value == value.strip()
            and all(32 <= ord(character) <= 126 for character in value),
            "invalid_packet", "Decision strings must be bounded, trimmed, nonblank printable ASCII.")


def packet(raw):
    value = release.parse_json(raw)
    exact_shape(value, PACKET_FIELDS)
    require(value["schema"] == "heleos.github-app-decisions/v1"
            and value["repository"] == "bbukolla-eng/Heleos-spark"
            and value["decision_owner"] == "Bekim Bukolla",
            "invalid_packet", "Decision packet schema, repository or owner does not match.")
    release.digest(value["expected_head"], 40)
    release.digest(value["expected_registry_sha256"])
    date = value["decision_date"]
    require(type(date) is str and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date),
            "invalid_packet", "Decision date must be a real YYYY-MM-DD date.")
    try:
        datetime.date.fromisoformat(date)
    except ValueError:
        raise StateError("invalid_packet", "Decision date must be a real YYYY-MM-DD date.")
    require(type(value["apps"]) is list and len(value["apps"]) == 4,
            "invalid_packet", "Decision packet requires exactly four app decisions.")
    names = set()
    for app in value["apps"]:
        exact_shape(app, APP_FIELDS)
        require(type(app["name"]) is str and app["name"] in release.APP_NAMES
                and app["name"] not in names,
                "invalid_packet", "Decision packet requires every exact app name once.")
        names.add(app["name"])
        require(type(app["disposition"]) is str
                and app["disposition"] in ("retain", "restrict", "suspend", "remove"),
                "invalid_packet", "App disposition must be retain, restrict, suspend or remove.")
        for key, limit in FIELD_LIMITS.items():
            printable(app[key], limit)
    return value


def registry_lines(raw):
    """Parse only the admitted TOML subset, retaining every untouched byte."""
    release.apps(raw)  # Validate current unresolved or resolved semantics.
    lines = raw.decode("utf-8").splitlines(keepends=True)
    records = []
    for index, line in enumerate(lines):
        content = line.removesuffix("\n").removesuffix("\r")
        stripped = content.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "[[github_app]]":
            records.append({"values": {}, "lines": {}})
            continue
        match = ASSIGNMENT.fullmatch(content)
        require(records and match is not None,
                "invalid_apps", "Unsupported GitHub App registry syntax.")
        key = match[2]
        require(key in BASE_FIELDS + DECISION_FIELDS and key not in records[-1]["values"],
                "invalid_apps", "Unknown or duplicate GitHub App registry field.")
        decoded = json.loads(match[4])
        require(not any(0xD800 <= ord(character) <= 0xDFFF for character in decoded),
                "invalid_apps", "Registry strings must contain valid Unicode scalar values.")
        records[-1]["values"][key] = decoded
        records[-1]["lines"][key] = (index, match)
    require(len(records) == 4 and all(set(BASE_FIELDS) <= set(record["values"]) for record in records),
            "invalid_apps", "Registry must contain four complete app records.")
    return lines, records


def proposed_registry(raw, decision):
    lines, records = registry_lines(raw)
    decisions = {app["name"]: app for app in decision["apps"]}
    replacements = {}
    deletions = set()
    summaries = []
    for record in records:
        app = decisions[record["values"]["name"]]
        summaries.append({"name": app["name"], "disposition": app["disposition"]})
        for key in REPLACED_FIELDS:
            index, match = record["lines"][key]
            ending = "\r\n" if lines[index].endswith("\r\n") else "\n" if lines[index].endswith("\n") else ""
            replacements[index] = (match[1] + key + match[3]
                                   + json.dumps(app[key], ensure_ascii=True) + match[5] + ending)
        index, match = record["lines"]["disposition"]
        ending = "\r\n" if lines[index].endswith("\r\n") else "\n"
        if not replacements[index].endswith("\n"):
            replacements[index] += ending
        for key in DECISION_FIELDS:
            value = decision[key] if key in ("decision_owner", "decision_date") else app[key]
            replacements[index] += match[1] + key + " = " + json.dumps(value, ensure_ascii=True) + ending
            if key in record["lines"]:
                deletions.add(record["lines"][key][0])
    proposed = "".join(replacements.get(index, line)
                       for index, line in enumerate(lines) if index not in deletions).encode("utf-8")
    require(len(proposed) <= release.LIMIT,
            "oversized_file", "Proposed registry exceeds the release verifier size limit.")
    require(release.apps(proposed), "invalid_apps", "Proposed registry decisions are incomplete.")
    return proposed, summaries


def identity(path):
    info = release.canonical(path).lstat()
    require(stat.S_ISREG(info.st_mode), "invalid_file", "Registry must be a regular file.")
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def check_main(root, expected_head, committed, proposed, target, expected_identity=None):
    observed_root, observed = release.main_checkout(root)
    require(observed_root == root and observed["head"] == expected_head,
            "head_mismatch", "Main HEAD does not match the decision packet.")
    before_identity = identity(target)
    current = release.read_file(target)
    require(identity(target) == before_identity
            and (expected_identity is None or before_identity == expected_identity),
            "state_changed", "Registry identity changed during inspection.")
    already = current == proposed
    require(already or current == committed,
            "registry_drift", "Working registry differs from committed or exact already-applied bytes.")
    tracked = observed["tracked_changes"]
    # Only an unstaged byte-identical prior application is exempt. Staged changes,
    # mode changes, renames and unrelated tracked modifications remain failures.
    require(not tracked or (already and len(tracked) == 1
            and tracked[0] == {"status": " M", "path": release.APP_PATH}),
            "tracked_changes", "Main contains tracked changes outside an exact prior application.")
    index = state.git(root, "ls-files", "--stage", "-z", "--", release.APP_PATH)[1]
    tree = state.git(root, "ls-tree", "-z", expected_head, "--", release.APP_PATH)[1]
    match = re.fullmatch(rb"(100644|100755) blob ([0-9a-f]{40})\t" + re.escape(release.APP_PATH.encode()) + b"\0", tree)
    require(match is not None and index == match[1] + b" " + match[2] + b" 0\t" + release.APP_PATH.encode() + b"\0",
            "registry_drift", "Registry index differs from committed regular-file identity.")
    if os.name != "nt":
        require(bool(before_identity[2] & 0o111) == (match[1] == b"100755"),
                "registry_drift", "Registry executable mode differs from its committed mode.")
    return current, before_identity, already


def apply(root, decision, committed, proposed, target, original_identity, report):
    temporary = None
    temporary_identity = None
    directory_fd = None
    try:
        parent = release.canonical(target.parent)
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                               | getattr(os, "O_NOFOLLOW", 0))
        parent_identity = os.fstat(directory_fd)
        descriptor, name = tempfile.mkstemp(prefix=".github-apps-decisions-", suffix=".tmp", dir=str(parent))
        temporary = Path(name)
        temporary_identity = os.fstat(descriptor)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(proposed)
            stream.flush()
            os.fchmod(stream.fileno(), stat.S_IMODE(original_identity[2]))
            os.fsync(stream.fileno())
        # Recheck the source packet as well as main and the original target before
        # the single replacement. No mutation to Git or its index is necessary.
        require(sha256(release.read_file(Path(report["packet"]["path"]))) == report["packet"]["sha256"],
                "state_changed", "Decision packet changed during inspection.")
        current, _, already = check_main(root, decision["expected_head"], committed, proposed,
                                         target, original_identity)
        require(current == committed and not already,
                "state_changed", "Registry changed before application.")
        current_parent = release.canonical(parent).lstat()
        require((current_parent.st_dev, current_parent.st_ino)
                == (parent_identity.st_dev, parent_identity.st_ino),
                "state_changed", "Registry directory changed before application.")
        temp_info = temporary.lstat()
        require(stat.S_ISREG(temp_info.st_mode) and (temp_info.st_dev, temp_info.st_ino)
                == (temporary_identity.st_dev, temporary_identity.st_ino)
                and release.read_file(temporary) == proposed,
                "state_changed", "Temporary registry changed before application.")
        require(state.git_text(root, "rev-parse", "--verify", "HEAD^{commit}") == decision["expected_head"]
                and state.branch_name(root) == "main" and identity(target) == original_identity
                and release.read_file(target) == committed,
                "state_changed", "Main or registry changed immediately before application.")
        os.replace(temporary, target)
        temporary = None
        report["write_performed"] = True
        os.fsync(directory_fd)
        require(release.read_file(target) == proposed
                and sha256(release.read_file(target)) == report["registry"]["after_sha256"]
                and stat.S_IMODE(identity(target)[2]) == stat.S_IMODE(original_identity[2]),
                "write_verification_failed", "Replaced registry did not retain the expected bytes.")
    finally:
        if temporary is not None and temporary_identity is not None:
            try:
                remaining = temporary.lstat()
                if (remaining.st_dev, remaining.st_ino) == (temporary_identity.st_dev, temporary_identity.st_ino):
                    temporary.unlink()
            except FileNotFoundError:
                pass
        if directory_fd is not None:
            os.close(directory_fd)


def inspect(args, report):
    reject_git_overrides()
    packet_path = release.canonical(args.packet)
    raw = release.read_file(packet_path)
    report["packet"] = {"path": str(packet_path), "sha256": sha256(raw)}
    decision = packet(raw)
    root, observed = release.main_checkout(release.canonical(args.repo))
    report["main"] = {"path": str(root), "head": observed["head"]}
    require(observed["head"] == decision["expected_head"],
            "head_mismatch", "Main HEAD does not match the decision packet.")
    target = release.canonical(root / release.APP_PATH)
    committed, _ = release.committed_file(root, decision["expected_head"], release.APP_PATH)
    report["registry"] = {"path": str(target), "before_sha256": sha256(committed), "after_sha256": None}
    require(sha256(committed) == decision["expected_registry_sha256"],
            "registry_hash_mismatch", "Committed registry digest does not match the decision packet.")
    proposed, summaries = proposed_registry(committed, decision)
    report["registry"]["after_sha256"] = sha256(proposed)
    report["apps"] = summaries
    current, original_identity, already = check_main(root, decision["expected_head"], committed, proposed, target)
    report["registry"]["before_sha256"] = sha256(current)
    if already:
        report["status"] = "ALREADY_APPLIED"
    elif args.apply:
        apply(root, decision, committed, proposed, target, original_identity, report)
        report["status"] = "APPLIED"
    else:
        report["status"] = "DRY_RUN"


def main(argv=None):
    report = {"schema_version": 1, "status": "FAIL", "main": {"path": None, "head": None},
              "packet": {"path": None, "sha256": None},
              "registry": {"path": None, "before_sha256": None, "after_sha256": None},
              "apps": [], "write_performed": False, "account_changes_performed": False, "errors": []}
    args = None
    try:
        parser = Parser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--repo", default=".", help="checkout or nested directory (default: current directory)")
        parser.add_argument("--packet", required=True, help="strict owner decision packet JSON path")
        parser.add_argument("--apply", action="store_true", help="atomically replace the main registry after validation")
        parser.add_argument("--human", action="store_true", help="print a concise human-readable report")
        args = parser.parse_args(argv)
        inspect(args, report)
    except StateError as error:
        report["errors"].append({"code": error.code, "message": str(error)})
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError, StopIteration) as error:
        report["errors"].append({"code": "application_failed",
                                 "message": "Local decision application failed (" + type(error).__name__ + ")."})
    if report["errors"]:
        report["status"] = "FAIL"
    if args is not None and args.human:
        print(report["status"])
        if report["main"]["path"] is not None:
            print("Main: " + report["main"]["path"] + " " + report["main"]["head"])
        for app in report["apps"]:
            print(app["name"] + ": " + app["disposition"])
        print("Registry write: " + ("yes" if report["write_performed"] else "no") + "; account changes: no")
        for error in report["errors"]:
            print(error["code"] + ": " + error["message"])
    else:
        print(json.dumps(report, sort_keys=True, ensure_ascii=True, separators=(",", ":")))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
