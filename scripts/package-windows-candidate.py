#!/usr/bin/env python3
"""Package a clean pinned commit for offline native Windows verification.

Requires Python 3.9+ and Git. Run with --repo, --branch and a full --commit.
Output is target/windows-native-candidates/<commit>/; temporary verification
repositories are retained under target/. Existing output is never overwritten;
a failure may retain incomplete evidence without a manifest.
candidate.json is written last and denotes successful packaging, not gate PASS.
No network, checkout mutation, installation, push or native test is performed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile


GATE = "scripts/verify-windows-worker-containment.ps1"
PROFILES = {
    "worker-containment": ("heleos.windows-native-candidate/v1", GATE),
    "foundation-supply-chain": (
        "heleos.foundation-windows-native-candidate/v1", "scripts/verify-supply-chain.ps1",
    ),
}


class PackagingError(Exception):
    pass


def git(repo, *args, input_bytes=None, output=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
         "-C", str(repo), *args], input=input_bytes, stdout=output or subprocess.PIPE,
        stderr=subprocess.PIPE, env=env, check=False,
    )
    if result.returncode:
        raise PackagingError("Git command failed: " + " ".join(args[:2]))
    return result.stdout


def assert_source(repo, branch, commit, native_gate=GATE):
    if git(repo, "rev-parse", "--show-toplevel").decode().strip() != str(repo):
        raise PackagingError("--repo must name the checkout root")
    if git(repo, "symbolic-ref", "--quiet", "HEAD").decode().strip() != "refs/heads/" + branch:
        raise PackagingError("checkout branch differs from --branch")
    if git(repo, "rev-parse", "--verify", "HEAD").decode().strip() != commit:
        raise PackagingError("checkout HEAD differs from --commit")
    if git(repo, "status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none"):
        raise PackagingError("checkout has tracked or nonignored untracked changes")
    if git(repo, "rev-parse", "--is-shallow-repository").strip() != b"false":
        raise PackagingError("complete history is required; shallow checkouts are rejected")
    gate = git(repo, "ls-tree", commit, "--", native_gate).decode().strip()
    if not gate.startswith(("100644 blob ", "100755 blob ")):
        raise PackagingError("candidate must contain the regular committed native gate")


def operator_readme(commit, branch, filename, digest, manifest_digest, profile="worker-containment"):
    if profile == "foundation-supply-chain":
        return f"""# Foundation native Windows candidate handoff

Commit: `{commit}`. Branch: `{branch}`.
Bundle SHA-256: `{digest}`.
Manifest SHA-256: `{manifest_digest}`.

Copy all three files to a local fixed NTFS volume. Use PowerShell 7 and a
separately trusted copy of `scripts/import-foundation-native-candidate.ps1`.
Obtain that script and the expected manifest digest from the controller's
handoff; an unopened bundle cannot supply the script that imports itself.
The destination must not already exist. Replace the trusted script path below
with its local absolute path, then run this command from the package directory:

```powershell
pwsh -NoProfile -File 'C:\\trusted-tools\\import-foundation-native-candidate.ps1' -CandidateDirectory (Get-Location).Path -Destination 'C:\\Heleos-spark-foundation-native-gate' -ExpectedManifestSha256 '{manifest_digest}' -ExpectedCommit '{commit}'
```

The importer verifies this package, creates a new checkout, and runs the
committed `scripts/verify-supply-chain.ps1` without extra gate arguments.
Git, pinned Rust 1.96.1, required targets/components, admitted verification
tools, and locked dependencies must already be available offline. This package
supplies complete source history only; it supplies no dependencies or native
Windows results.

Packaging leaves the native gate pending. Native evidence requires a zero-exit
gate, exactly one canonical `heleos.native-suite-receipt/v1` receipt attesting
this commit, `windows-x86_64`, `NTFS`, and all seven suites, plus exactly one
native Windows `SUPPLY_CHAIN_LOCAL_PASS` line. The importer checks these results.
Retain its output and evidence for the controller's acceptance checks.
The manifest hash is an integrity check, not an independent signature: compare
it with the controller's separately recorded identity before transfer.
"""
    # Git permits apostrophes in refs. PowerShell single-quoted literals double them.
    quoted_branch = branch.replace("'", "''")
    return f"""# Native Windows candidate handoff

Commit: `{commit}`. Branch: `{branch}`.
Bundle SHA-256: `{digest}`.
Manifest SHA-256: `{manifest_digest}`.

Copy all three files to a local NTFS volume. In PowerShell 7, from this
directory, run the commands below. The destination must not already exist.
Git, pinned Rust 1.96.1, its required targets/components and locked dependencies
must already be installed and available offline. This package supplies source
history only; it does not supply dependencies or native Windows test results.

Preferred entry point: use a separately trusted copy of the committed
`scripts/import-windows-native-candidate.ps1` on Windows. Obtain that copy and
the expected manifest digest from the controller's handoff; an unopened bundle
cannot supply the script that imports itself. Replace the script path below
with that trusted local path. The importer creates a new checkout and runs
the full native gate when `-Full` is supplied:

```powershell
pwsh -NoProfile -File 'C:\\trusted-tools\\import-windows-native-candidate.ps1' -CandidateDirectory (Get-Location).Path -Destination 'C:\\Heleos-spark-native-gate' -ExpectedManifestSha256 '{manifest_digest}' -ExpectedCommit '{commit}' -Full
```

Manual fallback when the trusted importer is unavailable:

