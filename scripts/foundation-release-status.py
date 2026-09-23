#!/usr/bin/env python3
"""Inspect local Foundation release gates without writes or network access.

Evidence links are structurally checked, never fetched. Transfer readiness is
not native execution evidence. Exit 1 denotes an invalid inspection; otherwise
blocked gates exit 0 (2 with --require-ready). This observes, not locks, files.
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


sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location(
    "repo_state", Path(__file__).with_name("verify-repo-state.py"))
state = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(state)
StateError = state.StateError
LIMIT = 65536
APP_PATH = "governance/github-apps.toml"
WORKFLOW = ".github/workflows/core-ci.yml"
DOSSIER = "docs/verification/foundation-0.1.md"
IMPORTER = "scripts/import-foundation-native-candidate.ps1"
APP_NAMES = {"Azure Pipelines", "AWS Connector for GitHub", "Amazon Q Developer", "ECC Tools"}
GATES = ("github_apps", "workflow_candidate", "owner_git_handoff",
         "same_sha_platforms", "acceptance_dossier")
BASE_URL = "https://github.com/bbukolla-eng/Heleos-spark/"


def require(condition, code, message):
    if not condition:
        raise StateError(code, message)


def canonical(value):
    """Reject aliases, traversal and symlink/reparse-point path components."""
    raw = os.fspath(value)
    require(isinstance(raw, str) and raw and "\\" not in raw
            and ".." not in raw.split("/") and "\x00" not in raw,
            "invalid_path", "Paths must be canonical and contain no traversal.")
    path = Path(raw)
    require(raw in (".", str(path)),
            "invalid_path", "Paths must use a canonical spelling.")
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(info.st_mode)
                and not getattr(info, "st_file_attributes", 0) & 0x400,
                "invalid_path", "Symlink or reparse-point paths are not accepted.")
    require(path.resolve() == path, "invalid_path", "Paths must not contain aliases.")
    return path


def read_file(path, limit=LIMIT):
    path = canonical(path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode), "invalid_file", "Expected a regular evidence file.")
    require(info.st_size <= limit, "oversized_file", "Evidence file exceeds its size limit.")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        require(stat.S_ISREG(opened.st_mode) and (info.st_dev, info.st_ino) ==
                (opened.st_dev, opened.st_ino), "state_changed", "Evidence changed during inspection.")
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "oversized_file", "Evidence file exceeds its size limit.")
    return raw


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_json_key", "Duplicate JSON keys are not accepted.")
        result[key] = value
    return result


def parse_json(raw):
    require(len(raw) <= LIMIT, "oversized_file", "JSON exceeds 64 KiB.")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise StateError("invalid_json", "Evidence must be strict UTF-8 JSON.")


def shape(value, keys):
    require(type(value) is dict and set(value) == set(keys.split()),
            "invalid_evidence", "Evidence object fields do not match the contract.")


def digest(value, length=64):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{%d}" % length, value),
            "invalid_evidence", "Evidence requires complete lowercase digests.")
    return value


def substantive(value):
    if not isinstance(value, str):
        return False
    normalized = " ".join(value.lower().replace("-", " ").replace("_", " ").split())
    placeholders = ("pending", "unknown", "not inventoried", "not evaluated", "tbd",
                    "unversioned", "latest", "owner decision required", "unavailable")
    return bool(normalized) and not any(
        re.match(re.escape(word) + r"(?:$|[^a-z0-9])", normalized) for word in placeholders)


def apps(raw):
    """Accept the registry's deliberately constrained quoted-string TOML subset."""
    records = []
    for line in raw.decode("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[[github_app]]":
            records.append({})
            continue
        match = re.fullmatch(r'([A-Za-z0-9_]+)\s*=\s*("(?:[^"\\\x00-\x1f]|\\["\\/bfnrt]|\\u[0-9a-fA-F]{4})*")', line)
        require(records and match is not None, "invalid_apps", "Unsupported GitHub App registry syntax.")
        key, encoded = match.groups()
        require(key not in records[-1], "invalid_apps", "Duplicate GitHub App registry field.")
        records[-1][key] = json.loads(encoded)
    require(len(records) == 4 and {record.get("name") for record in records} == APP_NAMES,
            "invalid_apps", "Registry must contain exactly the four unique required GitHub Apps.")
    required = "name origin version_or_digest license_or_rights data_class owner permissions egress evaluation rollback disposition".split()
    for record in records:
        require(all(record.get(key, "").strip() for key in required),
                "invalid_apps", "GitHub App registry is missing required fields.")
        require(record["data_class"] in ("PUBLIC", "INTERNAL", "PROJECT_CONFIDENTIAL", "SECRET"),
                "invalid_apps", "Invalid GitHub App data classification.")
        disposition = record["disposition"]
        require(disposition in ("owner_decision_required", "retain", "restrict", "suspend", "remove"),
                "invalid_apps", "Unknown GitHub App disposition.")
        inactive = disposition in ("owner_decision_required", "suspend", "remove")
        egress = record["egress"]
        require(egress.startswith("prohibited") or (not inactive and egress.startswith("approved:")
                and substantive(egress[len("approved:"):])),
                "invalid_apps", "GitHub App egress conflicts with its disposition.")
        if disposition == "owner_decision_required":
            continue
        for key in ("version_or_digest", "permissions", "evaluation", "decision_owner",
                    "decision_evidence", "installation_evidence"):
            value = record.get(key, "")
            unavailable = (inactive and key in ("version_or_digest", "permissions")
                           and value.startswith("unavailable:") and substantive(value[len("unavailable:"):]))
            require(unavailable or substantive(value), "invalid_apps", "GitHub App decision evidence is incomplete.")
            if not unavailable and key in ("version_or_digest", "permissions", "evaluation"):
                words = re.findall(r"[a-z0-9]+", value.lower())
                require(not set(words) & {"unknown", "unavailable", "pending", "unresolved"}
                        and not any(pair in (("not", "inventoried"), ("not", "evaluated"))
                                    for pair in zip(words, words[1:])),
                        "invalid_apps", "GitHub App inventory remains unresolved.")
            if key == "version_or_digest" and not unavailable:
                normalized = value.strip().lower().replace("-", " ").replace("_", " ")
                require(not re.fullmatch(r"[0-9]+", normalized)
                        and not normalized.startswith(("app id", "github app id", "installation")),
                        "invalid_apps", "App identities cannot replace version evidence.")
        date = record.get("decision_date", "")
        require(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date),
                "invalid_apps", "GitHub App decision date is invalid.")
        try:
            datetime.date.fromisoformat(date)
        except ValueError:
            raise StateError("invalid_apps", "GitHub App decision date is invalid.")
    return all(record["disposition"] != "owner_decision_required" for record in records)


