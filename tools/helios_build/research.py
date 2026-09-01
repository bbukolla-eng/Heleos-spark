"""Finite file transport for ATHENA research requests and source packets."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from tools.helios_build.canonical import canonical_json_bytes, load_strict_json, sha256_hex
from tools.helios_build.errors import ContractError
from tools.helios_build.graph import BuildGraph, validate_acyclic
from tools.helios_build.ledger import replay_events
from tools.helios_build.paths import BuildPaths
from tools.helios_build.schemas import SchemaRegistry
from tools.helios_build.source_registry import (
    SourceRegistry,
    canonical_source_record,
    load_athena_profile,
    load_notebook_manifest,
    research_operation_lock,
)
from tools.helios_build.store import ContentAddressedStore, StoredObject
from tools.helios_build.types import NodeState, NodeType


REQUEST_PROTOCOL = "helios.build.research-request/v1"
PACKET_PROTOCOL = "helios.build.source-packet/v1"
BUNDLE_PROTOCOL = "helios.build.builder-source-bundle/v1"


@dataclass(frozen=True, slots=True)
class PacketImportResult:
    """Stable result from one atomic packet batch import."""

    packet_ids: tuple[str, ...]
    packet_sha256s: tuple[str, ...]
    source_ids: tuple[str, ...]
    source_sha256s: tuple[str, ...]
    notebook_memberships: Mapping[str, tuple[str, ...]]
    import_event_sha256: str | None
    replayed: bool


def export_research_request(paths: BuildPaths, request_path: Path) -> StoredObject:
    """Validate and content-address one provider-independent ResearchRequest."""
    schemas = SchemaRegistry(paths.repo_root)
    request = load_strict_json(request_path)
    schemas.validate(request, "research-request-v1.schema.json")
    content = canonical_json_bytes(request)
    digest = sha256_hex(content)
    with research_operation_lock(paths):
        manifest = load_notebook_manifest(
            paths, request["notebook_manifest_sha256"], schemas
        )
        profile = load_athena_profile(paths, request["athena_profile_sha256"], schemas)
        notebook_ids = {item["notebook_id"] for item in manifest["notebooks"]}
        if request["target_notebook_id"] not in notebook_ids:
            raise ContractError("ResearchRequest target_notebook_id is not in its notebook manifest")
        if request["data_class"] not in profile["allowed_data_classes"]:
            raise ContractError("ResearchRequest data_class is not allowed by the ATHENA profile")
        if request["data_class"] in request["excluded_data_classes"]:
            raise ContractError("ResearchRequest excludes its own data_class")
        if request["required_output_protocol"] not in profile["allowed_output_protocols"]:
            raise ContractError(
                "ResearchRequest output protocol is not allowed by the ATHENA profile"
            )

        requests_root = paths.repo_root / "build_control" / "tasks" / "research_requests"
        for existing_path in sorted((requests_root / "sha256").glob("*/*.json")):
            existing = _load_verified_json(existing_path)
            schemas.validate(existing, "research-request-v1.schema.json")
            if existing["request_id"] == request["request_id"] and existing_path.stem != digest:
                raise ContractError(
                    f"request_id {request['request_id']} is reused with changed bytes"
                )
        stored = ContentAddressedStore(
            paths.repo_root / "build_control" / "tasks"
        ).put_json("research_requests", request)
        if stored.sha256 != digest:
            raise ContractError("published ResearchRequest digest mismatch")
        return stored


def import_source_packets(
    paths: BuildPaths, packet_paths: Sequence[Path]
) -> PacketImportResult:
    """Validate a complete batch, publish its objects, then append one final event."""
    if not packet_paths:
        raise ContractError("packet import requires at least one SourcePacket file")
    schemas = SchemaRegistry(paths.repo_root)
    packets_by_id: dict[str, tuple[dict[str, Any], str]] = {}
    for packet_path in packet_paths:
        packet = load_strict_json(packet_path)
        schemas.validate(packet, "source-packet-v1.schema.json")
        digest = sha256_hex(canonical_json_bytes(packet))
        packet_id = packet["packet_id"]
        prior = packets_by_id.get(packet_id)
        if prior is not None and prior[1] != digest:
            raise ContractError(f"packet_id {packet_id} conflicts inside the import batch")
        packets_by_id[packet_id] = (packet, digest)

    ordered = tuple(packets_by_id[key] for key in sorted(packets_by_id))
    registry = SourceRegistry(paths, schemas)
    imported = registry.import_packets(
        [packet for packet, _ in ordered], [digest for _, digest in ordered]
    )
    snapshot = imported.snapshot
    packet_ids = tuple(packet["packet_id"] for packet, _ in ordered)
    packet_sha256s = tuple(digest for _, digest in ordered)
    source_ids = tuple(
        sorted({source["source_id"] for packet, _ in ordered for source in packet["sources"]})
    )
    return PacketImportResult(
        packet_ids=packet_ids,
        packet_sha256s=packet_sha256s,
        source_ids=source_ids,
        source_sha256s=tuple(snapshot.source_sha256_by_id[item] for item in source_ids),
        notebook_memberships=MappingProxyType(
            {item: snapshot.memberships_by_source_id[item] for item in source_ids}
        ),
        import_event_sha256=imported.import_event_sha256,
        replayed=imported.replayed,
    )


def prepare_task_source_bundle(paths: BuildPaths, task_manifest_sha256: str) -> StoredObject:
    """Build a deterministic bundle from one task's exact integrated packet nodes."""
    schemas = SchemaRegistry(paths.repo_root)
    task = _load_task(paths, task_manifest_sha256, schemas)
    graph_manifest, task_node_id = _resolve_task_graph(
        paths, task_manifest_sha256, schemas
    )
    node_ids = {node["node_id"] for node in graph_manifest["nodes"]}
    events = tuple(
        event
        for event in replay_events(paths.repo_root / "build_control" / "graph" / "events.jsonl")
        if event.get("node_id") in node_ids
    )
    for event in events:
        schemas.validate(event, "lifecycle-event-v1.schema.json")
    graph = BuildGraph.load(
        graph_manifest,
        events,
        successor_resolver=lambda _: True,
    )
    validate_acyclic(graph)
    dependencies = graph.dependencies(task_node_id)
    if set(task["dependency_node_ids"]) != set(dependencies):
        raise ContractError("frozen task dependency_node_ids differ from its committed graph")

    required_ids = task["required_source_packet_ids"]
    if not isinstance(required_ids, list):
        raise ContractError("frozen task required_source_packet_ids must be an array")
    registry = SourceRegistry(paths, schemas)
    snapshot = registry.snapshot()
    dependency_rows: list[dict[str, Any]] = []
    packets_by_digest: dict[str, dict[str, Any]] = {}
    for dependency_node_id in sorted(required_ids):
        if dependency_node_id not in dependencies:
            raise ContractError(
                f"required SourcePacket node is not a task dependency: {dependency_node_id}"
            )
        node = graph.nodes.get(dependency_node_id)
        if node is None or node.node_type is not NodeType.SOURCE_PACKET:
            raise ContractError(
                f"task dependency {dependency_node_id} is not a SourcePacket node"
            )
        if graph.state(dependency_node_id) is not NodeState.INTEGRATED:
            raise ContractError(
                f"task SourcePacket dependency {dependency_node_id} is not INTEGRATED"
            )
        packet = registry.packet_by_digest(node.manifest_sha256)
        packet_id = packet["packet_id"]
        if snapshot.packet_ids_by_sha256.get(node.manifest_sha256) != packet_id:
            raise ContractError("SourcePacket graph digest does not match the import lineage")
        if packet_id in snapshot.successor_by_packet_id:
            raise ContractError(f"task SourcePacket dependency {packet_id} is superseded")
        dependency_rows.append(
            {
                "dependency_node_id": dependency_node_id,
                "packet_id": packet_id,
                "packet_sha256": node.manifest_sha256,
            }
        )
        packets_by_digest[node.manifest_sha256] = packet

    packet_rows: list[dict[str, Any]] = []
    source_accumulator: dict[str, dict[str, Any]] = {}
    citations: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    limitations: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for packet_sha, packet in sorted(packets_by_digest.items()):
        packet_id = packet["packet_id"]
        notebook_id = packet["notebook_id"]
        packet_rows.append(
            {
                "packet_id": packet_id,
                "packet_sha256": packet_sha,
                "request_id": packet["request_id"],
                "request_sha256": packet["request_sha256"],
                "notebook_id": notebook_id,
            }
        )
        for raw_source in packet["sources"]:
            source = canonical_source_record(raw_source, schemas)
            source_id = source["source_id"]
            source_sha = snapshot.source_sha256_by_id.get(source_id)
            if source_sha is None or sha256_hex(canonical_json_bytes(source)) != source_sha:
                raise ContractError("SourcePacket source does not match the canonical source registry")
            row = source_accumulator.setdefault(
                source_id,
                {
                    "source_record_sha256": source_sha,
                    "source": source,
                    "packet_ids": set(),
                    "notebook_ids": set(),
                },
            )
            row["packet_ids"].add(packet_id)
            row["notebook_ids"].add(notebook_id)
        citations.extend(
            {"packet_id": packet_id, "notebook_id": notebook_id, "citation": item}
            for item in packet["citations"]
        )
        findings.extend(
            {"packet_id": packet_id, "notebook_id": notebook_id, "finding": item}
            for item in packet["findings"]
        )
        conflicts.extend(
            {"packet_id": packet_id, "notebook_id": notebook_id, "conflict": item}
            for item in packet["conflicts"]
        )
        limitations.extend(
            {"packet_id": packet_id, "notebook_id": notebook_id, "text": item}
            for item in packet["limitations"]
        )
        gaps.extend(
            {"packet_id": packet_id, "notebook_id": notebook_id, "question": item}
            for item in packet["unanswered_questions"]
        )

    sources = [
        {
            "source_record_sha256": row["source_record_sha256"],
            "source": row["source"],
            "packet_ids": sorted(row["packet_ids"]),
            "notebook_ids": sorted(row["notebook_ids"]),
        }
        for _, row in sorted(source_accumulator.items())
    ]
    bundle = {
        "protocol": BUNDLE_PROTOCOL,
        "task_manifest_sha256": task_manifest_sha256,
        "task_id": task["task_id"],
        "packet_dependencies": dependency_rows,
        "packets": sorted(packet_rows, key=lambda item: (item["packet_id"], item["packet_sha256"])),
        "sources": sources,
        "citations": sorted(
            citations, key=lambda item: (item["packet_id"], item["citation"]["citation_id"])
        ),
        "findings": sorted(
            findings, key=lambda item: (item["packet_id"], item["finding"]["finding_id"])
        ),
        "conflicts": sorted(
            conflicts, key=lambda item: (item["packet_id"], item["conflict"]["conflict_id"])
        ),
        "limitations": sorted(
            limitations, key=lambda item: (item["packet_id"], item["text"])
        ),
        "gaps": sorted(gaps, key=lambda item: (item["packet_id"], item["question"])),
    }
    schemas.validate(bundle, "builder-source-bundle-v1.schema.json")
    return ContentAddressedStore(paths.repo_root / "build_control").put_json(
        "builder_source_bundles", bundle
    )


