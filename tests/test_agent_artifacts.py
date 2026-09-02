"""Behavioral tests for content-addressed P1A artifacts and worker results."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.agentic.artifacts import ArtifactRecord, ArtifactStore
from helios_takeoff_core.agentic.contracts import validate_worker_result
from helios_takeoff_core.errors import ValidationError


class AgentArtifactTests(unittest.TestCase):
    """Catch artifacts that are mutable, escape their root, or lack content identity."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name) / "artifacts"
        self.store = ArtifactStore(self.root)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_json_bytes_are_canonical_deduplicated_and_verified(self) -> None:
        first = self.store.put_json(
            {"z": [3, 2, 1], "a": "audit"},
            media_type="application/json",
            schema_version="helios.p1a.input/v1",
        )
        second = self.store.put_json(
            {"a": "audit", "z": [3, 2, 1]},
            media_type="application/json",
            schema_version="helios.p1a.input/v1",
        )

        self.assertEqual(first.sha256, hashlib.sha256(b'{"a":"audit","z":[3,2,1]}').hexdigest())
        self.assertEqual(first, second)
        self.assertEqual(first.byte_size, len(b'{"a":"audit","z":[3,2,1]}'))
        self.assertTrue(self.store.verify(first))

    def test_file_copy_preserves_source_and_uses_content_addressed_path(self) -> None:
        source = Path(self.temporary_directory.name) / "audit-input.txt"
        original_bytes = b"mechanical schedule\nAHU-1\n"
        source.write_bytes(original_bytes)

        record = self.store.put_file(
            source,
            media_type="text/plain",
            schema_version="helios.p1a.source/v1",
        )
        stored_path = self.root / record.store_key

        self.assertEqual(source.read_bytes(), original_bytes)
        self.assertEqual(record.sha256, hashlib.sha256(original_bytes).hexdigest())
        self.assertEqual(stored_path.read_bytes(), original_bytes)
        self.assertTrue(stored_path.is_relative_to(self.root))
        self.assertIn(record.sha256, record.store_key)

    def test_verify_fails_after_stored_content_is_modified(self) -> None:
        record = self.store.put_json(
            {"report": "immutable"},
            media_type="application/json",
            schema_version="helios.p1a.input/v1",
        )
        (self.root / record.store_key).write_bytes(b"changed")

        self.assertFalse(self.store.verify(record))

    def test_cas_publication_fsyncs_files_and_directories(self) -> None:
        real_fsync = os.fsync
        synced_modes: list[int] = []

        def tracking_fsync(descriptor: int) -> None:
            synced_modes.append(os.fstat(descriptor).st_mode)
            real_fsync(descriptor)

        with mock.patch("helios_takeoff_core.agentic.artifacts.os.fsync", side_effect=tracking_fsync):
            store = ArtifactStore(Path(self.temporary_directory.name) / "durable-artifacts")
            store.put_bytes(
                b"durable publication",
                media_type="application/octet-stream",
                schema_version="test/durable-v1",
            )

        self.assertTrue(any(stat.S_ISREG(mode) for mode in synced_modes))
        self.assertTrue(any(stat.S_ISDIR(mode) for mode in synced_modes))

    def test_verify_rejects_store_key_traversal_and_escape(self) -> None:
        record = self.store.put_json(
            {"safe": True},
            media_type="application/json",
            schema_version="helios.p1a.input/v1",
        )
        outside = Path(self.temporary_directory.name) / "outside"
        outside.write_bytes(b"outside")

        for unsafe_key in ("../outside", str(outside)):
            with self.subTest(store_key=unsafe_key):
                unsafe_record: ArtifactRecord = replace(record, store_key=unsafe_key)
                self.assertFalse(self.store.verify(unsafe_record))

    def test_verify_returns_false_for_a_final_artifact_symlink_loop(self) -> None:
        content = b"loop-safe"
        digest = hashlib.sha256(content).hexdigest()
        record = ArtifactRecord(
            sha256=digest,
            byte_size=len(content),
            media_type="application/octet-stream",
            schema_version="helios.p1a.input/v1",
            store_key=f"sha256/{digest[:2]}/{digest}",
        )
        path = self.root / record.store_key
        path.parent.mkdir(parents=True)
        path.symlink_to(path.name)

        self.assertFalse(self.store.verify(record))

    def test_package_imports_and_blocks_without_posix_security_primitives(self) -> None:
        source_root = Path(__file__).resolve().parents[1] / "src"
        environment = os.environ | {"PYTHONPATH": str(source_root)}
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "\n".join(
                    [
                        "import os",
                        "from pathlib import Path",
                        "os.O_DIRECTORY = None",
                        "os.O_NOFOLLOW = None",
                        "from helios_takeoff_core.agentic import ArtifactStore",
                        "from helios_takeoff_core.errors import ValidationError",
                        "try:",
                        "    ArtifactStore(Path('artifact-store'))",
                        "except ValidationError as error:",
                        "    assert str(error) == 'secure P1A artifact storage is unavailable on this platform'",
                        "else:",
                        "    raise AssertionError('ArtifactStore must reject an insecure platform')",
                    ]
                ),
            ],
            capture_output=True,
            env=environment,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_put_json_never_publishes_partial_final_bytes(self) -> None:
        payload = {"data": "x" * 32_000_000}
        content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        destination = self.root / "sha256" / digest[:2] / digest
        writing_finished = threading.Event()
        partial_bytes_seen = threading.Event()

        def observer() -> None:
            while not writing_finished.is_set():
                try:
                    if destination.exists() and destination.read_bytes() != content:
                        partial_bytes_seen.set()
                        return
                except OSError:
                    pass

        prior_interval = sys.getswitchinterval()
        sys.setswitchinterval(0.000001)
        try:
            observer_thread = threading.Thread(target=observer)
            observer_thread.start()
            self.store.put_json(
                payload,
                media_type="application/json",
                schema_version="helios.p1a.input/v1",
            )
        finally:
            writing_finished.set()
            observer_thread.join()
            sys.setswitchinterval(prior_interval)

        self.assertFalse(partial_bytes_seen.is_set())

    def test_put_json_never_follows_a_parent_directory_swapped_to_symlink(self) -> None:
        store_directory = self.root / "sha256"
        parked_directory = self.root / "parked-sha256"
        outside_directory = Path(self.temporary_directory.name) / "outside"
        store_directory.mkdir(parents=True)
        outside_directory.mkdir()
        stop_attacker = threading.Event()

        def swap_parent_directory() -> None:
            while not stop_attacker.is_set():
                try:
                    os.replace(store_directory, parked_directory)
                    try:
                        store_directory.symlink_to(outside_directory, target_is_directory=True)
                    except FileExistsError:
                        continue
                    time.sleep(0.001)
                    if store_directory.is_symlink():
                        store_directory.unlink()
                    if not store_directory.exists() and parked_directory.exists():
                        os.replace(parked_directory, store_directory)
                except OSError:
                    pass

        attacker = threading.Thread(target=swap_parent_directory)
        attacker.start()
        try:
            for number in range(200):
                try:
                    self.store.put_json(
                        {"number": number},
                        media_type="application/json",
                        schema_version="helios.p1a.input/v1",
                    )
                except (OSError, ValidationError):
                    pass
        finally:
            stop_attacker.set()
            attacker.join()

        self.assertEqual(list(outside_directory.rglob("*")), [])
        recovered_store = ArtifactStore(self.root / "recovered")
        recovered_record = recovered_store.put_json(
            {"number": "after-race"},
            media_type="application/json",
            schema_version="helios.p1a.input/v1",
        )
        self.assertTrue(recovered_store.verify(recovered_record))

    def test_worker_result_accepts_exact_protocol_and_rejects_invalid_forms(self) -> None:
        expected_digest = "a" * 64
        valid = {
            "protocol": "helios.p1a.worker-result/v1",
            "status": "SUCCEEDED",
            "input_sha256": expected_digest,
            "report": {
                "kind": "BASELINE_AUDIT_REPORT",
                "schema_version": "helios.p1a.baseline-audit-report/v1",
                "payload": {"findings": []},
            },
        }

        self.assertEqual(validate_worker_result(valid, expected_input_sha256=expected_digest), valid)

        invalid_results = [
            {**valid, "unexpected": True},
            {**valid, "status": "FAILED"},
            {**valid, "input_sha256": "A" * 64},
            {**valid, "input_sha256": "b" * 64},
            {**valid, "protocol": "helios.p1a.worker-result/v2"},
            {**valid, "report": {**valid["report"], "kind": "OTHER_REPORT"}},
            {**valid, "report": {**valid["report"], "schema_version": "other/v1"}},
            {**valid, "report": {**valid["report"], "payload": []}},
            {**valid, "report": {**valid["report"], "unexpected": True}},
            [valid],
        ]
        for invalid in invalid_results:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    validate_worker_result(copy.deepcopy(invalid), expected_input_sha256=expected_digest)


if __name__ == "__main__":
    unittest.main()
