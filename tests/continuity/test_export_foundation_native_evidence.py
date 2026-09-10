"""Black-box export contracts. Synthetic fixtures never prove native execution."""

import copy
import contextlib
import errno
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


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
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
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

    def documents(self, fixture):
        return {key: json.loads(fixture[key].read_bytes())
                for key in ("manifest", "receipt", "summary")}

    def write_documents(self, fixture, documents, bind_manifest=False, bind_receipt=True):
        fixture["manifest"].write_bytes(canonical(documents["manifest"]))
        if bind_manifest and "manifest_sha256" in documents["receipt"]:
            documents["receipt"]["manifest_sha256"] = digest(fixture["manifest"].read_bytes())
        fixture["receipt"].write_bytes(canonical(documents["receipt"]))
        if bind_receipt and "gate_summary" in documents["summary"]:
            documents["summary"]["gate_summary"] = copy.deepcopy(documents["receipt"])
        fixture["summary"].write_bytes(canonical(documents["summary"]))

    @staticmethod
    def set_value(document, path, value):
        for part in path[:-1]:
            document = document[part]
        document[path[-1]] = value

    def snapshot_sources(self, fixture):
        result = {}
        for path in self.source_paths(fixture):
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode):
                result[path] = (path.read_bytes(), stat.S_IMODE(info.st_mode))
        return result

    def assert_sources_unchanged(self, snapshot):
        for path, (payload, mode) in snapshot.items():
            self.assertEqual(path.read_bytes(), payload, str(path))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)

    def assert_safe_report(self, result, code, created=False):
        self.assertEqual(result.returncode, 1, result)
        self.assertEqual(result.stderr, "")
        expected = dict(schema="heleos.foundation-native-evidence-export-result/v1",
                        status="FAIL", candidate_sha=None, archive_sha256=None,
                        archive_bytes=None, member_count=None, output_created=created,
                        error_code=code, error=ERRORS[code])
        self.assertEqual(json.loads(result.stdout), expected)
        self.assertEqual(result.stdout.encode(), canonical(expected))
        for forbidden in (CANARY, str(self.base), str(self.repo), "Traceback"):
            self.assertNotIn(forbidden, result.stdout + result.stderr)

    def assert_rejected(self, fixture, code, extra=(), runner=None):
        snapshot = self.snapshot_sources(fixture)
        parent = Path(fixture["output"]).parent
        before = set(parent.iterdir()) if parent.is_dir() else None
        result = (runner or self.run_export)(fixture, extra)
        self.assert_safe_report(result, code)
        self.assertFalse(os.path.lexists(fixture["output"]))
        if before is not None:
            self.assertEqual(set(parent.iterdir()), before)
        self.assert_sources_unchanged(snapshot)
        return result

    def assert_success(self, fixture, result):
        self.assertEqual(result.returncode, 0, result)
        self.assertEqual(result.stderr, "")
        payload = fixture["output"].read_bytes()
        expected = dict(schema="heleos.foundation-native-evidence-export-result/v1",
                        status="PASS", candidate_sha=fixture["candidate"],
                        archive_sha256=digest(payload), archive_bytes=len(payload),
                        member_count=18, output_created=True, error_code=None, error=None)
        self.assertEqual(json.loads(result.stdout), expected)
        self.assertEqual(result.stdout.encode(), canonical(expected))
        self.assertEqual(set(json.loads(result.stdout)), REPORT_KEYS)
        info = fixture["output"].stat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(info.st_nlink, 1)
        originals = self.source_members(fixture)
        with tarfile.open(fixture["output"], "r:") as archive:
            self.assertEqual(archive.getnames(), self.expected_member_names())
            inventory_bytes = archive.extractfile("inventory.json").read()
            expected_inventory = {
                "schema": "heleos.foundation-native-evidence-export/v1",
                "candidate_sha": fixture["candidate"], "platform": "windows-x86_64",
                "filesystem": "NTFS", "authority": AUTHORITY,
                "claims": {"inputs_structurally_and_hash_consistent": True},
                "members": [{"name": name, "sha256": digest(data), "size": len(data)}
                            for name, data in originals],
            }
            self.assertEqual(inventory_bytes, canonical(expected_inventory))
            for item, (name, original) in zip(archive.getmembers(), originals + [("inventory.json", inventory_bytes)]):
                self.assertEqual(item.name, name)
                self.assertEqual(archive.extractfile(item).read(), original)
                self.assertEqual(item.size, len(original))
                self.assertEqual((item.mode, item.mtime, item.uid, item.gid), (0o600, 0, 0, 0))
                self.assertEqual((item.uname, item.gname, item.linkname), ("", "", ""))
                self.assertEqual(item.type, tarfile.REGTYPE)
                self.assertEqual(item.pax_headers, {})
                self.assertEqual(payload[item.offset + 257:item.offset + 265], b"ustar\x0000")
        self.assertEqual(set(fixture["output"].parent.iterdir()), {fixture["output"]})
        return payload

    def load_exporter(self):
        spec = importlib.util.spec_from_file_location("native_export_contract_subject", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        # Registration supports ordinary dataclass implementations without inspecting them.
        with mock.patch.dict(sys.modules, {spec.name: module}):
            spec.loader.exec_module(module)
        return module

    def call_main(self, module, fixture, extra=()):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, self.env, clear=True), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = module.main(self.arguments(fixture) + list(extra))
        return subprocess.CompletedProcess([], code, stdout.getvalue(), stderr.getvalue())

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
        self.assert_success(fixture, result)

    def test_deterministic_bytes_and_metadata(self):
        archives = []
        for index, mode in enumerate((0o600, 0o640, 0o644)):
            fixture = self.make_fixture()
            fixture["output"] = fixture["output"].with_name("different-%d.tar" % index)
            for path in self.source_paths(fixture):
                path.chmod(mode)
                os.utime(path, ns=(1000000000 + index, 9000000000 + index))
            snapshot = self.snapshot_sources(fixture)
            archives.append(self.assert_success(fixture, self.run_export(fixture)))
            self.assert_sources_unchanged(snapshot)
        self.assertEqual(archives[0], archives[1])
        self.assertEqual(archives[1], archives[2])

    def test_exact_root_and_suite_key_sets(self):
        fixture = self.make_fixture()
        documents = self.documents(fixture)
        targets = [(kind, ()) for kind in documents] + [(kind, ("suites", 0)) for kind in ("manifest", "receipt")]
        for kind, path in targets:
            target = documents[kind]
            for part in path:
                target = target[part]
            for key in list(target) + ["extra_" + CANARY]:
                with self.subTest(document=kind, path=path, key=key):
                    fixture = self.make_fixture()
                    changed = self.documents(fixture)
                    node = changed[kind]
                    for part in path:
                        node = node[part]
                    if key in node:
                        del node[key]
                    else:
                        node[key] = CANARY
                    self.write_documents(fixture, changed, bind_manifest=True)
                    self.assert_rejected(fixture, "CONTRACT_MISMATCH")

    def test_typed_contract_mutations(self):
        cases = []
        def add(kind, path, values):
            cases.extend((kind, path, value) for value in values)
        for kind in ("manifest", "receipt"):
            add(kind, ("schema",), [CANARY])
            add(kind, ("candidate_sha",), ["f" * 40])
            add(kind, ("platform",), ["linux-x86_64"])
            add(kind, ("filesystem",), ["ext4"])
            add(kind, ("suites", 0, "id"), [MATRIX[1][0], CANARY])
        for key in ("list_exit_code", "run_exit_code"):
            add("manifest", ("suites", 0, key), [1, False, 0.0, "0"])
        for key in ("list_path", "run_path"):
            add("manifest", ("suites", 0, key), ["../" + CANARY, "/" + CANARY])
        add("manifest", ("suites", 0, "run_argv", 1), ["+stable"])
        add("manifest", ("suites", 0, "list_argv", -1), ["--ignored"])
        add("receipt", ("status",), ["PASS", "fail"])
        add("receipt", ("suite_count",), [6, True, 7.0])
        for key in ("total_listed", "total_passed"):
            add("receipt", (key,), [6, True, 7.0])
        for key in ("listed", "passed"):
            add("receipt", ("suites", 0, key), [0, -1, True, 1.0, "1", 2, 1000001])
        for key in ("list_sha256", "run_sha256"):
            add("receipt", ("suites", 0, key), ["A" * 64, "0" * 63, 0])
        for key, values in {
            "schema": [CANARY], "status": ["pass"], "mode": ["bundle"],
            "native_host": [False, 1], "native_evidence": [False, 1],
            "destination_created": [False, 1], "commit": ["f" * 40],
            "destination": ["", None], "gate_log": ["", None],
            "gate_exit_code": [1, False, 0.0], "error_code": [CANARY], "error": [CANARY],
            "manifest_sha256": ["A" * 64, "0" * 63], "bundle_sha256": [None, "z" * 64],
            "events": [{}, [True], [CANARY]],
        }.items():
            add("summary", (key,), values)
        for kind, path, value in cases:
            with self.subTest(document=kind, path=path, value=value):
                fixture = self.make_fixture()
                documents = self.documents(fixture)
                self.set_value(documents[kind], path, value)
                self.write_documents(fixture, documents, bind_manifest=True)
                self.assert_rejected(fixture, "CONTRACT_MISMATCH")

    def test_suite_order_and_cardinality(self):
        for kind in ("manifest", "receipt"):
            for mutation in ("reverse", "missing", "duplicate"):
                with self.subTest(document=kind, mutation=mutation):
                    fixture = self.make_fixture()
                    documents = self.documents(fixture)
                    suites = documents[kind]["suites"]
                    if mutation == "reverse":
                        suites.reverse()
                    elif mutation == "missing":
                        suites.pop()
                    else:
                        suites[1] = copy.deepcopy(suites[0])
                    self.write_documents(fixture, documents, bind_manifest=True)
                    self.assert_rejected(fixture, "CONTRACT_MISMATCH")

    def test_summary_binding_is_typed_and_transfer_digest_is_distinct(self):
        fixture = self.make_fixture()
        documents = self.documents(fixture)
        summary = documents["summary"]
        self.assertNotEqual(summary["manifest_sha256"], summary["gate_summary"]["manifest_sha256"])
        summary["gate_summary"] = dict(reversed(list(summary["gate_summary"].items())))
        fixture["summary"].write_bytes(canonical(dict(reversed(list(summary.items())))))
        self.assert_success(fixture, self.run_export(fixture))
        for path, value in [(("total_listed",), True), (("suites", 0, "listed"), True),
                            (("manifest_sha256",), "c" * 64)]:
            with self.subTest(path=path):
                fixture = self.make_fixture()
                summary = self.documents(fixture)["summary"]
                self.set_value(summary["gate_summary"], path, value)
                fixture["summary"].write_bytes(canonical(summary))
                self.assert_rejected(fixture, "CONTRACT_MISMATCH")

    def test_every_transcript_and_bound_digest_tampering(self):
        for index in range(14):
            with self.subTest(transcript=index):
                fixture = self.make_fixture()
                path = self.source_paths(fixture)[3 + index]
                path.write_bytes(path.read_bytes() + CANARY.encode())
                self.assert_rejected(fixture, "HASH_MISMATCH")
        fixture = self.make_fixture()
        fixture["manifest"].write_bytes(fixture["manifest"].read_bytes() + b" ")
        self.assert_rejected(fixture, "HASH_MISMATCH")
        paths = [("manifest_sha256",)] + [("suites", index, key)
                 for index in range(7) for key in ("list_sha256", "run_sha256")]
        for path in paths:
            with self.subTest(receipt_digest=path):
                fixture = self.make_fixture()
                documents = self.documents(fixture)
                self.set_value(documents["receipt"], path, "c" * 64)
                # Preserve manifest bytes: only the intentionally false digest changes.
                fixture["receipt"].write_bytes(canonical(documents["receipt"]))
                documents["summary"]["gate_summary"] = documents["receipt"]
                fixture["summary"].write_bytes(canonical(documents["summary"]))
                self.assert_rejected(fixture, "HASH_MISMATCH")

    def test_receipt_requires_exact_canonical_bytes(self):
        variations = {
            "pretty": lambda raw: json.dumps(json.loads(raw), indent=2).encode() + b"\n",
            "CRLF": lambda raw: raw[:-1] + b"\r\n",
            "extra LF": lambda raw: raw + b"\n",
            "missing LF": lambda raw: raw[:-1],
            "leading space": lambda raw: b" " + raw,
            "float": lambda raw: raw.replace(b'"suite_count":7', b'"suite_count":7.0'),
            "escaped ASCII": lambda raw: raw.replace(b'"pass"', b'"pa\\u0073s"'),
            "key order": lambda raw: (json.dumps(dict(reversed(list(json.loads(raw).items()))), separators=(",", ":")) + "\n").encode(),
        }
        for label, variation in variations.items():
            with self.subTest(variation=label):
                fixture = self.make_fixture()
                fixture["receipt"].write_bytes(variation(fixture["receipt"].read_bytes()))
                self.assert_rejected(fixture, "CONTRACT_MISMATCH")

    def test_duplicate_keys_rejected_at_all_depths(self):
        cases = [(kind, None) for kind in ("summary", "manifest", "receipt")]
        cases += [("manifest", b'"id"'), ("receipt", b'"id"'),
                  ("summary", b'"event"'), ("summary", b'"value"'),
                  ("summary", b'"total_listed"')]
        for kind, nested_key in cases:
            with self.subTest(document=kind, key=nested_key):
                fixture = self.make_fixture()
                raw = canonical(self.documents(fixture)[kind])
                if nested_key is None:
                    raw = b'{"schema":"' + CANARY.encode() + b'",' + raw[1:]
                else:
                    raw = raw.replace(nested_key + b":", nested_key + b':null,' + nested_key + b":", 1)
                fixture[kind].write_bytes(raw)
                self.assert_rejected(fixture, "INVALID_JSON")

    def test_strict_json_rejections(self):
        variations = {
            "UTF8": lambda raw: b"\xff" + raw,
            "BOM": lambda raw: b"\xef\xbb\xbf" + raw,
            "NaN": lambda raw: b'{"bad":NaN}',
            "Infinity": lambda raw: b'{"bad":Infinity}',
            "negative Infinity": lambda raw: b'{"bad":-Infinity}',
            "surrogate": lambda raw: b'{"bad":"\\ud800"}',
            "trailing": lambda raw: raw + b"{}",
            "malformed": lambda raw: b'{"' + CANARY.encode(),
        }
        for kind in ("summary", "manifest", "receipt"):
            for label, variation in variations.items():
                with self.subTest(document=kind, variation=label):
                    fixture = self.make_fixture()
                    fixture[kind].write_bytes(variation(fixture[kind].read_bytes()))
                    self.assert_rejected(fixture, "INVALID_JSON")

    def test_json_resource_limits(self):
        values = {
            "depth": b"[" * 33 + b"0" + b"]" * 33,
            "values": b"[" + b"0," * 100000 + b"0]",
            "integer token": b'{"n":10000000000000000}',
            "safe integer": b'{"n":9007199254740992}',
        }
        for label, raw in values.items():
            with self.subTest(limit=label):
                fixture = self.make_fixture()
                fixture["summary"].write_bytes(raw)
                self.assert_rejected(fixture, "INPUT_LIMIT")

    def test_input_byte_caps(self):
        for key, cap in (("summary", 16 * 1024 * 1024), ("manifest", 1024 * 1024),
                         ("receipt", 1024 * 1024)):
            with self.subTest(input=key):
                fixture = self.make_fixture()
                # Sparse files exercise stat bounds without allocating large payloads.
                with fixture[key].open("wb") as stream:
                    stream.truncate(cap + 1)
                self.assert_rejected(fixture, "INPUT_LIMIT")
        fixture = self.make_fixture()
        with self.source_paths(fixture)[3].open("wb") as stream:
            stream.truncate(64 * 1024 * 1024 + 1)
        self.assert_rejected(fixture, "INPUT_LIMIT")

    def test_every_missing_transcript_is_rejected(self):
        for index in range(14):
            with self.subTest(transcript=index):
                fixture = self.make_fixture()
                self.source_paths(fixture)[3 + index].unlink()
                self.assert_rejected(fixture, "CONTRACT_MISMATCH")

    def test_unexpected_transcript_shaped_entries(self):
        for name, kind in [("extra.list.txt", "file"), ("extra.run.txt", "file"),
                           ("extra.LIST.TXT", "file"), ("extra.Run.Txt", "file"),
                           (CANARY + ".list.txt", "directory"), ("extra.run.txt", "symlink")]:
            with self.subTest(name=name, kind=kind):
                fixture = self.make_fixture()
                path = fixture["manifest"].parent / name
                if kind == "directory":
                    path.mkdir()
                elif kind == "symlink":
                    path.symlink_to(self.base / CANARY)
                else:
                    path.write_text(CANARY)
                self.assert_rejected(fixture, "CONTRACT_MISMATCH")

    def test_unrelated_entries_are_not_read_or_archived(self):
        fixture = self.make_fixture()
        parent = fixture["manifest"].parent
        (parent / "notes.md").write_text(CANARY)
        (parent / "unrelated").mkdir()
        (parent / "unrelated" / "extra.run.txt").write_text(CANARY)
        (parent / "unrelated-link").symlink_to(self.base / CANARY)
        os.mkfifo(parent / "unrelated-fifo")
        self.assert_success(fixture, self.run_export(fixture))

    def test_directory_entry_limit(self):
        fixture = self.make_fixture()
        parent = fixture["manifest"].parent
        for index in range(4097 - 15):
            (parent / ("note-%d" % index)).touch()
        self.assert_rejected(fixture, "INPUT_LIMIT")

    def test_candidate_validation_and_older_commit(self):
        for candidate in (self.candidate[:12], self.candidate.upper(), "g" * 40,
                          "0" * 40, CANARY, self.git("rev-parse", "HEAD^{tree}").strip()):
            with self.subTest(candidate=candidate):
                fixture = self.make_fixture()
                fixture["candidate"] = candidate
                self.assert_rejected(fixture, "INVALID_CANDIDATE")
        fixture = self.make_fixture()
        self.git("-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "Later controller commit")
        self.assertNotEqual(self.git("rev-parse", "HEAD").strip(), fixture["candidate"])
        self.assert_success(fixture, self.run_export(fixture))

    def test_candidate_type_cannot_be_forged_by_replace_ref(self):
        blob = subprocess.run(
            ["git", "-C", str(self.repo), "hash-object", "-w", "--stdin"],
            env=self.env, input="synthetic blob\n", capture_output=True, text=True,
            check=True,
        ).stdout.strip()
        replace = self.repo / ".git" / "refs" / "replace" / blob
        replace.parent.mkdir(parents=True, exist_ok=True)
        replace.write_text(self.candidate + "\n")
        fixture = self.make_fixture()
        fixture["candidate"] = blob
        documents = self.documents(fixture)
        documents["manifest"]["candidate_sha"] = blob
        documents["receipt"]["candidate_sha"] = blob
        documents["summary"]["commit"] = blob
        self.write_documents(fixture, documents, bind_manifest=True)
        self.assert_rejected(fixture, "INVALID_CANDIDATE")

    def test_invalid_paths(self):
        for key in ("repo", "summary", "manifest", "receipt", "output"):
            for value in ("relative-" + CANARY, "/", str(self.base) + "/../" + CANARY):
                with self.subTest(argument=key, value=value):
                    fixture = self.make_fixture()
                    original_output = fixture["output"]
                    fixture[key] = value
                    result = self.run_export(fixture)
                    self.assert_safe_report(result, "INVALID_PATH")
                    self.assertFalse(original_output.exists())
        for name in ("output.TAR", "output.tar.gz", ".hidden.tar", "output"):
            with self.subTest(output_name=name):
                fixture = self.make_fixture()
                fixture["output"] = fixture["output"].with_name(name)
                self.assert_rejected(fixture, "INVALID_PATH")

    def test_output_parent_hazards(self):
        for kind in ("missing", "writable", "symlink", "transcript-directory"):
            with self.subTest(kind=kind):
                fixture = self.make_fixture()
                if kind == "missing":
                    fixture["output"] = fixture["output"].parent / "missing" / "return.tar"
                elif kind == "writable":
                    fixture["output"].parent.chmod(0o777)
                elif kind == "symlink":
                    alias = self.base / "output-link"
                    alias.symlink_to(fixture["output"].parent, target_is_directory=True)
                    fixture["output"] = alias / "return.tar"
                else:
                    fixture["output"] = fixture["manifest"].parent / "return.tar"
                self.assert_rejected(fixture, "INVALID_PATH")

    def test_source_file_hazards(self):
        for kind in ("symlink", "hardlink", "directory", "fifo", "socket", "group-write", "other-write", "setuid", "setgid", "sticky"):
            with self.subTest(kind=kind):
                fixture = self.make_fixture()
                path = fixture["summary"]
                if kind == "hardlink":
                    os.link(path, path.with_name("source-alias"))
                elif kind in ("group-write", "other-write", "setuid", "setgid", "sticky"):
                    path.chmod({"group-write": 0o620, "other-write": 0o602,
                                "setuid": 0o4600, "setgid": 0o2600, "sticky": 0o1600}[kind])
                else:
                    path.unlink()
                    if kind == "symlink":
                        path.symlink_to(fixture["receipt"])
                    elif kind == "directory":
                        path.mkdir()
                    elif kind == "fifo":
                        os.mkfifo(path)
                    else:
                        server = socket.socket(socket.AF_UNIX)
                        self.addCleanup(server.close)
                        # macOS AF_UNIX has a short pathname cap; use a short scoped path.
                        socket_path = self.base / ("s%d" % self.fixture_counter)
                        server.bind(str(socket_path))
                        socket_path.rename(path)
                self.assert_rejected(fixture, "UNSAFE_SOURCE")

    def test_symlinked_source_ancestor(self):
        fixture = self.make_fixture()
        alias = self.base / "evidence-link"
        alias.symlink_to(fixture["summary"].parent, target_is_directory=True)
        fixture["summary"] = alias / fixture["summary"].name
        self.assert_rejected(fixture, "INVALID_PATH")

    def test_existing_output_is_preserved(self):
        for kind in ("regular", "dangling-symlink", "directory"):
            with self.subTest(kind=kind):
                fixture = self.make_fixture()
                output = fixture["output"]
                if kind == "regular":
                    output.write_bytes(CANARY.encode())
                elif kind == "dangling-symlink":
                    output.symlink_to(self.base / CANARY)
                else:
                    output.mkdir()
                before = output.lstat()
                entries = set(output.parent.iterdir())
                self.assert_safe_report(self.run_export(fixture), "OUTPUT_EXISTS")
                self.assertEqual(output.lstat().st_ino, before.st_ino)
                self.assertEqual(set(output.parent.iterdir()), entries)
                if kind == "regular":
                    self.assertEqual(output.read_bytes(), CANARY.encode())
                elif kind == "dangling-symlink":
                    self.assertEqual(os.readlink(output), str(self.base / CANARY))

    def test_argument_errors_and_help(self):
        fixture = self.make_fixture()
        self.assert_rejected(fixture, "INVALID_ARGUMENT", extra=("--" + CANARY,))
        result = subprocess.run([sys.executable, "-B", str(SCRIPT)], capture_output=True, text=True)
        self.assert_safe_report(result, "INVALID_ARGUMENT")
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--help"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        for flag in ("--repo", "--candidate", "--summary", "--manifest", "--receipt", "--output", "--human"):
            self.assertIn(flag, result.stdout)

    def test_no_clobber_link_race(self):
        fixture = self.make_fixture()
        module = self.load_exporter()
        real_link = os.link
        def competing_link(*args, **kwargs):
            fixture["output"].write_bytes(CANARY.encode())
            return real_link(*args, **kwargs)
        with mock.patch.object(module.os, "link", side_effect=competing_link), mock.patch.object(module.os, "replace", side_effect=AssertionError(CANARY)):
            result = self.call_main(module, fixture)
        self.assert_safe_report(result, "OUTPUT_EXISTS")
        self.assertEqual(fixture["output"].read_bytes(), CANARY.encode())
        self.assertEqual(set(fixture["output"].parent.iterdir()), {fixture["output"]})

    def test_write_failure_cleanup(self):
        for operation in ("tar", "file-fsync", "link"):
            with self.subTest(operation=operation):
                fixture = self.make_fixture()
                module = self.load_exporter()
                if operation == "tar":
                    patcher = mock.patch.object(tarfile.TarFile, "addfile", side_effect=OSError(errno.EIO, CANARY))
                elif operation == "file-fsync":
                    patcher = mock.patch.object(module.os, "fsync", side_effect=OSError(errno.EIO, CANARY))
                else:
                    patcher = mock.patch.object(module.os, "link", side_effect=OSError(errno.EIO, CANARY))
                with patcher:
                    self.assert_rejected(fixture, "WRITE_FAILED", runner=lambda f, e: self.call_main(module, f, e))

    def test_directory_fsync_failure_retains_complete_archive(self):
        fixture = self.make_fixture()
        module = self.load_exporter()
        real_fsync = os.fsync
        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(errno.EIO, CANARY)
            return real_fsync(fd)
        with mock.patch.object(module.os, "fsync", side_effect=fail_directory):
            result = self.call_main(module, fixture)
        self.assert_safe_report(result, "PUBLISH_UNCERTAIN", created=True)
        with tarfile.open(fixture["output"], "r:") as archive:
            self.assertEqual(archive.getnames(), self.expected_member_names())
            for name, payload in self.source_members(fixture):
                self.assertEqual(archive.extractfile(name).read(), payload)
        self.assertEqual(set(fixture["output"].parent.iterdir()), {fixture["output"]})

    def test_post_link_byte_change_is_publication_uncertainty(self):
        fixture = self.make_fixture()
        module = self.load_exporter()
        real_fsync = os.fsync
        changed = []

        def corrupt_before_directory_fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode) and not changed:
                with fixture["output"].open("r+b") as stream:
                    first = stream.read(1)
                    stream.seek(0)
                    stream.write(bytes([first[0] ^ 1]))
                    stream.flush()
                changed.append(True)
            return real_fsync(fd)

        with mock.patch.object(module.os, "fsync", side_effect=corrupt_before_directory_fsync):
            result = self.call_main(module, fixture)
        self.assert_safe_report(result, "PUBLISH_UNCERTAIN", created=True)
        self.assertTrue(fixture["output"].exists())
        self.assertEqual(set(fixture["output"].parent.iterdir()), {fixture["output"]})

    def test_source_mutation_after_tar_is_detected(self):
        for kind in ("same-size", "chmod", "hardlink", "truncate", "grow", "replace", "new-transcript"):
            with self.subTest(mutation=kind):
                fixture = self.make_fixture()
                module = self.load_exporter()
                original_build = module.build_archive
                source = self.source_paths(fixture)[3]
                def mutate_after_build(*args, **kwargs):
                    result = original_build(*args, **kwargs)
                    if kind == "same-size":
                        info = source.stat()
                        data = source.read_bytes()
                        source.write_bytes(b"X" + data[1:])
                        os.utime(source, ns=(info.st_atime_ns, info.st_mtime_ns))
                    elif kind == "chmod":
                        source.chmod(0o600)
                    elif kind == "hardlink":
                        os.link(source, source.with_name("new-hardlink"))
                    elif kind == "truncate":
                        source.write_bytes(b"")
                    elif kind == "grow":
                        source.write_bytes(source.read_bytes() + b"extra")
                    elif kind == "replace":
                        original = source.read_bytes()
                        source.rename(source.with_name("old-source"))
                        source.write_bytes(original)
                    else:
                        (source.parent / "extra.run.txt").write_text(CANARY)
                    return result
                with mock.patch.object(module, "build_archive", side_effect=mutate_after_build):
                    result = self.call_main(module, fixture)
                self.assert_safe_report(result, "SOURCE_CHANGED")
                self.assertFalse(fixture["output"].exists())
                self.assertEqual(list(fixture["output"].parent.iterdir()), [])

    def test_non_posix_rejected_before_any_write(self):
        fixture = self.make_fixture()
        module = self.load_exporter()
        with mock.patch.object(module, "is_posix_host", return_value=False), mock.patch.object(module.os, "open", side_effect=AssertionError(CANARY)), mock.patch.object(module.os, "link", side_effect=AssertionError(CANARY)):
            result = self.call_main(module, fixture)
        self.assert_safe_report(result, "UNSUPPORTED_PLATFORM")
        self.assertFalse(fixture["output"].exists())

    def test_help_is_available_without_platform_or_filesystem_access(self):
        module = self.load_exporter()
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(module, "is_posix_host", return_value=False), \
                mock.patch.object(module.os, "open", side_effect=AssertionError(CANARY)), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = module.main(["--help"])
        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("--candidate", stdout.getvalue())
        self.assertNotIn(CANARY, stdout.getvalue())

    def test_unexpected_exception_is_secret_safe(self):
        fixture = self.make_fixture()
        module = self.load_exporter()
        with mock.patch.object(module, "export", side_effect=RuntimeError(CANARY + str(self.base))):
            result = self.call_main(module, fixture)
        self.assert_safe_report(result, "INTERNAL_ERROR")
        self.assertFalse(fixture["output"].exists())

    def test_human_errors_are_secret_safe(self):
        fixture = self.make_fixture()
        fixture["summary"].write_text('{"' + CANARY)
        result = self.run_export(fixture, ("--human",))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "")
        self.assertIn("INVALID_JSON", result.stdout)
        for forbidden in (CANARY, str(self.base), "Traceback"):
            self.assertNotIn(forbidden, result.stdout)
        self.assertFalse(fixture["output"].exists())


if __name__ == "__main__":
    unittest.main()
