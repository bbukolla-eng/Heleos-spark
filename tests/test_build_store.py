from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from tests.build_fabric_support import valid_task_manifest
from tools.helios_build.errors import ContractError
from tools.helios_build.ledger import append_event, replay_events
from tools.helios_build.store import ContentAddressedStore


def lifecycle_event(prior_state: str, new_state: str) -> dict[str, object]:
    return {
        "protocol": "helios.build.lifecycle-event/v1",
        "node_id": "storage-test",
        "prior_state": prior_state,
        "new_state": new_state,
        "reason_code": "TEST_TRANSITION",
        "actor_profile_id": "codex-control-v1",
        "attempt_manifest_sha256": None,
        "external_session_assignment_sha256": None,
        "routing_event_sha256": None,
        "recorded_at": "2026-09-01T00:00:00Z",
        "previous_event_sha256": None,
    }


class BuildStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_json_publication_replays_existing_identical_object(self) -> None:
        store = ContentAddressedStore(self.root / "control")

        first = store.put_json("tasks", valid_task_manifest())
        second = store.put_json("tasks", valid_task_manifest())

        self.assertEqual(first.sha256, second.sha256)
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(store.get_json(first.sha256), valid_task_manifest())

    def test_replay_rejects_an_identical_object_with_tampered_mode(self) -> None:
        store = ContentAddressedStore(self.root / "control")
        first = store.put_json("tasks", valid_task_manifest())

        self.assertEqual(stat.S_IMODE(first.path.lstat().st_mode), 0o644)
        os.chmod(first.path, 0o600)

        with self.assertRaisesRegex(ContractError, "mode"):
            store.put_json("tasks", valid_task_manifest())

    def test_replay_rejects_truncated_event_ledger(self) -> None:
        events = self.root / "events.jsonl"
        lock_root = self.root / "external-state"
        append_event(events, lifecycle_event("DRAFT", "READY"), lock_root)
        events.write_bytes(events.read_bytes()[:-4])

        with self.assertRaisesRegex(ContractError, "event ledger"):
            replay_events(events)
