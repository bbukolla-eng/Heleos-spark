from __future__ import annotations


def valid_task_manifest() -> dict[str, object]:
    """Return the smallest complete Task 1 task-manifest fixture."""
    return {
        "protocol": "helios.build.task-manifest/v1",
        "task_id": "task-contracts",
        "capability_id": "build-fabric",
        "capability": "Build Fabric contracts",
        "node_type": "BuildTask",
        "base_commit_sha": "a" * 40,
        "dependency_node_ids": [],
        "roles": {
            "builder_profile_id": "builder-v1",
            "reviewer_profile_id": "reviewer-v1",
            "integrator_profile_id": "codex-control-v1",
        },
        "ownership": {
            "files": ["tools/helios_build/canonical.py"],
            "path_globs": [],
            "modules": ["tools.helios_build.canonical"],
            "migrations": [],
            "schemas": [],
            "public_interfaces": ["canonical_json_bytes"],
            "forbidden_paths": [],
        },
        "frozen_interfaces": [],
        "schema_impact": [],
        "required_source_record_ids": [],
        "required_source_packet_ids": [],
        "evidence_threshold": 0,
        "deliverables": [],
        "acceptance": {"behaviors": [], "commands": []},
        "budgets": {
            "implementation_seconds": 1,
            "cost_microusd": 0,
            "focused_test_seconds": 1,
            "affected_integration_seconds": 1,
            "milestone_seconds": 1,
            "maximum_correction_rounds": 0,
        },
        "checkpoint_boundaries": [],
        "conditions": {
            "stop": [],
            "block": [],
            "reroute": [],
            "escalation": [],
        },
        "correction_round": 0,
        "supersedes_task_manifest_sha256": None,
        "resume_from_checkpoint_sha256": None,
        "created_at": "2026-09-01T00:00:00Z",
        "created_by_profile_id": "codex-control-v1",
    }
