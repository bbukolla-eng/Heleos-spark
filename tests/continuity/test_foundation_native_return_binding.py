"""Native-return binding contracts against real disposable Git repositories.

Every importer, receipt, transcript digest and Windows path here is synthetic.
These fixtures neither execute native suites nor establish release authority.
"""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/verify-foundation-native-return-binding.py"
IMPORTER = "scripts/import-foundation-native-candidate.ps1"
SUITES = (
    "core-backup-restore", "core-store", "core-backup", "platform-fs",
    "cli-unit", "cli-integration", "workspace-all",
)


class FoundationNativeReturnBindingTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="heleos-synthetic-return-binding-")
        self.addCleanup(scratch.cleanup)
        self.base = Path(scratch.name).resolve()
        self.repo = self.base / "synthetic visible main"
        self.repo.mkdir()
        template = self.base / "empty-git-template"
        template.mkdir()

        # Fixture setup needs Git configuration; the CLI rejects all GIT_*
        # overrides, so its separately constructed environment has none.
        self.command_env = {
            key: value for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        self.command_env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.git_env = dict(self.command_env)
        self.git_env.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Synthetic Fixture",
            "GIT_AUTHOR_EMAIL": "synthetic@example.invalid",
            "GIT_COMMITTER_NAME": "Synthetic Fixture",
            "GIT_COMMITTER_EMAIL": "synthetic@example.invalid",
            "GIT_AUTHOR_DATE": "2026-09-10T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-09-10T00:00:00Z",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
        })
        self.git("init", "--initial-branch=main", "--template=" + str(template))
        self.write(".gitignore", "WINDOWS_NATIVE_HANDOFF_synthetic/\nsynthetic-summary.json\n")
        self.write(IMPORTER, "# Synthetic trusted importer; never executed.\n")
        self.write("scripts/verify-supply-chain.ps1",
                   "# Synthetic native gate; never executed.\n")
        self.git("add", "--", ".gitignore", IMPORTER, "scripts/verify-supply-chain.ps1")
        self.git("commit", "-qm", "synthetic Foundation candidate")
        self.candidate = self.git("rev-parse", "HEAD").strip()
        self.branch = "release/foundation-0.1-native-" + self.candidate
        self.git("branch", self.branch, self.candidate)

        self.handoff = self.repo / "WINDOWS_NATIVE_HANDOFF_synthetic"
        self.bundle_path = self.handoff / "candidate" / (
            "heleos-spark-" + self.candidate + ".bundle")
        self.bundle_path.parent.mkdir(parents=True)
        self.git("bundle", "create", str(self.bundle_path), "refs/heads/" + self.branch)
        self.bundle_path.chmod(0o644)
        self.manifest = {
            "schema": "heleos.foundation-windows-native-candidate/v1",
            "branch": self.branch,
            "commit": self.candidate,
            "bundle": self.bundle_path.name,
            "bundle_sha256": self.sha256(self.bundle_path),
            "bundle_bytes": self.bundle_path.stat().st_size,
            "native_gate": "scripts/verify-supply-chain.ps1",
            "required_filesystem": "NTFS",
            "native_gate_status": "pending",
        }
        self.manifest_path = self.write(
            self.handoff / "candidate/candidate.json", json.dumps(self.manifest) + "\n")
        self.write(self.handoff / "candidate/README.md",
                   "Synthetic Foundation transfer only; no native execution evidence.\n")
        self.write(self.handoff / IMPORTER, (self.repo / IMPORTER).read_text(encoding="utf-8"))
        self.refresh_checksums()

        # This describes a different manifest from candidate/candidate.json.
        self.transcript_manifest_sha256 = hashlib.sha256(
            b"synthetic native-suite-manifest.json; no transcripts executed\n").hexdigest()
        suites = [{
            "id": name,
            "list_sha256": hashlib.sha256(("synthetic list " + name).encode()).hexdigest(),
            "run_sha256": hashlib.sha256(("synthetic run " + name).encode()).hexdigest(),
            "listed": 1,
            "passed": 1,
        } for name in SUITES]
        self.summary = {
            "schema": "heleos.foundation-windows-native-import/v1",
            "status": "PASS",
            "mode": "native_suites",
            "native_host": True,
            "native_evidence": True,
            "commit": self.candidate,
            "destination": r"C:\synthetic-only\foundation",
            "destination_created": True,
            "manifest_sha256": self.sha256(self.manifest_path),
            "bundle_sha256": self.sha256(self.bundle_path),
            "gate_summary": {
                "schema": "heleos.native-suite-receipt/v1",
                "manifest_sha256": self.transcript_manifest_sha256,
                "suites": suites,
                "status": "pass",
                "candidate_sha": self.candidate,
                "platform": "windows-x86_64",
                "filesystem": "NTFS",
                "suite_count": 7,
                "total_listed": 7,
                "total_passed": 7,
            },
            "gate_exit_code": 0,
            "gate_log": r"C:\synthetic-only\foundation\.git\foundation-native-import.log",
            "error_code": None,
            "error": None,
            "events": [{"event": "synthetic fixture; no native execution"}],
        }
        self.summary_path = self.write("synthetic-summary.json", json.dumps(self.summary) + "\n")

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "core.hooksPath=" + os.devnull,
             "-c", "commit.gpgSign=false", "-c", "core.autocrlf=false",
             "-C", str(self.repo), *args],
            env=self.git_env, check=True, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=10,
        ).stdout

    def write(self, path, value):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding="utf-8")
        target.chmod(0o644)
        return target

    @staticmethod
    def sha256(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def refresh_checksums(self):
        paths = ("candidate/candidate.json", "candidate/README.md",
                 "candidate/" + self.bundle_path.name, IMPORTER)
        checksums = "".join(
            self.sha256(self.handoff / path) + "  " + path + "\n" for path in paths)
        self.write(self.handoff / "SHA256SUMS.txt", checksums)

    def run_binding(self, *extra):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "--repo", str(self.repo),
             "--candidate", self.candidate, "--handoff", str(self.handoff),
             "--summary", str(self.summary_path), *extra],
            env=self.command_env, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=60,
        )

    def test_success_binds_actual_outbound_bytes(self):
        completed = self.run_binding()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["candidate_sha"], self.candidate)
        self.assertEqual(result["outbound_manifest_sha256"], self.sha256(self.manifest_path))
        self.assertEqual(result["bundle_sha256"], self.sha256(self.bundle_path))
        self.assertEqual(result["summary_sha256"], self.sha256(self.summary_path))
        self.assertEqual(result["trusted_importer_sha256"], self.sha256(self.repo / IMPORTER))
        self.assertEqual(result["transcript_manifest_sha256"], self.transcript_manifest_sha256)
        self.assertEqual(result["authority"], {
            "independent_native_authentication": False,
            "native_execution_independently_proven": False,
            "ci_authority": False,
            "owner_approval": False,
            "release_approval": False,
        })
        self.assertTrue(all(value is False for value in result["authority"].values()))
        self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()