def _load_task(
    paths: BuildPaths, digest: str, schemas: SchemaRegistry
) -> dict[str, Any]:
    if not _is_digest(digest):
        raise ContractError("task manifest reference must be a lowercase SHA-256 digest")
    path = (
        paths.repo_root
        / "build_control/tasks/sha256"
        / digest[:2]
        / f"{digest}.json"
    )
    task = _load_verified_json(path, expected_digest=digest)
    schemas.validate(task, "task-manifest-v1.schema.json")
    return task


def _resolve_task_graph(
    paths: BuildPaths, task_digest: str, schemas: SchemaRegistry
) -> tuple[dict[str, Any], str]:
    matches: list[tuple[dict[str, Any], str]] = []
    for graph_path in sorted((paths.repo_root / "build_control/graph").glob("*.json")):
        graph = _load_verified_json(graph_path)
        if graph.get("protocol") != "helios.build.graph-manifest/v1":
            continue
        schemas.validate(graph, "graph-manifest-v1.schema.json")
        for node in graph["nodes"]:
            if node["node_type"] == "BuildTask" and node["manifest_sha256"] == task_digest:
                matches.append((graph, node["node_id"]))
    if len(matches) != 1:
        raise ContractError("frozen BuildTask does not resolve to exactly one committed graph")
    return matches[0]


def _load_verified_json(path: Path, expected_digest: str | None = None) -> dict[str, Any]:
    payload = load_strict_json(path)
    content = canonical_json_bytes(payload)
    try:
        stored = path.read_bytes()
    except OSError as error:
        raise ContractError(f"cannot reread immutable JSON at {path}: {error}") from error
    if stored != content:
        raise ContractError(f"immutable JSON is not canonical at {path}")
    digest = sha256_hex(content)
    if expected_digest is not None and digest != expected_digest:
        raise ContractError(f"immutable JSON digest mismatch at {path}")
    return payload


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )
