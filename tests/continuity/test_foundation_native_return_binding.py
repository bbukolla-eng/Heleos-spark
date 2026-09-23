"""Native-return binding contracts against real disposable Git repositories.

Every importer, receipt, transcript digest and Windows path here is synthetic.
These fixtures neither execute native suites nor establish release authority.
"""

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/verify-foundation-native-return-binding.py"
IMPORTER = "scripts/import-foundation-native-candidate.ps1"
SUITES = (
    "core-backup-restore", "core-store", "core-backup", "platform-fs",
    "cli-unit", "cli-integration", "workspace-all",
)
AUTHORITY = {
    "independent_native_authentication": False,
    "native_execution_independently_proven": False,
    "ci_authority": False,
    "owner_approval": False,
    "release_approval": False,
}
IDENTITIES = (
    "candidate_sha", "outbound_manifest_sha256", "bundle_sha256",
    "trusted_importer_sha256", "transcript_manifest_sha256", "summary_sha256",
)
RESULT_KEYS = set(IDENTITIES) | {"schema", "status", "authority", "error_code", "error"}
ERRORS = {
    "INVALID_ARGUMENT": "Invalid binding arguments.",
    "GIT_OVERRIDE": "Git environment overrides are not accepted.",
    "INVALID_PATH": "Unsafe binding path.",
    "UNSAFE_SOURCE": "Unsafe source file.",
    "INPUT_LIMIT": "Input limit exceeded.",
    "INVALID_JSON": "Invalid summary JSON.",
    "INVALID_SUMMARY": "Invalid Foundation importer summary.",
    "INVALID_REPOSITORY": "Repository inspection failed.",
    "INVALID_CANDIDATE": "Candidate commit unavailable.",
    "INVALID_HANDOFF": "Invalid Foundation outbound handoff.",
    "CANDIDATE_MISMATCH": "Returned candidate does not match the outbound handoff.",
    "MANIFEST_MISMATCH": "Returned outbound manifest digest does not match.",
    "BUNDLE_MISMATCH": "Returned bundle digest does not match.",
    "SOURCE_CHANGED": "Source changed during inspection.",
    "INTERNAL_ERROR": "Binding verification failed.",
}
AUTHORITY_TEXT = (
    " No independent native authentication, execution proof, CI authority, "
    "owner approval, or release approval.\n"
)
SENTINEL = "SYNTHETIC_SECRET_NEVER_PRINT_82d4c"


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

    def binding_args(self, **overrides):
        values = {"repo": self.repo, "candidate": self.candidate,
                  "handoff": self.handoff, "summary": self.summary_path}
        values.update(overrides)
        return [part for key, value in values.items() for part in ("--" + key, str(value))]

    def run_cli(self, args, env=None):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *args],
            env=self.command_env if env is None else env, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=60,
        )

    def run_binding(self, *extra, **overrides):
        return self.run_cli(self.binding_args(**overrides) + list(extra))

    def binding_module(self):
        if not hasattr(self, "_binding"):
            spec = importlib.util.spec_from_file_location("synthetic_return_binding", SCRIPT)
            self._binding = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self._binding)
        return self._binding

    def run_direct(self, **overrides):
        binding = self.binding_module()
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, self.command_env, clear=True):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = binding.main(self.binding_args(**overrides))
        return subprocess.CompletedProcess([], code, stdout.getvalue(), stderr.getvalue())

    def assert_failure(self, completed, code):
        self.assertEqual(completed.returncode, 1, completed.stderr + completed.stdout)
        self.assertEqual(completed.stderr, "")
        result = json.loads(completed.stdout)
        expected = dict.fromkeys(IDENTITIES)
        expected.update({"schema": "heleos.foundation-native-return-binding-result/v1",
                         "status": "FAIL", "authority": AUTHORITY,
                         "error_code": code, "error": ERRORS[code]})
        self.assertEqual(result, expected)
        self.assertTrue(all(value is False for value in result["authority"].values()))
        self.assertEqual(completed.stdout, json.dumps(
            expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")
        self.assertNotIn(SENTINEL, completed.stdout + completed.stderr)
        return result

    def assert_binding_error(self, code, function, *args):
        with self.assertRaises(self.binding_module().BindingError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)

    def save_summary(self):
        self.write(self.summary_path, json.dumps(self.summary) + "\n")

    def save_manifest(self):
        self.write(self.manifest_path, json.dumps(self.manifest) + "\n")
        self.refresh_checksums()

    def make_later_candidate(self):
        self.write("synthetic-later.txt", "Synthetic later candidate.\n")
        self.git("add", "--", "synthetic-later.txt")
        self.git("commit", "-qm", "synthetic later candidate")
        return self.git("rev-parse", "HEAD").strip()

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

    def test_success_result_is_complete_and_deterministic(self):
        first, second = self.run_binding(), self.run_binding()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(first.stderr + second.stderr, "")
        self.assertEqual(first.stdout, second.stdout)
        result = json.loads(first.stdout)
        self.assertEqual(set(result), RESULT_KEYS)
        self.assertEqual(result["schema"], "heleos.foundation-native-return-binding-result/v1")
        self.assertIsNone(result["error"])
        self.assertIsNone(result["error_code"])
        self.assertEqual(first.stdout, json.dumps(
            result, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")

    def test_human_success_and_failure_have_fixed_output(self):
        success = self.run_binding("--human")
        self.assertEqual(success.returncode, 0, success.stdout + success.stderr)
        self.assertEqual(success.stderr, "")
        self.assertEqual(success.stdout,
                         "PASS: Foundation native return matches the outbound handoff." + AUTHORITY_TEXT)
        for args in (["--unknown", SENTINEL], ["--human", "--human"]):
            with self.subTest(args=args):
                failed = self.run_binding("--human", *args)
                self.assertEqual(failed.returncode, 1)
                self.assertEqual(failed.stderr, "")
                self.assertEqual(failed.stdout,
                                 "FAIL [INVALID_ARGUMENT]: Invalid binding arguments." + AUTHORITY_TEXT)

    def test_help_is_fixed_and_does_not_inspect(self):
        first = self.run_cli(["--help"], env=dict(self.command_env, GIT_DIR=SENTINEL))
        second = self.run_cli(["-h"])
        self.assertEqual(first.returncode, 0)
        self.assertEqual(second.returncode, 0)
        self.assertEqual(first.stderr + second.stderr, "")
        self.assertEqual(first.stdout, second.stdout)
        self.assertIn("--repo PATH --candidate FULL_SHA --handoff PATH --summary PATH [--human]", first.stdout)
        self.assertNotIn(str(self.repo), first.stdout)
        self.assertNotIn(SENTINEL, first.stdout)
        binding = self.binding_module()
        with mock.patch.object(binding, "verify", side_effect=AssertionError("inspection forbidden")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(binding.main(["--help"]), 0)
            self.assertEqual(output.getvalue(), first.stdout)

    def test_invalid_arguments_are_closed_and_redacted(self):
        variants = [[], ["--unknown", SENTINEL], ["--repo"], ["--help", SENTINEL],
                    self.binding_args() + ["--human", "--human"]]
        for flag in ("--repo", "--candidate", "--handoff", "--summary"):
            variants.append(self.binding_args() + [flag, SENTINEL])
        for args in variants:
            with self.subTest(args=args):
                completed = self.run_cli(args)
                if "--human" in args:
                    self.assertEqual(completed.returncode, 1)
                    self.assertEqual(completed.stderr, "")
                    self.assertEqual(completed.stdout,
                                     "FAIL [INVALID_ARGUMENT]: Invalid binding arguments." + AUTHORITY_TEXT)
                else:
                    self.assert_failure(completed, "INVALID_ARGUMENT")

    def test_returned_candidate_mismatch_is_distinct(self):
        self.summary["commit"] = "f" * 40
        self.summary["gate_summary"]["candidate_sha"] = "f" * 40
        self.save_summary()
        self.assert_failure(self.run_binding(), "CANDIDATE_MISMATCH")

    def test_selected_candidate_must_match_valid_handoff(self):
        later = self.make_later_candidate()
        self.assert_failure(self.run_binding(candidate=later), "CANDIDATE_MISMATCH")

    def test_validly_rebuilt_handoff_for_another_candidate_fails_binding(self):
        later = self.make_later_candidate()
        later_branch = "release/foundation-0.1-native-" + later
        self.git("branch", later_branch, later)
        self.bundle_path = self.handoff / "candidate" / ("heleos-spark-" + later + ".bundle")
        self.git("bundle", "create", str(self.bundle_path), "refs/heads/" + later_branch)
        self.bundle_path.chmod(0o644)
        self.manifest.update(commit=later, branch=later_branch, bundle=self.bundle_path.name,
                             bundle_sha256=self.sha256(self.bundle_path),
                             bundle_bytes=self.bundle_path.stat().st_size)
        self.save_manifest()
        self.assert_failure(self.run_binding(), "CANDIDATE_MISMATCH")

    def test_missing_or_noncanonical_candidate_is_rejected(self):
        for candidate in ("0" * 40, "a" * 39, "A" * 40, SENTINEL):
            with self.subTest(candidate=candidate):
                self.assert_failure(self.run_binding(candidate=candidate), "INVALID_CANDIDATE")

    def test_outbound_and_bundle_digest_mismatches_are_distinct(self):
        for field, code in (("manifest_sha256", "MANIFEST_MISMATCH"),
                            ("bundle_sha256", "BUNDLE_MISMATCH")):
            with self.subTest(field=field):
                original = self.summary[field]
                self.summary[field] = "0" * 64
                self.save_summary()
                self.assert_failure(self.run_binding(), code)
                self.summary[field] = original

    def test_transcript_digest_never_substitutes_for_outbound_manifest(self):
        self.assertNotEqual(self.summary["manifest_sha256"], self.transcript_manifest_sha256)
        self.summary["manifest_sha256"] = self.transcript_manifest_sha256
        self.save_summary()
        self.assert_failure(self.run_binding(), "MANIFEST_MISMATCH")

    def test_consistently_rebuilt_manifest_still_requires_new_return_binding(self):
        self.write(self.manifest_path, json.dumps(self.manifest, indent=2) + "\n")
        self.refresh_checksums()
        self.assert_failure(self.run_binding(), "MANIFEST_MISMATCH")

    def test_older_candidate_remains_valid_after_main_moves(self):
        self.assertNotEqual(self.make_later_candidate(), self.candidate)
        completed = self.run_binding()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["candidate_sha"], self.candidate)

    def test_sibling_worktree_resolves_registered_main(self):
        sibling = self.base / "synthetic sibling"
        self.git("worktree", "add", "-b", "synthetic-worker", str(sibling), self.candidate)
        completed = self.run_binding(repo=sibling)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_missing_registered_main_fails(self):
        self.git("branch", "-m", "synthetic-not-main")
        self.assert_failure(self.run_binding(), "INVALID_REPOSITORY")

    def test_handoff_corruption_is_rejected(self):
        paths = (self.manifest_path, self.bundle_path,
                 self.handoff / "candidate/README.md", self.handoff / "SHA256SUMS.txt",
                 self.handoff / IMPORTER, self.repo / IMPORTER)
        for path in paths:
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_bytes(original + b"synthetic corruption\n")
                self.assert_failure(self.run_binding(), "INVALID_HANDOFF")
                path.write_bytes(original)

    def test_checksum_order_spacing_and_final_newline_are_exact(self):
        path = self.handoff / "SHA256SUMS.txt"
        original = path.read_bytes()
        for data in (b"".join(reversed(original.splitlines(keepends=True))),
                     original.replace(b"  ", b" "), original.rstrip(b"\n")):
            with self.subTest(data=data[:30]):
                path.write_bytes(data)
                self.assert_failure(self.run_binding(), "INVALID_HANDOFF")

    def test_manifest_fields_are_closed_and_foundation_specific(self):
        original = copy.deepcopy(self.manifest)
        variants = [("schema", "heleos.worker-candidate/v1"), ("bundle_bytes", True),
                    ("branch", "synthetic-wrong-branch"), ("native_gate_status", "pass"),
                    ("required_filesystem", "APFS"), ("bundle", "../synthetic.bundle")]
        for key, value in variants + [("extra", "synthetic")]:
            with self.subTest(key=key):
                self.manifest = dict(original, **{key: value})
                self.save_manifest()
                self.assert_failure(self.run_binding(), "INVALID_HANDOFF")
        self.manifest = dict(original)
        del self.manifest["native_gate"]
        self.save_manifest()
        self.assert_failure(self.run_binding(), "INVALID_HANDOFF")

    def test_wrong_advertised_bundle_branch_is_rejected_even_with_valid_hashes(self):
        self.git("branch", "synthetic-wrong-ref", self.candidate)
        self.bundle_path.unlink()
        self.git("bundle", "create", str(self.bundle_path), "refs/heads/synthetic-wrong-ref")
        self.bundle_path.chmod(0o644)
        self.manifest.update(bundle_sha256=self.sha256(self.bundle_path),
                             bundle_bytes=self.bundle_path.stat().st_size)
        self.save_manifest()
        self.assert_failure(self.run_binding(), "INVALID_HANDOFF")

    def test_invalid_bundle_is_rejected_even_with_consistent_hashes(self):
        self.bundle_path.write_bytes(b"Synthetic invalid bundle\n")
        self.manifest.update(bundle_sha256=self.sha256(self.bundle_path),
                             bundle_bytes=self.bundle_path.stat().st_size)
        self.save_manifest()
        self.assert_failure(self.run_binding(), "INVALID_HANDOFF")

    def test_nonignored_handoff_fails(self):
        self.write(".gitignore", "synthetic-summary.json\n")
        self.assert_failure(self.run_binding(), "INVALID_HANDOFF")

    def test_tracked_handoff_fails(self):
        self.git("add", "-f", "--", str(self.manifest_path))
        self.assert_failure(self.run_binding(), "INVALID_HANDOFF")

    def test_summary_root_closed_fields_and_types(self):
        binding = self.binding_module()
        for key in self.summary:
            with self.subTest(missing=key):
                value = copy.deepcopy(self.summary)
                del value[key]
                self.assert_binding_error("INVALID_SUMMARY", binding.validate_summary, value)
        variants = {
            "schema": ["heleos.worker-windows-import/v1", None],
            "status": ["FAIL", "pass"], "mode": ["preflight", "worker"],
            "native_host": [False, 1, "true"], "native_evidence": [False, 1, "true"],
            "destination_created": [False, 1, "true"],
            "destination": ["", None, 1], "gate_log": ["", None, 1],
            "gate_exit_code": [True, False, 1, 0.0, "0"],
            "error_code": ["synthetic error", 0], "error": ["synthetic error", False],
            "commit": ["a" * 39, "A" * 40, None],
            "manifest_sha256": ["A" * 64, "a" * 63, None],
            "bundle_sha256": ["a" * 65, "z" * 64, 0],
            "events": [None, "synthetic event", {}, [None], [1], ["synthetic"]],
            "extra": ["synthetic field"],
        }
        for key, alternatives in variants.items():
            for alternative in alternatives:
                with self.subTest(key=key, value=alternative):
                    value = copy.deepcopy(self.summary)
                    value[key] = alternative
                    self.assert_binding_error("INVALID_SUMMARY", binding.validate_summary, value)

    def test_receipt_closed_fields_and_types(self):
        binding = self.binding_module()
        receipt = self.summary["gate_summary"]
        for key in receipt:
            with self.subTest(missing=key):
                value = copy.deepcopy(self.summary)
                del value["gate_summary"][key]
                self.assert_binding_error("INVALID_SUMMARY", binding.validate_summary, value)
        variants = {
            "schema": ["synthetic wrong schema"], "status": ["PASS", "fail"],
            "manifest_sha256": ["A" * 64, "a" * 63, None],
            "candidate_sha": ["f" * 40, None], "platform": ["linux-x86_64", None],
            "filesystem": ["APFS", "ntfs"], "suite_count": [True, 7.0, "7", 6, 8],
            "total_listed": [True, 7.0, "7", 6, 8],
            "total_passed": [True, 7.0, "7", 6, 8],
            "suites": [None, {}, [], receipt["suites"][:-1], receipt["suites"] * 2,
                       list(reversed(receipt["suites"])), [receipt["suites"][0]] * 7],
            "extra": ["synthetic field"],
        }
        for key, alternatives in variants.items():
            for alternative in alternatives:
                with self.subTest(key=key, value=alternative):
                    value = copy.deepcopy(self.summary)
                    value["gate_summary"][key] = alternative
                    self.assert_binding_error("INVALID_SUMMARY", binding.validate_summary, value)

    def test_each_suite_has_closed_fields_hashes_and_exact_integer_counts(self):
        binding = self.binding_module()
        for index in range(7):
            for key in self.summary["gate_summary"]["suites"][index]:
                with self.subTest(index=index, missing=key):
                    value = copy.deepcopy(self.summary)
                    del value["gate_summary"]["suites"][index][key]
                    self.assert_binding_error("INVALID_SUMMARY", binding.validate_summary, value)
        variants = {"id": ["synthetic wrong suite"], "extra": [True],
                    "list_sha256": ["A" * 64, None], "run_sha256": ["g" * 64, "a" * 63],
                    "listed": [True, 1.0, "1", 0, -1, 2, 1000001],
                    "passed": [True, 1.0, "1", 0, -1, 2, 1000001]}
        for key, alternatives in variants.items():
            for alternative in alternatives:
                with self.subTest(key=key, value=alternative):
                    value = copy.deepcopy(self.summary)
                    value["gate_summary"]["suites"][0][key] = alternative
                    self.assert_binding_error("INVALID_SUMMARY", binding.validate_summary, value)
        value = copy.deepcopy(self.summary)
        for suite in value["gate_summary"]["suites"]:
            suite.update(listed=1000000, passed=1000000)
        value["gate_summary"].update(total_listed=7000000, total_passed=7000000)
        self.assertEqual(binding.validate_summary(value), value)

    def test_representative_summary_errors_use_cli_protocol(self):
        for key, value in (("schema", "heleos.worker-windows-import/v1"),
                           ("gate_exit_code", False), ("events", [SENTINEL]),
                           ("native_evidence", False), ("gate_summary", None)):
            with self.subTest(key=key):
                original = self.summary[key]
                self.summary[key] = value
                self.save_summary()
                self.assert_failure(self.run_binding(), "INVALID_SUMMARY")
                self.summary[key] = original

    def test_malformed_json_uses_cli_protocol(self):
        original = json.dumps(self.summary).encode()
        variants = [b"\xff", b"\xef\xbb\xbf" + original, original + b"{}",
                    b'{"events":[],"events":[]}',
                    original.replace(b'"listed": 1', b'"listed": 1,"listed": 1', 1),
                    original.replace(b'"listed": 1', b'"listed": NaN', 1),
                    original.replace(b'"listed": 1', b'"listed": Infinity', 1),
                    original.replace(b'"listed": 1', b'"listed": -Infinity', 1),
                    ("{invalid " + SENTINEL).encode(), b'"\\ud800"']
        for index, data in enumerate(variants):
            with self.subTest(index=index):
                self.summary_path.write_bytes(data)
                self.assert_failure(self.run_binding(), "INVALID_JSON")

    def test_strict_json_depth_value_and_integer_budgets(self):
        parser = self.binding_module().strict_json
        # Explicit literal boundaries come from the locked contract.
        for data in (b"[" * 33 + b"0" + b"]" * 33,
                     b"[" + b"0," * 99999 + b"0]",
                     b"10000000000000000", b"9007199254740992", b"-9007199254740992"):
            with self.subTest(length=len(data), prefix=data[:40]):
                self.assert_binding_error("INPUT_LIMIT", parser, data)
        self.assertEqual(parser(b"9007199254740991"), 9007199254740991)
        # The 16-character token ceiling independently excludes a signed
        # 16-digit magnitude, despite the inclusive numeric safe range.
        self.assertEqual(parser(b"-999999999999999"), -999999999999999)
        self.assert_binding_error("INPUT_LIMIT", parser, b"-9007199254740991")
        self.assertEqual(parser(b"[" * 32 + b"0" + b"]" * 32),
                         json.loads(b"[" * 32 + b"0" + b"]" * 32))
        self.assertEqual(len(parser(b"[" + b"0," * 99998 + b"0]")), 99999)
        for data in (b"1e999", b"NaN", b'[{"x":{"y":1,"y":2}}]',
                     b'{"\\ud800":1}', b"[] []"):
            with self.subTest(data=data):
                self.assert_binding_error("INVALID_JSON", parser, data)

    def test_integer_budget_violation_uses_cli_protocol(self):
        self.summary["events"] = [{"synthetic_number": 9007199254740992}]
        self.save_summary()
        self.assert_failure(self.run_binding(), "INPUT_LIMIT")

    def test_opaque_unicode_windows_paths_and_finite_event_numbers_pass(self):
        self.summary["destination"] = r"Z:\synthetic-only\路径" + "\\" + SENTINEL
        self.summary["gate_log"] = SENTINEL
        self.summary["events"] = [{"message": SENTINEL + " café", "number": 1.25}]
        self.summary_path.write_bytes((json.dumps(self.summary, ensure_ascii=False) + "\r\n").encode())
        for extra in ((), ("--human",)):
            with self.subTest(extra=extra):
                completed = self.run_binding(*extra)
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                self.assertEqual(completed.stderr, "")
                self.assertNotIn(SENTINEL, completed.stdout)
                self.assertNotIn("路径", completed.stdout)

    def test_traversal_relative_alias_and_symlink_paths_are_rejected(self):
        for path in (str(self.summary_path.parent) + "/../" + self.repo.name + "/" + self.summary_path.name,
                     str(self.summary_path.parent) + "/./" + self.summary_path.name,
                     str(self.summary_path.parent) + "//" + self.summary_path.name,
                     "synthetic-summary.json"):
            with self.subTest(path=path):
                self.assert_failure(self.run_binding(summary=path), "INVALID_PATH")
        link = self.base / "synthetic-summary-link"
        link.symlink_to(self.summary_path)
        self.assert_failure(self.run_binding(summary=link), "INVALID_PATH")
        parent = self.base / "synthetic-parent-link"
        parent.symlink_to(self.repo, target_is_directory=True)
        self.assert_failure(self.run_binding(summary=parent / self.summary_path.name), "INVALID_PATH")

    def test_handoff_source_symlink_fails(self):
        path = self.handoff / "candidate/README.md"
        original = self.base / "synthetic-original-readme"
        path.rename(original)
        path.symlink_to(original)
        self.assert_failure(self.run_binding(), "INVALID_PATH")

    def test_hardlinks_and_group_world_writable_sources_fail(self):
        for source in (self.summary_path, self.handoff / "candidate/README.md"):
            with self.subTest(source=source.name, kind="hardlink"):
                link = self.base / "synthetic-hardlink"
                os.link(source, link)
                self.assert_failure(self.run_binding(), "UNSAFE_SOURCE")
                link.unlink()
            for mode in (0o664, 0o646):
                with self.subTest(source=source.name, mode=oct(mode)):
                    source.chmod(mode)
                    self.assert_failure(self.run_binding(), "UNSAFE_SOURCE")
                    source.chmod(0o644)

    def test_readonly_and_executable_regular_sources_are_allowed(self):
        for mode in (0o444, 0o755):
            with self.subTest(mode=oct(mode)):
                self.summary_path.chmod(mode)
                completed = self.run_binding()
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_directory_and_fifo_are_rejected_without_blocking(self):
        directory = self.base / "synthetic-directory-summary"
        directory.mkdir()
        self.assert_failure(self.run_binding(summary=directory), "UNSAFE_SOURCE")
        if hasattr(os, "mkfifo"):
            fifo = self.base / "synthetic-fifo-summary"
            os.mkfifo(fifo, 0o600)
            self.assert_failure(self.run_binding(summary=fifo), "UNSAFE_SOURCE")

    def test_summary_and_bundle_size_limits_precede_allocation(self):
        binding = self.binding_module()
        with mock.patch.object(binding, "SUMMARY_MAX_BYTES", self.summary_path.stat().st_size - 1):
            self.assert_failure(self.run_direct(), "INPUT_LIMIT")
        with mock.patch.object(binding, "BUNDLE_MAX_BYTES", self.bundle_path.stat().st_size - 1):
            self.assert_failure(self.run_direct(), "INPUT_LIMIT")
            # Keep the declaration below the cap to independently exercise
            # the actual file-size check before any bundle streaming.
            self.manifest["bundle_bytes"] = 1
            self.save_manifest()
            self.assert_failure(self.run_direct(), "INPUT_LIMIT")
        self.manifest["bundle_bytes"] = 512 * 1024 * 1024 + 1
        self.save_manifest()
        self.assert_failure(self.run_binding(), "INPUT_LIMIT")

    def test_metadata_size_limit_is_enforced(self):
        self.write(self.handoff / "candidate/README.md", "s" * (64 * 1024 + 1))
        self.assert_failure(self.run_binding(), "INPUT_LIMIT")

    def test_descriptor_reads_enforce_cap_during_growth(self):
        binding = self.binding_module()
        path = self.write("synthetic-small-source", "tiny")
        resources = []
        source = binding.open_source(str(path), 4, resources)
        try:
            # Inject extra bytes at the read boundary without allocating a huge
            # source or relying on a concurrent writer's scheduling.
            reads = []
            def growing_read(fd, count):
                self.assertEqual(fd, source.fd)
                reads.append(count)
                return b"x" * count
            with mock.patch.object(binding.os, "read", side_effect=growing_read):
                self.assert_binding_error("INPUT_LIMIT", source.consume)
            self.assertEqual(sum(reads), 5)
            self.assertTrue(all(0 < count <= 1024 * 1024 for count in reads))
        finally:
            source.close()
            for resource in reversed(resources):
                resource.close()

    def test_git_environment_overrides_are_rejected_without_leaks(self):
        for key in ("GIT_DIR", "GIT_CONFIG_COUNT", "GIT_OPTIONAL_LOCKS", "GIT_CONFIG_GLOBAL"):
            with self.subTest(key=key):
                completed = self.run_cli(self.binding_args(), dict(self.command_env, **{key: SENTINEL}))
                self.assert_failure(completed, "GIT_OVERRIDE")

    def assert_drift_detected(self, mutate):
        binding = self.binding_module()
        original = binding.recheck_sources
        invoked = []
        def change_then_recheck(sources):
            if not invoked:
                invoked.append(True)
                mutate()
            return original(sources)
        with mock.patch.object(binding, "recheck_sources", side_effect=change_then_recheck):
            self.assert_failure(self.run_direct(), "SOURCE_CHANGED")
        self.assertEqual(invoked, [True])

    def test_same_size_summary_replacement_is_observed_as_drift(self):
        def replace_summary():
            replacement = self.base / "synthetic-replacement-summary"
            data = self.summary_path.read_bytes()
            replacement.write_bytes(data.replace(b"synthetic", b"sYnthetic", 1))
            replacement.chmod(0o644)
            replacement.replace(self.summary_path)
        self.assert_drift_detected(replace_summary)

    def test_each_consumed_handoff_file_and_trusted_importer_is_rechecked(self):
        paths = (self.manifest_path, self.bundle_path,
                 self.handoff / "candidate/README.md", self.handoff / "SHA256SUMS.txt",
                 self.handoff / IMPORTER, self.repo / IMPORTER)
        for path in paths:
            with self.subTest(path=str(path.relative_to(self.repo))):
                original = path.read_bytes()
                def mutate(path=path, original=original):
                    changed = bytes([original[0] ^ 1]) + original[1:]
                    path.write_bytes(changed)
                self.assert_drift_detected(mutate)
                path.write_bytes(original)

    def test_handoff_parent_replacement_is_observed_as_drift(self):
        def replace_parent():
            displaced = self.repo / "synthetic-displaced-handoff"
            self.handoff.rename(displaced)
            self.handoff.mkdir()
        self.assert_drift_detected(replace_parent)

    def test_main_head_movement_is_observed_as_drift(self):
        self.assert_drift_detected(self.make_later_candidate)

    def test_worktree_registration_change_is_observed_as_drift(self):
        def add_registration():
            self.git("worktree", "add", "--detach", str(self.base / "synthetic-new-worktree"), self.candidate)
        self.assert_drift_detected(add_registration)

    def snapshot_repository(self):
        files = {}
        for path in self.repo.rglob("*"):
            info = path.lstat()
            relative = str(path.relative_to(self.repo))
            if path.is_file():
                files[relative] = (self.sha256(path), info.st_mode, info.st_nlink)
            elif path.is_symlink():
                files[relative] = (os.readlink(path), info.st_mode, info.st_nlink)
            else:
                files[relative] = (None, info.st_mode, info.st_nlink)
        return files

    def test_success_and_failure_preserve_repository_and_evidence(self):
        before = self.snapshot_repository()
        caches_before = set((ROOT / "scripts").rglob("__pycache__"))
        completed = self.run_binding()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(self.snapshot_repository(), before)
        self.assert_failure(self.run_binding(candidate="0" * 40), "INVALID_CANDIDATE")
        self.assertEqual(self.snapshot_repository(), before)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertEqual(set((ROOT / "scripts").rglob("__pycache__")), caches_before)
        self.assertEqual(list(self.repo.rglob("__pycache__")), [])

    def test_verifier_subprocesses_are_only_bounded_readonly_git(self):
        binding = self.binding_module()
        actual_popen = subprocess.Popen
        recorded = []
        allowed = {"rev-parse", "symbolic-ref", "rev-list", "status", "worktree",
                   "check-ignore", "ls-files", "ls-tree", "cat-file", "bundle"}
        def inspect_spawn(argv, **kwargs):
            self.assertEqual(argv[0], "git")
            command = argv[argv.index("-C") + 2:]
            self.assertIn(command[0], allowed)
            if command[0] == "worktree":
                self.assertEqual(command, ["worktree", "list", "--porcelain", "-z"])
            if command[0] == "bundle":
                self.assertIn(command[1], ("list-heads", "verify"))
            self.assertIs(kwargs["shell"], False)
            self.assertIs(kwargs["start_new_session"], True)
            self.assertEqual(kwargs["env"]["GIT_ALLOW_PROTOCOL"], "")
            for key, value in (("GIT_OPTIONAL_LOCKS", "0"), ("GIT_NO_LAZY_FETCH", "1"),
                               ("GIT_NO_REPLACE_OBJECTS", "1"), ("GIT_TERMINAL_PROMPT", "0"),
                               ("GIT_CONFIG_GLOBAL", os.devnull), ("GIT_CONFIG_SYSTEM", os.devnull)):
                self.assertEqual(kwargs["env"][key], value)
            self.assertIn("core.hooksPath=" + os.devnull, argv)
            self.assertIn("core.fsmonitor=false", argv)
            self.assertIn("protocol.allow=never", argv)
            recorded.append(command)
            return actual_popen(argv, **kwargs)
        with mock.patch.object(binding.subprocess, "Popen", side_effect=inspect_spawn):
            completed = self.run_direct()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertTrue(recorded)
        self.assertTrue(any(command[:2] == ["bundle", "verify"] for command in recorded))
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_git_failures_do_not_echo_output_or_exception_text(self):
        binding = self.binding_module()
        actual_popen = subprocess.Popen
        def failing_process(argv, **kwargs):
            return actual_popen([sys.executable, "-B", "-c",
                                 "import sys; sys.stdout.write(" + repr(SENTINEL) + "); "
                                 "sys.stderr.write(" + repr(SENTINEL) + "); sys.exit(7)"], **kwargs)
        with mock.patch.object(binding.subprocess, "Popen", side_effect=failing_process):
            self.assert_failure(self.run_direct(), "INVALID_REPOSITORY")
        with mock.patch.object(binding.subprocess, "Popen", side_effect=OSError(SENTINEL)):
            self.assert_failure(self.run_direct(), "INVALID_REPOSITORY")
        with mock.patch.object(binding, "verify", side_effect=RuntimeError(SENTINEL)):
            self.assert_failure(self.run_direct(), "INTERNAL_ERROR")

    def test_git_pipe_caps_are_enforced_while_draining(self):
        binding = self.binding_module()
        actual_popen = subprocess.Popen
        for pipe in ("stdout", "stderr"):
            with self.subTest(pipe=pipe):
                children = []
                def noisy_process(argv, **kwargs):
                    child = actual_popen([sys.executable, "-B", "-c",
                                          "import sys; sys." + pipe + ".write('s' * 257)"], **kwargs)
                    children.append(child)
                    return child
                with mock.patch.object(binding, "GIT_OUTPUT_MAX_BYTES", 256):
                    with mock.patch.object(binding.subprocess, "Popen", side_effect=noisy_process):
                        self.assert_failure(self.run_direct(), "INPUT_LIMIT")
                self.assertTrue(children)
                self.assertTrue(all(child.poll() is not None for child in children))
                self.assertTrue(all(child.stdout.closed and child.stderr.closed for child in children))

    def test_git_timeout_terminates_and_reaps_child(self):
        binding = self.binding_module()
        actual_popen = subprocess.Popen
        children = []
        def blocked_process(argv, **kwargs):
            child = actual_popen([sys.executable, "-B", "-c",
                                  "import signal; signal.pause()"], **kwargs)
            children.append(child)
            return child
        with mock.patch.object(binding, "GIT_TIMEOUT_SECONDS", 0.1):
            with mock.patch.object(binding.subprocess, "Popen", side_effect=blocked_process):
                self.assert_failure(self.run_direct(), "INVALID_REPOSITORY")
        self.assertTrue(children)
        self.assertTrue(all(child.poll() is not None for child in children))
        self.assertTrue(all(child.stdout.closed and child.stderr.closed for child in children))

    def test_git_adapter_rejects_mutating_commands_before_spawning(self):
        binding = self.binding_module()
        forbidden = [("fetch",), ("checkout", "main"), ("worktree", "add", "synthetic"),
                     ("bundle", "create", "synthetic.bundle"), ("symbolic-ref", "HEAD", "refs/heads/other")]
        with mock.patch.object(binding.subprocess, "Popen", side_effect=AssertionError("spawn forbidden")):
            for args in forbidden:
                with self.subTest(args=args):
                    self.assert_binding_error("INTERNAL_ERROR", binding.git, self.repo, *args)


if __name__ == "__main__":
    unittest.main()
