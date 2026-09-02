"""Task-scoped builder bundle tests for integrated SourcePacket graph nodes."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from tools.helios_build.canonical import canonical_json_bytes, sha256_hex
from tools.helios_build.errors import ContractError
from tools.helios_build.ledger import append_event
from tools.helios_build.research import import_source_packets, prepare_task_source_bundle
from tools.helios_build.store import ContentAddressedStore

from tests.build_fabric_support import valid_task_manifest
from tests.test_build_research_packets import (
    ResearchFixture,
    valid_packet,
    write_json,
)


class ResearchTaskInputTests(ResearchFixture):
    def _integrate_node(self, node_id: str) -> None:
        states = ("READY", "DISPATCHED", "RETURNED", "REVIEWED", "ACCEPTED", "INTEGRATED")
        prior = "DRAFT"
        for state in states:
            append_event(
                self.root / "build_control/graph/events.jsonl",
                {
                    "protocol": "helios.build.lifecycle-event/v1",
                    "node_id": node_id,
                    "prior_state": prior,
                    "new_state": state,
                    "reason_code": "TEST_FIXTURE_TRANSITION",
                    "actor_profile_id": "codex-control-v1",
                    "attempt_manifest_sha256": None,
                    "external_session_assignment_sha256": None,
                    "routing_event_sha256": None,
                    "recorded_at": "2026-09-01T18:30:00Z",
                },
                self.state,
            )
            prior = state

    def _frozen_task(
        self,
        packet_nodes: list[tuple[str, str]],
        *,
        integrate: bool = True,
        node_type: str = "SourcePacket",
    ) -> tuple[str, dict[str, object]]:
        node_ids = [node_id for node_id, _ in packet_nodes]
        task = valid_task_manifest()
        task["task_id"] = "builder-consumes-research"
        task["base_commit_sha"] = "d" * 40
        task["dependency_node_ids"] = node_ids
        task["required_source_packet_ids"] = node_ids
        task["required_source_record_ids"] = []
        task["created_at"] = "2026-09-01T18:20:00Z"
        task_stored = ContentAddressedStore(self.root / "build_control").put_json("tasks", task)
        task_node = "builder-task"
        graph = {
            "protocol": "helios.build.graph-manifest/v1",
            "graph_id": "builder-research-input-graph",
            "nodes": [
                *[
                    {
                        "node_id": node_id,
                        "node_type": node_type,
                        "manifest_sha256": digest,
                    }
                    for node_id, digest in packet_nodes
                ],
                {
                    "node_id": task_node,
                    "node_type": "BuildTask",
                    "manifest_sha256": task_stored.sha256,
                },
            ],
            "edges": [
                {"edge_type": "DEPENDS_ON", "from_node_id": node_id, "to_node_id": task_node}
                for node_id in node_ids
            ],
            "created_at": "2026-09-01T18:20:00Z",
            "created_by_profile_id": "codex-control-v1",
        }
        write_json(self.root / "build_control/graph/builder-research-input.json", graph)
        if integrate:
            for node_id in node_ids:
                self._integrate_node(node_id)
        return task_stored.sha256, task

    def test_bundle_uses_only_declared_integrated_nodes_and_preserves_gaps(self) -> None:
        request_a, request_sha_a = self.export()
        request_b, request_sha_b = self.export(request_id="REQ-N03-001", notebook_id="N03")
        packet_a = valid_packet(request_a, request_sha_a)
        packet_b = valid_packet(request_b, request_sha_b, packet_id="PACKET-N03-001")
        packet_b["citations"].append(
            {
                "citation_id": "CITE-PACKET-N03-UNRESOLVED",
                "source_id": "SRC-OFFICIAL-001",
                "resolution": "UNRESOLVED",
                "locator": None,
                "limitation": "ATHENA could not resolve the referenced appendix locator.",
            }
        )
        imported = import_source_packets(
            self.paths,
            [
                write_json(Path(self.temporary.name) / "a.json", packet_a),
                write_json(Path(self.temporary.name) / "b.json", packet_b),
            ],
        )
        packet_digests = dict(zip(imported.packet_ids, imported.packet_sha256s, strict=True))
        task_sha, _ = self._frozen_task(
            [
                ("packet-air", packet_digests["PACKET-N02-001"]),
                ("packet-pipe", packet_digests["PACKET-N03-001"]),
            ]
        )
        stored = prepare_task_source_bundle(self.paths, task_sha)
        bundle = json.loads(stored.path.read_text(encoding="utf-8"))
        self.assertEqual(bundle["task_manifest_sha256"], task_sha)
        self.assertEqual(
            [item["dependency_node_id"] for item in bundle["packet_dependencies"]],
            ["packet-air", "packet-pipe"],
        )
        self.assertEqual(len(bundle["sources"]), 1)
        self.assertEqual(bundle["sources"][0]["notebook_ids"], ["N02", "N03"])
        self.assertEqual(len(bundle["citations"]), 3)
        self.assertTrue(
            any(item["citation"]["resolution"] == "UNRESOLVED" for item in bundle["citations"])
        )
        self.assertEqual(len(bundle["findings"]), 2)
        self.assertEqual(len(bundle["gaps"]), 2)
        self.assertNotIn("code", bundle)
        replay = prepare_task_source_bundle(self.paths, task_sha)
        self.assertEqual((stored.sha256, replay.sha256), (replay.sha256, stored.sha256))
        self.assertTrue(replay.replayed)

    def test_distinct_dependency_nodes_may_reuse_one_packet_digest(self) -> None:
        request, request_sha = self.export()
        packet = valid_packet(request, request_sha)
        imported = import_source_packets(
            self.paths, [write_json(Path(self.temporary.name) / "packet.json", packet)]
        )
        digest = imported.packet_sha256s[0]
        task_sha, _ = self._frozen_task([("packet-a", digest), ("packet-b", digest)])
        bundle = json.loads(prepare_task_source_bundle(self.paths, task_sha).path.read_text())
        self.assertEqual(len(bundle["packet_dependencies"]), 2)
        self.assertEqual(len(bundle["packets"]), 1)
        self.assertEqual(len(bundle["findings"]), 1)

    def test_bundle_rejects_unintegrated_wrong_type_missing_and_mismatched_lineage(self) -> None:
        request, request_sha = self.export()
        packet = valid_packet(request, request_sha)
        imported = import_source_packets(
            self.paths, [write_json(Path(self.temporary.name) / "packet.json", packet)]
        )
        digest = imported.packet_sha256s[0]

        task_sha, _ = self._frozen_task([("packet-a", digest)], integrate=False)
        with self.assertRaisesRegex(ContractError, "INTEGRATED"):
            prepare_task_source_bundle(self.paths, task_sha)

        (self.root / "build_control/graph/builder-research-input.json").unlink()
        task_sha, _ = self._frozen_task([("packet-a", digest)], node_type="Artifact")
        with self.assertRaisesRegex(ContractError, "SourcePacket"):
            prepare_task_source_bundle(self.paths, task_sha)

        (self.root / "build_control/graph/builder-research-input.json").unlink()
        (self.root / "build_control/graph/events.jsonl").unlink()
        task_sha, _ = self._frozen_task([("packet-a", "f" * 64)])
        with self.assertRaisesRegex(ContractError, "packet|digest"):
            prepare_task_source_bundle(self.paths, task_sha)

    def test_superseded_packet_cannot_enter_a_new_builder_bundle(self) -> None:
        request, request_sha = self.export()
        original = valid_packet(request, request_sha)
        first = import_source_packets(
            self.paths, [write_json(Path(self.temporary.name) / "first.json", original)]
        )
        successor = valid_packet(
            request,
            request_sha,
            packet_id="PACKET-N02-002",
            supersedes_packet_id="PACKET-N02-001",
        )
        import_source_packets(
            self.paths, [write_json(Path(self.temporary.name) / "successor.json", successor)]
        )
        task_sha, _ = self._frozen_task([("packet-old", first.packet_sha256s[0])])
        with self.assertRaisesRegex(ContractError, "superseded"):
            prepare_task_source_bundle(self.paths, task_sha)


if __name__ == "__main__":
    import unittest

    unittest.main()