def snapshot(repo):
    report = {"main_head": None, "divergence": None}
    state.inspect(repo, report)
    return report


def main_checkout(repo):
    initial = snapshot(repo)
    raw = state.git(repo, "worktree", "list", "--porcelain", "-z")[1]
    records = []
    for block in raw.split(b"\0\0"):
        record = {}
        for field in block.split(b"\0"):
            key, _, value = field.partition(b" ")
            if key:
                record[os.fsdecode(key)] = os.fsdecode(value)
        if record.get("branch") == "refs/heads/main":
            records.append(record)
    require(len(records) == 1 and "prunable" not in records[0],
            "main_checkout_unavailable", "Exactly one registered existing main checkout is required.")
    root = canonical(records[0]["worktree"])
    observed = snapshot(root)
    require(observed["branch"] == "main" and observed["checkout_root"] == str(root)
            and observed["git_common_dir"] == initial["git_common_dir"],
            "main_checkout_unavailable", "Main checkout does not match its registration.")
    return root, observed


def commit_exists(root, sha):
    digest(sha, 40)
    code, raw = state.git(root, "rev-parse", "--verify", "--quiet", sha + "^{commit}", allowed=(0, 1, 128))
    require(code == 0 and raw == (sha + "\n").encode(),
            "missing_candidate", "Evidence candidate commit is not present locally.")