```powershell
$ErrorActionPreference = 'Stop'
$bundle = (Resolve-Path '.\\{filename}').Path
if ((Get-FileHash -Algorithm SHA256 $bundle).Hash.ToLowerInvariant() -cne '{digest}') {{ throw 'Bundle SHA-256 mismatch' }}
$destination = Join-Path (Get-Location) 'Heleos-spark-native-gate'
if (Test-Path $destination) {{ throw 'Destination already exists' }}
git clone --no-checkout -b '{quoted_branch}' $bundle $destination
if ($LASTEXITCODE -ne 0) {{ throw 'Bundle clone failed' }}
git -C $destination bundle verify $bundle
if ($LASTEXITCODE -ne 0) {{ throw 'Bundle verification failed' }}
$candidate = git -C $destination rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $candidate -cne '{commit}') {{ throw 'Candidate commit mismatch' }}
git -C $destination checkout --detach '{commit}'
if ($LASTEXITCODE -ne 0) {{ throw 'Candidate materialization failed' }}
Set-Location $destination
pwsh -NoProfile -File .\\scripts\\verify-windows-worker-containment.ps1 -PreflightOnly
if ($LASTEXITCODE -ne 0) {{ throw 'Native preflight failed' }}
pwsh -NoProfile -File .\\scripts\\verify-windows-worker-containment.ps1
if ($LASTEXITCODE -ne 0) {{ throw 'Native containment gate failed' }}
```

The native gate records the exact SHA and requires a clean checkout on NTFS.
Preflight alone is not acceptance. Only the final native summary with
`status=PASS` and `native_evidence=true` satisfies the pending containment gate.
The manifest hash is an integrity check, not an independent signature: compare
it with the controller's separately recorded bundle identity before transfer.
"""


def package(repo, branch, commit, profile="worker-containment"):
    repo = repo.resolve(strict=True)
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise PackagingError("--commit must be a complete lowercase SHA-1 commit ID")
    schema, native_gate = PROFILES[profile]
    if profile == "foundation-supply-chain" and branch != "release/foundation-0.1-native-" + commit:
        raise PackagingError("Foundation branch must be release/foundation-0.1-native-<exact commit>")
    git(repo, "check-ref-format", "refs/heads/" + branch)
    assert_source(repo, branch, commit, native_gate)
    directory = repo / "target/windows-native-candidates" / commit
    git(repo, "check-ignore", "--quiet", "--no-index", str(directory / "candidate.json"))
    # Reject symlinks/reparse points before creating any output directories.
    for component in (repo / "target", directory.parent, directory):
        if component.exists() or component.is_symlink():
            info = component.lstat()
            if (not stat.S_ISDIR(info.st_mode)
                    or getattr(info, "st_file_attributes", 0) & 0x400):
                raise PackagingError("output ancestors must be regular directories")
    if directory.exists():
        raise PackagingError("candidate output already exists; preserve it")
    directory.mkdir(parents=True, exist_ok=False)
    filename = "heleos-spark-" + commit + ".bundle"
    bundle = directory / filename
    # Fix pack construction independently of source pack layout and reuse.
    # v2 bundle header advertises one ref, with no prerequisites (full history).
    with bundle.open("xb") as stream:
        stream.write(("# v2 git bundle\n" + commit + " refs/heads/" + branch + "\n\n").encode())
        stream.flush()
        git(repo, "pack-objects", "--stdout", "--revs", "--no-reuse-delta",
            "--no-reuse-object", "--window=0", "--threads=1", "--compression=9",
            input_bytes=(commit + "\n").encode(), output=stream)
        stream.flush()
        os.fsync(stream.fileno())
    git(repo, "bundle", "verify", str(bundle))
    heads = git(repo, "bundle", "list-heads", str(bundle)).decode()
    if heads != commit + " refs/heads/" + branch + "\n":
        raise PackagingError("bundle ref does not match the candidate")
    # bundle verify checks the header and prerequisites, not pack integrity.
    # Reconstruct the history in a retained, ignored bare repository, then
    # verify all objects. No source repository object/index/ref is written.
    verification = Path(tempfile.mkdtemp(prefix=".windows-candidate-verify-", dir=repo / "target"))
    git(repo, "clone", "--quiet", "--bare", str(bundle), str(verification))
    git(verification, "fsck", "--strict", "--no-reflogs")
    if git(verification, "rev-parse", "refs/heads/" + branch).decode().strip() != commit:
        raise PackagingError("reconstructed branch differs from the candidate")
    assert_source(repo, branch, commit, native_gate)
    digest = hashlib.sha256()
    with bundle.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    manifest = {
        "schema": schema, "branch": branch,
        "commit": commit, "bundle": filename, "bundle_sha256": digest.hexdigest(),
        "bundle_bytes": bundle.stat().st_size, "native_gate": native_gate,
        "required_filesystem": "NTFS", "native_gate_status": "pending",
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    with (directory / "README.md").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(operator_readme(commit, branch, filename, digest.hexdigest(), manifest_digest, profile))
    with (directory / "candidate.json").open("xb") as stream:
        stream.write(manifest_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    return directory, manifest_digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--profile", choices=PROFILES, default="worker-containment")
    args = parser.parse_args()
    try:
        directory, manifest_digest = package(args.repo, args.branch, args.commit, args.profile)
    except (PackagingError, OSError, UnicodeError) as error:
        print("WINDOWS_CANDIDATE_ERROR: " + str(error), file=sys.stderr)
        return 1
    print(json.dumps({"status": "PACKAGED", "directory": str(directory),
                      "manifest_sha256": manifest_digest, "native_evidence": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
