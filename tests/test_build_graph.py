from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from tests.build_fabric_support import valid_task_manifest
from tools.helios_build.canonical import canonical_json_bytes, sha256_hex
from tools.helios_build.errors import CollisionError, ContractError, TransitionError
from tools.helios_build.graph import BuildGraph, descendants, ready_nodes, transition_node, validate_acyclic
from tools.helios_build.ledger import append_event, replay_events
from tools.helios_build.ownership import OwnershipClaim, assert_owned_changes, find_collisions
from tools.helios_build.paths import BuildPaths
from tools.helios_build.types import NodeState


def task_with_ownership(*, suffix: str = "candidate", files: list[str] | None = None) -> dict[str, object]:
    task = valid_task_manifest() | {"task_id": f"task-{suffix}"}
    task["ownership"] = {
        "files": files if files is not None else ["src/helios_takeoff_core/engine/compiler.py"],
        "path_globs": ["src/helios_takeoff_core/engine/*.py"],
        "modules": ["helios_takeoff_core.engine"],
        "migrations": ["src/helios_takeoff_core/migrations/018_build.sql"],
        "schemas": ["build_control/schemas/task-manifest-v1.schema.json"],
        "public_interfaces": ["compile_takeoff"],
        "forbidden_paths": ["src/helios_takeoff_core/db.py"],
    }
    return task


def graph_manifest(nodes: list[tuple[str, str, str]], edges: list[dict[str, str]], graph_id: str = "build-graph-tests") -> dict[str, object]:
    return {
        "protocol": "helios.build.graph-manifest/v1",
        "graph_id": graph_id,
        "nodes": [{"node_id": node_id, "node_type": node_type, "manifest_sha256": digest} for node_id, node_type, digest in nodes],
        "edges": edges,
        "created_at": "2026-09-01T00:00:00Z",
        "created_by_profile_id": "codex-control-v1",
    }


class BuildGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        source_schemas = Path(__file__).resolve().parents[1] / "build_control" / "schemas"
        shutil.copytree(source_schemas, self.root / "build_control" / "schemas")
        (self.root / "build_control" / "tasks").mkdir(parents=True)
        (self.root / "build_control" / "graph").mkdir(parents=True)
        (self.root / "build_control" / "graph" / "events.jsonl").touch()
        self.paths = BuildPaths(self.root, self.root.parent / f"{self.root.name}-state")
        self.paths.state_root.mkdir(mode=0o700)
        self.paths.state_root.chmod(0o700)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def _write_task(self, task: dict[str, object]) -> str:
        digest = sha256_hex(canonical_json_bytes(task))
        path = self.root / "build_control" / "tasks" / "sha256" / digest[:2] / f"{digest}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(task))
        return digest

    def _write_graph(self, manifest: dict[str, object], name: str = "graph") -> None:
        path = self.root / "build_control" / "graph" / "sha256" / name / "graph.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_json_bytes(manifest))

    def _advance(self, node_id: str, target: NodeState) -> None:
        sequence = [NodeState.READY, NodeState.DISPATCHED, NodeState.RETURNED, NodeState.REVIEWED, NodeState.ACCEPTED, NodeState.INTEGRATED]
        current = NodeState.DRAFT
        for state in sequence[: sequence.index(target) + 1]:
            append_event(
                self.root / "build_control" / "graph" / "events.jsonl",
                {
                    "protocol": "helios.build.lifecycle-event/v1",
                    "node_id": node_id,
                    "prior_state": current.value,
                    "new_state": state.value,
                    "reason_code": "TEST",
                    "actor_profile_id": "codex-control-v1",
                    "attempt_manifest_sha256": None,
                    "external_session_assignment_sha256": None,
                    "routing_event_sha256": None,
                    "recorded_at": "2026-09-01T00:00:00Z",
                },
                self.paths.state_root,
            )
            current = state

    def _task(self, name: str, **updates: object) -> dict[str, object]:
        task = task_with_ownership(suffix=name, files=[f"src/{name}.py"])
        task["ownership"] = {
            "files": [f"src/{name}.py"],
            "path_globs": [f"src/{name}/*.py"],
            "modules": [f"helios.{name}"],
            "migrations": [f"migrations/{name}.sql"],
            "schemas": [f"schemas/{name}.json"],
            "public_interfaces": [f"{name}_interface"],
            "forbidden_paths": [],
        }
        task.update(updates)
        return task

    def test_conservative_collisions_and_owned_changes(self) -> None:
        candidate = OwnershipClaim.from_manifest(task_with_ownership())
        active = OwnershipClaim.from_manifest(task_with_ownership(suffix="active"))
        self.assertEqual(
            {item.kind for item in find_collisions(candidate, [active])},
            {"FILE", "PATH_GLOB", "MODULE", "MIGRATION", "SCHEMA", "PUBLIC_INTERFACE"},
        )
        assert_owned_changes(candidate, ["src/helios_takeoff_core/engine/compiler.py"])
        with self.assertRaises(CollisionError):
            assert_owned_changes(candidate, ["src/helios_takeoff_core/db.py"])
        with self.assertRaises(CollisionError):
            assert_owned_changes(candidate, ["README.md"])

    def test_cycle_and_blocked_descendants(self) -> None:
        cyclic = BuildGraph.load(
            graph_manifest(
                [("first", "BuildTask", "1" * 64), ("second", "BuildTask", "2" * 64), ("third", "BuildTask", "3" * 64)],
                [
                    {"edge_type": "DEPENDS_ON", "from_node_id": "first", "to_node_id": "second"},
                    {"edge_type": "DEPENDS_ON", "from_node_id": "second", "to_node_id": "third"},
                    {"edge_type": "DEPENDS_ON", "from_node_id": "third", "to_node_id": "first"},
                ],
            ),
            [],
        )
        with self.assertRaisesRegex(ContractError, r"cycle.*first.*second.*third.*first"):
            validate_acyclic(cyclic)
        graph = BuildGraph.load(
            graph_manifest(
                [("blocked", "BuildTask", "4" * 64), ("dependent", "BuildTask", "5" * 64), ("independent", "BuildTask", "6" * 64)],
                [{"edge_type": "DEPENDS_ON", "from_node_id": "blocked", "to_node_id": "dependent"}],
            ),
            [{"node_id": "blocked", "prior_state": "DRAFT", "new_state": "BLOCKED"}],
            readiness_resolver=lambda _graph, _node: True,
        )
        self.assertEqual(graph.state("dependent"), NodeState.BLOCKED)
        self.assertEqual(graph.state("independent"), NodeState.READY)
        self.assertEqual(descendants(graph, "blocked"), frozenset({"dependent"}))
        self.assertEqual(ready_nodes(graph), ("independent",))

    def test_ready_requires_integrated_opaque_artifacts_and_checkpoint(self) -> None:
        task = self._task(
            "candidate",
            dependency_node_ids=["record", "packet", "checkpoint"],
            required_source_record_ids=["record"],
            required_source_packet_ids=["packet"],
            resume_from_checkpoint_sha256="a" * 64,
        )
        digest = self._write_task(task)
        self._write_graph(
            graph_manifest(
                [("record", "ResearchRequest", "7" * 64), ("packet", "SourcePacket", "8" * 64), ("checkpoint", "Checkpoint", "a" * 64), ("candidate", "BuildTask", digest)],
                [
                    {"edge_type": "DEPENDS_ON", "from_node_id": "record", "to_node_id": "candidate"},
                    {"edge_type": "DEPENDS_ON", "from_node_id": "packet", "to_node_id": "candidate"},
                    {"edge_type": "DEPENDS_ON", "from_node_id": "checkpoint", "to_node_id": "candidate"},
                ],
            )
        )
        for node in ("record", "packet", "checkpoint"):
            self._advance(node, NodeState.INTEGRATED)
        transition_node(self.paths, "candidate", NodeState.DRAFT, NodeState.READY, "READY", "codex-control-v1")
        self.assertEqual(replay_events(self.root / "build_control" / "graph" / "events.jsonl")[-1]["new_state"], "READY")

        missing = self._task("missing", required_source_record_ids=["not-a-parent"])
        missing_digest = self._write_task(missing)
        self._write_graph(graph_manifest([("missing", "BuildTask", missing_digest)], [], "missing"), "missing")
        with self.assertRaisesRegex(TransitionError, "not ready"):
            transition_node(self.paths, "missing", NodeState.DRAFT, NodeState.READY, "READY", "codex-control-v1")

    def test_dispatch_rejects_each_active_ownership_collision_state(self) -> None:
        states = [NodeState.DISPATCHED, NodeState.RETURNED, NodeState.REVIEWED, NodeState.ACCEPTED]
        candidates = [self._task(f"candidate-{state.value.lower()}") for state in states]
        active = [self._task(f"active-{state.value.lower()}") for state in states]
        for candidate, writer in zip(candidates, active, strict=True):
            writer["ownership"] = candidate["ownership"]
        digests = [self._write_task(task) for task in [*candidates, *active]]
        nodes = [
            (f"candidate-{state.value.lower()}", "BuildTask", digests[index])
            for index, state in enumerate(states)
        ]
        nodes.extend(
            (f"active-{state.value.lower()}", "BuildTask", digests[len(states) + index])
            for index, state in enumerate(states)
        )
        self._write_graph(graph_manifest(nodes, []))
        for state in states:
            self._advance(f"active-{state.value.lower()}", state)
            transition_node(self.paths, f"candidate-{state.value.lower()}", NodeState.DRAFT, NodeState.READY, "READY", "codex-control-v1")
        for state in states:
            with self.subTest(active_state=state):
                with self.assertRaises(CollisionError):
                    transition_node(self.paths, f"candidate-{state.value.lower()}", NodeState.READY, NodeState.DISPATCHED, "DISPATCH", "codex-control-v1")

    def test_integration_requires_codex_and_exact_valid_task_evidence(self) -> None:
        task = self._task("integrate")
        digest = self._write_task(task)
        self._write_graph(graph_manifest([("integrate", "BuildTask", digest)], []))
        self._advance("integrate", NodeState.ACCEPTED)
        with self.assertRaisesRegex(TransitionError, "only codex-control-v1"):
            transition_node(self.paths, "integrate", NodeState.ACCEPTED, NodeState.INTEGRATED, "INTEGRATE", "builder-v1")
        transition_node(self.paths, "integrate", NodeState.ACCEPTED, NodeState.INTEGRATED, "INTEGRATE", "codex-control-v1")

    def test_integration_rejects_missing_or_mismatched_task_evidence(self) -> None:
        self._write_task(self._task("unrelated"))
        self._write_graph(graph_manifest([("missing", "BuildTask", "c" * 64)], []))
        self._advance("missing", NodeState.ACCEPTED)
        with self.assertRaisesRegex(TransitionError, "exactly one committed task manifest"):
            transition_node(self.paths, "missing", NodeState.ACCEPTED, NodeState.INTEGRATED, "INTEGRATE", "codex-control-v1")

    def test_integration_rejects_invalid_task_evidence(self) -> None:
        invalid = self._task("invalid")
        invalid["roles"] = {"builder_profile_id": "same", "reviewer_profile_id": "same", "integrator_profile_id": "codex-control-v1"}
        digest = self._write_task(invalid)
        self._write_graph(graph_manifest([("invalid", "BuildTask", digest)], []))
        self._advance("invalid", NodeState.ACCEPTED)
        with self.assertRaisesRegex(TransitionError, "invalid committed task manifest"):
            transition_node(self.paths, "invalid", NodeState.ACCEPTED, NodeState.INTEGRATED, "INTEGRATE", "codex-control-v1")

    def test_supersession_requires_real_schema_valid_successor(self) -> None:
        predecessor = self._task("predecessor")
        predecessor_digest = self._write_task(predecessor)
        successor = self._task("successor", supersedes_task_manifest_sha256=predecessor_digest)
        self._write_task(successor)
        manifest = graph_manifest([("predecessor", "BuildTask", predecessor_digest)], [])
        self._write_graph(manifest)
        with self.assertRaisesRegex(TransitionError, "routing"):
            transition_node(
                self.paths,
                "predecessor",
                NodeState.DRAFT,
                NodeState.SUPERSEDED,
                "REROUTE",
                "codex-control-v1",
            )
        with self.assertRaisesRegex(ContractError, "successor"):
            BuildGraph.load(manifest, [{"node_id": "predecessor", "prior_state": "DRAFT", "new_state": "SUPERSEDED"}])
        self.assertEqual(
            BuildGraph.load(
                manifest,
                [{"node_id": "predecessor", "prior_state": "DRAFT", "new_state": "SUPERSEDED"}],
                successor_resolver=lambda node: node.manifest_sha256 == predecessor_digest,
            ).state("predecessor"),
            NodeState.SUPERSEDED,
        )

    def test_supersession_without_successor_is_rejected(self) -> None:
        task = self._task("orphan")
        digest = self._write_task(task)
        self._write_graph(graph_manifest([("orphan", "BuildTask", digest)], []))
        with self.assertRaisesRegex(TransitionError, "without a valid successor"):
            transition_node(self.paths, "orphan", NodeState.DRAFT, NodeState.SUPERSEDED, "REROUTE", "codex-control-v1")

    def test_unknown_ambiguous_role_and_transition_lock_fail_closed(self) -> None:
        with self.assertRaisesRegex(TransitionError, "unknown"):
            transition_node(self.paths, "unknown", NodeState.DRAFT, NodeState.READY, "READY", "codex-control-v1")
        task = self._task("ambiguous")
        digest = self._write_task(task)
        self._write_graph(graph_manifest([("ambiguous", "BuildTask", digest)], [], "one"), "one")
        self._write_graph(graph_manifest([("ambiguous", "BuildTask", digest)], [], "two"), "two")
        with self.assertRaisesRegex(TransitionError, "ambiguous"):
            transition_node(self.paths, "ambiguous", NodeState.DRAFT, NodeState.READY, "READY", "codex-control-v1")

        role_task = self._task("role")
        role_task["roles"] = {"builder_profile_id": "same", "reviewer_profile_id": "same", "integrator_profile_id": "codex-control-v1"}
        role_digest = self._write_task(role_task)
        self._write_graph(graph_manifest([("role", "BuildTask", role_digest)], [], "role"), "role")
        with self.assertRaisesRegex(TransitionError, "builder_profile_id"):
            transition_node(self.paths, "role", NodeState.DRAFT, NodeState.READY, "READY", "codex-control-v1")

        locks = self.paths.state_root / "locks"
        locks.mkdir(parents=True, exist_ok=True)
        stale = locks / "build-control-transition.lock"
        stale.write_text("stale", encoding="utf-8")
        with self.assertRaisesRegex(TransitionError, "transition lock"):
            transition_node(self.paths, "role", NodeState.DRAFT, NodeState.READY, "READY", "codex-control-v1")
        self.assertTrue(stale.exists())

    def test_lane_cap_applies_only_to_build_tasks(self) -> None:
        tasks = [self._task(f"active-{index}") for index in range(3)]
        candidate = self._task("candidate")
        digests = [self._write_task(task) for task in [*tasks, candidate]]
        nodes = [(f"active-{index}", "BuildTask", digests[index]) for index in range(3)]
        nodes.extend([("candidate", "BuildTask", digests[3]), ("review", "Review", "b" * 64)])
        self._write_graph(graph_manifest(nodes, []))
        for index in range(3):
            self._advance(f"active-{index}", NodeState.DISPATCHED)
        transition_node(self.paths, "candidate", NodeState.DRAFT, NodeState.READY, "READY", "codex-control-v1")
        with self.assertRaisesRegex(TransitionError, "three implementation"):
            transition_node(self.paths, "candidate", NodeState.READY, NodeState.DISPATCHED, "DISPATCH", "codex-control-v1")
        transition_node(self.paths, "review", NodeState.DRAFT, NodeState.READY, "READY", "codex-control-v1")
        transition_node(self.paths, "review", NodeState.READY, NodeState.DISPATCHED, "DISPATCH", "codex-control-v1")


if __name__ == "__main__":
    unittest.main()