def committed_file(root, sha, relative):
    code, entry = state.git(root, "ls-tree", "-z", sha, "--", relative, allowed=(0, 128))
    match = re.fullmatch(rb"(100644|100755) blob ([0-9a-f]{40})\t" + re.escape(relative.encode()) + rb"\x00", entry)
    require(code == 0 and match is not None, "invalid_committed_file", "Required committed regular file is missing.")
    size = int(state.git_text(root, "cat-file", "-s", match[2].decode()))
    require(size <= LIMIT, "oversized_file", "Committed evidence file exceeds its size limit.")
    return state.git(root, "cat-file", "blob", match[2].decode())[1], match[2].decode()


def hash_file(path):
    path = canonical(path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode), "invalid_file", "Bundle must be a regular file.")
    hasher, length = hashlib.sha256(), 0
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        require(stat.S_ISREG(opened.st_mode) and (info.st_dev, info.st_ino) ==
                (opened.st_dev, opened.st_ino), "state_changed", "Bundle changed during inspection.")
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
            length += len(chunk)
    return length, hasher.hexdigest()


def transfer(root, head, directory):
    directory = canonical(directory)
    require(directory.is_dir(), "invalid_handoff", "Handoff must be an existing regular directory.")
    require(state.git(root, "check-ignore", "--quiet", "--no-index", str(directory), allowed=(0, 1, 128))[0] == 0,
            "invalid_handoff", "Handoff must be ignored by the main checkout.")
    require(not state.git(root, "ls-files", "-z", "--", str(directory))[1],
            "invalid_handoff", "Handoff transfer files must remain untracked and ignored.")
    raw = read_file(directory / "candidate/candidate.json")
    candidate = parse_json(raw)
    shape(candidate, "schema branch commit bundle bundle_sha256 bundle_bytes native_gate required_filesystem native_gate_status")
    sha = digest(candidate["commit"], 40)
    digest(candidate["bundle_sha256"])
    require(candidate["schema"] == "heleos.foundation-windows-native-candidate/v1"
            and candidate["branch"] == "release/foundation-0.1-native-" + sha
            and candidate["bundle"] == "heleos-spark-" + sha + ".bundle"
            and type(candidate["bundle_bytes"]) is int and candidate["bundle_bytes"] > 0
            and candidate["native_gate"] == "scripts/verify-supply-chain.ps1"
            and candidate["required_filesystem"] == "NTFS" and candidate["native_gate_status"] == "pending",
            "invalid_handoff", "Handoff manifest does not match the Foundation transfer contract.")
    commit_exists(root, sha)
    manifest_hash = hashlib.sha256(raw).hexdigest()
    readme_hash = hashlib.sha256(read_file(directory / "candidate/README.md")).hexdigest()
    bundle = directory / "candidate" / candidate["bundle"]
    size, bundle_hash = hash_file(bundle)
    require(size == candidate["bundle_bytes"] and bundle_hash == candidate["bundle_sha256"],
            "invalid_handoff", "Bundle size or digest does not match the manifest.")
    trusted, _ = committed_file(root, head, IMPORTER)
    require(read_file(root / IMPORTER) == trusted, "invalid_handoff", "Trusted importer has uncommitted bytes.")
    importer_hash = hashlib.sha256(trusted).hexdigest()
    require(hashlib.sha256(read_file(directory / IMPORTER)).hexdigest() == importer_hash,
            "invalid_handoff", "Handoff importer differs from the committed trusted importer.")
    expected_sums = "".join(value + "  " + name + "\n" for value, name in (
        (manifest_hash, "candidate/candidate.json"), (readme_hash, "candidate/README.md"),
        (bundle_hash, "candidate/" + candidate["bundle"]), (importer_hash, IMPORTER)))
    require(read_file(directory / "SHA256SUMS.txt") == expected_sums.encode(),
            "invalid_handoff", "SHA256SUMS.txt does not contain the exact four verified checksum lines.")
    heads = state.git(root, "bundle", "list-heads", str(bundle))[1]
    require(heads == (sha + " refs/heads/" + candidate["branch"] + "\n").encode(),
            "invalid_handoff", "Bundle does not advertise exactly the Foundation candidate branch.")
    state.git(root, "bundle", "verify", str(bundle))
    require(read_file(directory / "candidate/candidate.json") == raw and hash_file(bundle) == (size, bundle_hash),
            "state_changed", "Handoff changed during inspection.")
    return {"status": "PASS", "candidate_sha": sha, "manifest_sha256": manifest_hash,
            "bundle_sha256": bundle_hash, "importer_sha256": importer_hash, "native_evidence": False}


