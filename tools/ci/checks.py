"""Repository checks run by CI and locally: registry hashes, JSON validity, relative links, prose rule.

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
LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s#]+)(?:#[^)]*)?\)")
DASHES = ("—", "–")


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


def check_links(files, root=ROOT):
    """Relative markdown links must resolve to an existing file or directory."""
    failures = []
    for rel in files:
        if not rel.endswith(".md"):
            continue
        with open(os.path.join(root, rel), encoding="utf-8") as handle:
            text = handle.read()
        base = os.path.dirname(os.path.join(root, rel))
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


def main():
    files = tracked_files()
    failures = check_registry() + check_json(files) + check_links(files) + check_prose(files)
    for failure in failures:
        print(failure)
    print(f"{len(files)} tracked files checked, {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
