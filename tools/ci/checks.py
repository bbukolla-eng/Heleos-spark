"""Repository checks run by CI and locally: registry hashes, JSON validity, relative links and image
destinations, prose rule.

Standard library only. Exit code 1 on any failure; every failure is printed with its file.
"""
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROSE_SCOPE = (
    "ROADMAP.md",
    "CLAUDE.md",
    "README.md",
    "docs/roadmap",
    "docs/policies",
    "docs/decisions",
    "docs/runs",
    ".claude/workflows",
)
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s#]+)(?:#[^)]*)?\)")
DASHES = ("—", "–")
EGRESS_CLASSES = ("PUBLIC", "INTERNAL", "PROJECT_CONFIDENTIAL")
EGRESS_FIELDS = ("provider", "purpose", "data_class", "source_hashes", "policy_decision", "time", "result_ref")
RETAINED_ORIGINS = "docs/operations/retained-document-origins.json"


def tracked_files(root=ROOT):
    out = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line]


def check_registry(root=ROOT):
    """Every registry entry's script must exist and match its recorded sha256 (a prefix is accepted)."""
    failures = []
    path = os.path.join(root, ".claude", "workflows", "REGISTRY.json")
    if not os.path.exists(path):
        return failures
    with open(path, encoding="utf-8") as handle:
        registry = json.load(handle)
    entries = registry.get("workflows", registry) if isinstance(registry, dict) else registry
    items = entries.items() if isinstance(entries, dict) else [(e.get("name"), e) for e in entries]
    for name, entry in items:
        if not isinstance(entry, dict):
            continue
        script = entry.get("script") or entry.get("path") or f"{name}.js"
        if os.sep not in script and "/" not in script:
            script = os.path.join(".claude", "workflows", script)
        expected = entry.get("sha256")
        full = os.path.join(root, script)
        if not os.path.exists(full):
            failures.append(f"registry: {name}: script missing: {script}")
            continue
        if expected:
            with open(full, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            if not digest.startswith(expected):
                failures.append(f"registry: {name}: sha256 {digest[:12]} does not match recorded {expected}")
    return failures


def check_json(files, root=ROOT):
    failures = []
    for rel in files:
        if rel.endswith(".json"):
            try:
                with open(os.path.join(root, rel), encoding="utf-8") as handle:
                    json.load(handle)
            except (ValueError, OSError) as err:
                failures.append(f"json: {rel}: {err}")
    return failures


def _regular_repository_file(rel, root):
    """Return a canonical in-repository regular file; do not follow symbolic links."""
    if (not isinstance(rel, str) or not rel or "\\" in rel or ":" in rel
            or any(ord(char) < 32 for char in rel)
            or any(part in ("", ".", "..") for part in rel.split("/"))):
        raise ValueError(f"unsafe root-relative path: {rel!r}")
    path = os.path.abspath(root)
    for part in rel.split("/"):
        path = os.path.join(path, part)
        if os.path.islink(path):
            raise ValueError(f"symbolic link in path: {rel}")
    if not os.path.isfile(path):
        raise ValueError(f"not a regular file: {rel}")
    return path


def retained_document_origins(root=ROOT):
    """Validate exact retained bytes and their declared original link-resolution locations.

    The origin records do not assert that the current original file has the same content.
    Every registered copy is validated, even if it is not in the selected Markdown list.
    """
    origins, failures = {}, []
    registry_path = os.path.join(root, RETAINED_ORIGINS)
    if not os.path.lexists(registry_path):
        return origins, failures
    try:
        with open(_regular_repository_file(RETAINED_ORIGINS, root), encoding="utf-8") as handle:
            registry = json.load(handle)
        if (not isinstance(registry, dict) or set(registry) != {"schema_version", "documents"}
                or type(registry["schema_version"]) is not int or registry["schema_version"] != 1
                or not isinstance(registry["documents"], list)):
            raise ValueError("expected schema_version 1 and documents array")
    except (ValueError, OSError) as error:
        return origins, [f"retained origins: {RETAINED_ORIGINS}: {error}"]
    seen = set()
    for number, entry in enumerate(registry["documents"], 1):
        try:
            if not isinstance(entry, dict) or set(entry) != {"retained_path", "source_path", "sha256", "basis"}:
                raise ValueError("expected retained_path, source_path, sha256 and basis")
            retained = _regular_repository_file(entry["retained_path"], root)
            _regular_repository_file(entry["source_path"], root)
            if not entry["retained_path"].endswith(".md") or not entry["source_path"].endswith(".md"):
                raise ValueError("retained and source paths must be Markdown files")
            if entry["retained_path"] in seen:
                raise ValueError(f"duplicate retained_path: {entry['retained_path']}")
            seen.add(entry["retained_path"])
            if not isinstance(entry["basis"], str) or not entry["basis"].strip():
                raise ValueError("basis must be a non-empty string")
            if not isinstance(entry["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
                raise ValueError("sha256 must be a full lowercase digest")
            with open(retained, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            if digest != entry["sha256"]:
                raise ValueError(f"sha256 mismatch for {entry['retained_path']}: {digest}")
            origins[entry["retained_path"]] = entry["source_path"]
        except (ValueError, OSError) as error:
            failures.append(f"retained origins: entry {number}: {error}")
    return origins, failures


def check_links(files, root=ROOT):
    """Relative markdown link and image destinations must resolve to an existing file or directory."""
    origins, failures = retained_document_origins(root)
    for rel in files:
        if not rel.endswith(".md"):
            continue
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as handle:
                text = handle.read()
        except (ValueError, OSError) as error:
            failures.append(f"link: {rel}: cannot read: {error}")
            continue
        base = os.path.dirname(os.path.join(root, origins.get(rel, rel)))
        for target in LINK_RE.findall(text):
            if re.match(r"^[a-z]+:", target) or target.startswith("/"):
                continue
            if not os.path.exists(os.path.normpath(os.path.join(base, target))):
                failures.append(f"link: {rel}: {target}")
    return failures


def check_prose(files, root=ROOT):
    """No em or en dash in repository documents (the spec and generated skill files are out of scope)."""
    failures = []
    for rel in files:
        if not rel.endswith(".md") or not rel.startswith(PROSE_SCOPE):
            continue
        with open(os.path.join(root, rel), encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if any(dash in line for dash in DASHES):
                    failures.append(f"prose: {rel}:{number}: em or en dash")
    return failures


def check_egress_ledger(root=ROOT):
    """Every committed external-submission record carries the seven spec section 5 fields,
    names a decision file that exists in the tree, and records an allowed outcome."""
    failures = []
    path = os.path.join(root, "docs", "runs", "egress", "index.jsonl")
    if not os.path.exists(path):
        return failures
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            where = f"egress: index.jsonl:{number}"
            try:
                record = json.loads(line)
            except ValueError as err:
                failures.append(f"{where}: not valid JSON: {err}")
                continue
            if not isinstance(record, dict) or set(record) != set(EGRESS_FIELDS):
                # The recorder serialises with sort_keys, so compare the key set, never the order.
                failures.append(f"{where}: fields must be exactly {EGRESS_FIELDS}, got {sorted(record)}")
                continue
            if record["data_class"] not in EGRESS_CLASSES:
                failures.append(f"{where}: data class not one of {EGRESS_CLASSES}: {record['data_class']}")
            if not isinstance(record["source_hashes"], list) or not record["source_hashes"]:
                failures.append(f"{where}: source_hashes must be a non-empty list")
            for key in ("provider", "purpose", "time"):
                if not isinstance(record[key], str) or not record[key].strip():
                    failures.append(f"{where}: {key} must be a non-empty string")
            decision = record["policy_decision"]
            if not isinstance(decision, dict):
                failures.append(f"{where}: policy_decision must be an object, got {type(decision).__name__}")
                continue
            if decision.get("outcome") != "allow":
                failures.append(f"{where}: outcome is {decision.get('outcome')}, not allow")
            if not str(decision.get("rule", "")).strip():
                failures.append(f"{where}: no rule recorded")
            for key in ("policy", "decision"):
                target = decision.get(key)
                if not isinstance(target, str) or not os.path.exists(os.path.join(root, target)):
                    failures.append(f"{where}: {key} file named by the record is not in the tree: {target}")
                elif not re.fullmatch(r"[0-9a-f]{64}", str(decision.get(key + "_sha256", ""))):
                    failures.append(f"{where}: {key}_sha256 is not a sha256 digest")
    return failures


def main(root=ROOT):
    files = tracked_files(root)
    failures = (check_registry(root) + check_json(files, root) + check_links(files, root)
                + check_prose(files, root) + check_egress_ledger(root))
    for failure in failures:
        print(failure)
    print(f"{len(files)} tracked files checked, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