def candidate_status(value, sha):
    require(value["status"] == "pass" and value["candidate_sha"] == sha,
            "invalid_evidence", "All evidence must pass for the same exact candidate SHA.")


def ledger(root, value):
    shape(value, "schema release_candidate_sha workflow git_handoff platforms")
    require(value["schema"] == "heleos.foundation-release-evidence/v1",
            "invalid_evidence", "Unsupported release evidence schema.")
    sha = value["release_candidate_sha"]
    commit_exists(root, sha)
    workflow = value["workflow"]
    shape(workflow, "status candidate_sha path blob_sha1 local_supply_chain")
    candidate_status(workflow, sha)
    require(workflow["path"] == WORKFLOW, "invalid_evidence", "Workflow path is not canonical Foundation CI.")
    digest(workflow["blob_sha1"], 40)
    _, blob = committed_file(root, sha, WORKFLOW)
    require(blob == workflow["blob_sha1"], "invalid_evidence", "Workflow digest differs from the candidate commit.")
    local = workflow["local_supply_chain"]
    shape(local, "status candidate_sha platform receipt_sha256")
    candidate_status(local, sha)
    require(local["platform"] == "macos-arm64", "invalid_evidence", "Local supply-chain platform is invalid.")
    digest(local["receipt_sha256"])
    handoff = value["git_handoff"]
    shape(handoff, "status candidate_sha authorization_reference push_reference")
    candidate_status(handoff, sha)
    reference = handoff["authorization_reference"]
    require(substantive(reference) and len(reference) <= 2048 and reference == reference.strip()
            and not any(ord(character) < 32 or ord(character) == 127 for character in reference),
            "invalid_evidence", "Owner Git authorization reference must be substantive, trimmed, and contain no controls.")
    require(handoff["push_reference"] == BASE_URL + "commit/" + sha,
            "invalid_evidence", "Push reference must name the exact repository candidate commit.")
    platforms = value["platforms"]
    shape(platforms, "candidate_sha macos windows")
    require(platforms["candidate_sha"] == sha, "invalid_evidence", "Platform group candidate SHA differs.")
    for name, platform in (("macos", "macos-arm64"), ("windows", "windows-x86_64")):
        record = platforms[name]
        extra = " filesystem suite_count native_receipt_sha256" if name == "windows" else ""
        shape(record, "status candidate_sha platform run_url artifact_sha256" + extra)
        candidate_status(record, sha)
        require(record["platform"] == platform, "invalid_evidence", "Platform identity is invalid.")
        require(isinstance(record["run_url"], str) and len(record["run_url"]) <= 2048 and re.fullmatch(
            re.escape(BASE_URL) + r"actions/runs/[0-9]+", record["run_url"]),
            "invalid_evidence", "Platform run reference must be an exact repository GitHub Actions run URL.")
        digest(record["artifact_sha256"])
        if name == "windows":
            require(record["filesystem"] == "NTFS" and type(record["suite_count"]) is int
                    and record["suite_count"] == 7, "invalid_evidence", "Windows evidence requires NTFS and seven suites.")
            digest(record["native_receipt_sha256"])
    candidate_apps, _ = committed_file(root, sha, APP_PATH)
    return sha, apps(candidate_apps)


