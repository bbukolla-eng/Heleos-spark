"""Black-box export contracts. Synthetic fixtures never prove native execution."""

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/export-foundation-native-evidence.py"
MATRIX = [
    ("core-backup-restore", ["-p", "heleos-core", "--test", "backup_restore"]),
    ("core-store", ["-p", "heleos-core", "--lib", "store::tests"]),
    ("core-backup", ["-p", "heleos-core", "--lib", "backup::tests"]),
    ("platform-fs", ["-p", "heleos-platform-fs", "--lib"]),
    ("cli-unit", ["-p", "heleos-cli", "--bin", "heleos"]),
    ("cli-integration", ["-p", "heleos-cli", "--test", "cli"]),
    ("workspace-all", ["--workspace", "--all-targets", "--all-features"]),
]
ERRORS = {
    "UNSUPPORTED_PLATFORM": "POSIX controller required.",
    "INVALID_ARGUMENT": "Invalid export arguments.",
    "INVALID_PATH": "Unsafe export path.",
    "INVALID_CANDIDATE": "Candidate commit unavailable.",
    "UNSAFE_SOURCE": "Unsafe source file.",
    "INPUT_LIMIT": "Input limit exceeded.",
    "INVALID_JSON": "Invalid input JSON.",
    "CONTRACT_MISMATCH": "Evidence contract mismatch.",
    "HASH_MISMATCH": "Evidence hash mismatch.",
    "SOURCE_CHANGED": "Source changed during export.",
    "OUTPUT_EXISTS": "Output already exists.",
    "WRITE_FAILED": "Archive write failed.",
    "PUBLISH_UNCERTAIN": "Archive publication requires inspection.",
    "INTERNAL_ERROR": "Export failed.",
}
REPORT_KEYS = {"schema", "status", "candidate_sha", "archive_sha256", "archive_bytes",
               "member_count", "output_created", "error_code", "error"}
AUTHORITY = {
    "ci_authority": False, "independent_native_authentication": False,
    "native_execution_independently_proven": False, "owner_approval": False,
    "release_approval": False,
}
CANARY = "SYNTHETIC-SECRET-CANARY-never-log-72941"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