def dossier(root, head, sha):
    path = canonical(root / DOSSIER)
    if not path.exists():
        return False
    raw = read_file(path)
    committed, _ = committed_file(root, head, DOSSIER)
    require(raw == committed, "invalid_dossier", "Acceptance dossier must match its committed regular file.")
    text = raw.decode("utf-8")
    opening, closing = "<!-- foundation-acceptance:v1 -->", "<!-- /foundation-acceptance -->"
    require(text.count(opening) == 1 and text.count(closing) == 1,
            "invalid_dossier", "Dossier requires exactly one acceptance machine block.")
    match = re.search(re.escape(opening) + r"\s*```json\s*\n(.*?)\n```\s*" + re.escape(closing), text, re.DOTALL)
    require(match is not None, "invalid_dossier", "Acceptance machine block must contain fenced JSON.")
    value = parse_json(match[1].encode())
    shape(value, "schema status release_candidate_sha decision_owner accepted_at")
    require(value["schema"] == "heleos.foundation-acceptance/v1" and value["status"] == "accepted"
            and value["release_candidate_sha"] == sha and value["decision_owner"] == "Bekim Bukolla",
            "invalid_dossier", "Acceptance decision does not match the release candidate and owner.")
    date = value["accepted_at"]
    require(isinstance(date, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", date),
            "invalid_dossier", "Acceptance timestamp requires strict UTC RFC3339 Z.")
    try:
        datetime.datetime.strptime(date, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        raise StateError("invalid_dossier", "Acceptance timestamp is invalid.")
    return True


def inspect(args, report):
    root, main = main_checkout(canonical(args.repo))
    report["main"] = {"path": str(root), "head": main["head"]}
    registry, _ = committed_file(root, main["head"], APP_PATH)
    require(read_file(root / APP_PATH) == registry,
            "invalid_apps", "Main GitHub App registry must match its committed regular file.")
    app_ready = apps(registry)
    report["gates"][0].update(status="PASS" if app_ready else "BLOCKED",
                              message="Owner dispositions evidenced." if app_ready else "Resolve the four GitHub App owner dispositions with evidence.")
    if args.handoff is not None:
        handoff = canonical(args.handoff)
    else:
        matches = []
        for path in sorted(root.glob("WINDOWS_NATIVE_HANDOFF_*")):
            canonical(path)
            if not path.is_dir():
                continue
            candidate = parse_json(read_file(path / "candidate/candidate.json"))
            shape(candidate, "schema branch commit bundle bundle_sha256 bundle_bytes native_gate required_filesystem native_gate_status")
            if candidate["schema"] == "heleos.foundation-windows-native-candidate/v1":
                matches.append(path)
            else:
                # Only a well-formed known worker-containment package can be
                # excluded. A damaged Foundation-like package is an error.
                require(candidate["schema"] == "heleos.windows-native-candidate/v1"
                        and candidate["native_gate"] == "scripts/verify-windows-worker-containment.ps1"
                        and candidate["required_filesystem"] == "NTFS"
                        and candidate["native_gate_status"] == "pending"
                        and type(candidate["bundle_bytes"]) is int and candidate["bundle_bytes"] > 0
                        and all(isinstance(value, str) for key, value in candidate.items() if key != "bundle_bytes"),
                        "invalid_handoff", "Unrecognized or malformed handoff during discovery.")
                digest(candidate["commit"], 40)
                digest(candidate["bundle_sha256"])
                require(candidate["bundle"] == "heleos-spark-" + candidate["commit"] + ".bundle",
                        "invalid_handoff", "Non-Foundation handoff bundle identity is malformed.")
        require(len(matches) <= 1, "ambiguous_handoff", "Multiple Foundation handoffs exist; select one with --handoff.")
        handoff = matches[0] if matches else None
    if handoff is not None:
        report["transfer"] = transfer(root, main["head"], handoff)
    evidence_path = canonical(args.evidence if args.evidence is not None else root / "governance/foundation-release-evidence.json")
    report["evidence"] = {"path": str(evidence_path), "links_not_fetched": True}
    sha = None
    if evidence_path.exists():
        sha, candidate_apps_ready = ledger(root, parse_json(read_file(evidence_path)))
        report["release_candidate_sha"] = sha
        report["gates"][0].update(
            status="PASS" if candidate_apps_ready else "BLOCKED",
            message="Candidate GitHub App owner dispositions are evidenced." if candidate_apps_ready
            else "Candidate GitHub App owner dispositions remain unresolved.")
        report["gates"][1].update(status="PASS", message="Candidate workflow and local receipt references match.")
        report["gates"][2].update(status="PASS", message="Owner authorization and exact commit push references are present.")
        report["gates"][3].update(status="PASS", message="Both platform references attest the same candidate structurally; links not fetched.")
    if sha is not None and dossier(root, main["head"], sha):
        report["gates"][4].update(status="PASS", message="Committed exact-candidate owner acceptance block validated.")
    elif sha is None and canonical(root / DOSSIER).exists():
        report["gates"][4]["message"] = "Release candidate evidence is required before dossier validation."
    require(state.git_text(root, "rev-parse", "HEAD") == main["head"]
            and read_file(root / APP_PATH) == registry,
            "state_changed", "Main identity or registry changed during inspection.")


def main(argv=None):
    report = {"schema_version": 1, "status": "FAIL", "main": None,
              "release_candidate_sha": None, "links_not_fetched": True,
              "transfer": {"status": "MISSING", "native_evidence": False}, "evidence": None,
              "gates": [{"id": name, "status": "BLOCKED", "message": "Release evidence is missing."}
                        for name in GATES], "blockers": [], "next_action": "", "errors": []}
    report["gates"][4]["message"] = "Prepare the committed owner acceptance dossier after the first four gates pass."
    human, require_ready = False, False
    try:
        parser = state.JsonArgumentParser(description=__doc__, allow_abbrev=False,
                                         formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument("--repo", default=".")
        parser.add_argument("--handoff")
        parser.add_argument("--evidence")
        parser.add_argument("--human", action="store_true")
        parser.add_argument("--require-ready", action="store_true")
        args = parser.parse_args(argv)
        human, require_ready = args.human, args.require_ready
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_NAMESPACE",
                     "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT"):
            require(name not in os.environ, "git_environment_override", "Git routing overrides are not accepted.")
        inspect(args, report)
    except StateError as error:
        # Errors deliberately expose only our fixed diagnostic text, not input bytes.
        message = "Invalid command arguments." if error.code == "invalid_arguments" else str(error)
        report["errors"].append({"code": error.code, "message": message})
    except (OSError, ValueError, TypeError, KeyError, StopIteration, RecursionError):
        report["errors"].append({"code": "inspection_failed", "message": "Local evidence inspection failed."})
    report["blockers"] = [gate["id"] for gate in report["gates"] if gate["status"] != "PASS"]
    if report["errors"]:
        report["status"] = "FAIL"
        report["next_action"] = "Resolve the inspection error and run a fresh inspection."
    elif any(gate["status"] != "PASS" for gate in report["gates"][:4]):
        report["status"] = "BLOCKED"
        report["next_action"] = next(gate["message"] for gate in report["gates"][:4] if gate["status"] != "PASS")
    elif report["gates"][4]["status"] == "PASS":
        report["status"] = "ACCEPTED"
        report["next_action"] = "Preserve the exact-candidate acceptance evidence."
    else:
        report["status"] = "READY_FOR_DOSSIER"
        report["next_action"] = report["gates"][4]["message"]
    if human:
        print(report["status"])
        print("Native transfer: " + report["transfer"]["status"] + " (not native execution evidence)")
        for gate in report["gates"]:
            print(gate["id"] + ": " + gate["status"] + " - " + gate["message"])
        for error in report["errors"]:
            print(error["code"] + ": " + error["message"])
        print("Next: " + report["next_action"])
        print("Evidence links were not fetched; transfer readiness is not native evidence.")
    else:
        print(json.dumps(report, sort_keys=True, ensure_ascii=True, separators=(",", ":")))
    return 1 if report["errors"] else (2 if require_ready and report["status"] == "BLOCKED" else 0)


if __name__ == "__main__":
    sys.exit(main())