@unittest.skipUnless(os.name == "posix", "Exporter success contracts require POSIX")
class FoundationNativeEvidenceExportTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="heleos-export-contract-")
        self.addCleanup(scratch.cleanup)
        self.base = Path(scratch.name).resolve()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("GIT_")}
        self.env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                         "GIT_AUTHOR_NAME": "Synthetic Test",
                         "GIT_AUTHOR_EMAIL": "test@example.invalid",
                         "GIT_COMMITTER_NAME": "Synthetic Test",
                         "GIT_COMMITTER_EMAIL": "test@example.invalid",
                         "GIT_AUTHOR_DATE": "2026-09-09T00:00:00Z",
                         "GIT_COMMITTER_DATE": "2026-09-09T00:00:00Z"})
        self.repo = self.base / "synthetic repo"
        self.repo.mkdir(mode=0o700)
        self.git("init", "--initial-branch=main")
        self.git("-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "Synthetic candidate")
        self.candidate = self.git("rev-parse", "HEAD").strip()
        self.fixture_counter = 0

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], env=self.env,
                              capture_output=True, text=True, check=True).stdout

    def make_fixture(self):
        self.fixture_counter += 1
        root = self.base / ("copied synthetic evidence %d é" % self.fixture_counter)
        transcripts = root / "transcripts"
        transcripts.mkdir(parents=True, mode=0o700)
        output_dir = root / "private output"
        output_dir.mkdir(mode=0o700)
        fixture = {"repo": self.repo, "candidate": self.candidate,
                   "summary": root / "importer-summary.json",
                   "manifest": transcripts / "manifest.json", "receipt": root / "receipt.json",
                   "output": output_dir / "return.tar"}
        suites, records = [], []
        for index, (suite_id, selectors) in enumerate(MATRIX):
            test_name = "synthetic::" + suite_id.replace("-", "_")
            if suite_id == "core-store":
                test_name = "store::tests::windows_checked_close_releases_handles_in_three_real_rename_phases"
            list_bytes = (test_name + ": test\n\n1 test, 0 benchmarks\n").encode("ascii")
            run_bytes = ("\nrunning 1 test\ntest " + test_name + " ... ok\n\n"
                         "test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; "
                         "0 filtered out; finished in 0.01s\n").encode("ascii")
            if index == 0:
                run_bytes = run_bytes.replace(b"\n", b"\r\n")
            for kind, payload in (("list", list_bytes), ("run", run_bytes)):
                (transcripts / (suite_id + "." + kind + ".txt")).write_bytes(payload)
            run_argv = ["cargo", "+1.96.1", "test", "--frozen"] + selectors
            suites.append({"id": suite_id, "run_argv": run_argv,
                           "list_argv": run_argv + ["--", "--list"],
                           "list_exit_code": 0, "run_exit_code": 0,
                           "list_path": suite_id + ".list.txt", "run_path": suite_id + ".run.txt"})
            records.append({"id": suite_id, "list_sha256": digest(list_bytes),
                            "run_sha256": digest(run_bytes), "listed": 1, "passed": 1})
        manifest = {"schema": "heleos.native-suite-transcripts/v1", "candidate_sha": self.candidate,
                    "platform": "windows-x86_64", "filesystem": "NTFS", "suites": suites}
        fixture["manifest"].write_bytes(json.dumps(manifest, indent=2).replace("\n", "\r\n").encode() + b"\r\n")
        receipt = {"schema": "heleos.native-suite-receipt/v1", "status": "pass",
                   "candidate_sha": self.candidate, "platform": "windows-x86_64", "filesystem": "NTFS",
                   "manifest_sha256": digest(fixture["manifest"].read_bytes()), "suite_count": 7,
                   "total_listed": 7, "total_passed": 7, "suites": records}
        fixture["receipt"].write_bytes(canonical(receipt))
        summary = {"schema": "heleos.foundation-windows-native-import/v1", "status": "PASS",
                   "mode": "native_suites", "native_host": True, "native_evidence": True,
                   "commit": self.candidate, "destination": "C:\\synthetic\\candidate",
                   "destination_created": True, "manifest_sha256": "a" * 64,
                   "bundle_sha256": "b" * 64, "gate_summary": receipt, "gate_exit_code": 0,
                   "gate_log": "C:\\synthetic\\gate.log", "error_code": None, "error": None,
                   "events": [{"event": "synthetic fixture only", "opaque": {"value": [True, 0, None]}}]}
        fixture["summary"].write_bytes(json.dumps(summary, indent=2).replace("\n", "\r\n").encode() + b"\r\n")
        for path in self.source_paths(fixture):
            path.chmod(0o644)
        return fixture

    @staticmethod
    def expected_member_names():
        return (["importer-summary.json", "manifest.json", "receipt.json"] +
                ["transcripts/" + suite_id + "." + kind + ".txt"
                 for suite_id, _ in MATRIX for kind in ("list", "run")] + ["inventory.json"])

    def source_paths(self, fixture):
        return ([fixture[key] for key in ("summary", "manifest", "receipt")] +
                [fixture["manifest"].parent / (suite_id + "." + kind + ".txt")
                 for suite_id, _ in MATRIX for kind in ("list", "run")])

    def source_members(self, fixture):
        return [(name, path.read_bytes()) for name, path in
                zip(self.expected_member_names()[:-1], self.source_paths(fixture))]

    @staticmethod
    def arguments(fixture):
        return [item for key in ("repo", "candidate", "summary", "manifest", "receipt", "output")
                for item in ("--" + key, str(fixture[key]))]

    def run_export(self, fixture, extra=(), env=None):
        return subprocess.run([sys.executable, "-B", str(SCRIPT)] + self.arguments(fixture) + list(extra),
                              env=env or self.env, capture_output=True, text=True, timeout=30)

    def test_exports_exact_original_bytes(self):
        fixture = self.make_fixture()
        originals = self.source_members(fixture)
        result = self.run_export(fixture)
        self.assertEqual(result.returncode, 0, result.stderr)
        with tarfile.open(fixture["output"], "r:") as archive:
            self.assertEqual(archive.getnames(), self.expected_member_names())
            for name, original in originals:
                self.assertEqual(archive.extractfile(name).read(), original)
            self.assertEqual(len(archive.getmembers()), 18)


if __name__ == "__main__":
    unittest.main()
