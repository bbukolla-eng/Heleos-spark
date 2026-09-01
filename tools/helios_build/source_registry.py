"""Canonical, append-only source identity registry for build-time research."""

from __future__ import annotations

import copy
import fcntl
import os
import stat
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Mapping, Sequence
from urllib.parse import SplitResult, urlsplit, urlunsplit
from contextlib import contextmanager

from tools.helios_build.canonical import canonical_json_bytes, load_strict_json, sha256_hex
from tools.helios_build.doctor import _open_private_child_directory, _open_secure_state_root
from tools.helios_build.errors import ContractError
from tools.helios_build.ledger import append_event, replay_events
from tools.helios_build.paths import BuildPaths
from tools.helios_build.schemas import SchemaRegistry
from tools.helios_build.store import ContentAddressedStore


IMPORT_PROTOCOL = "helios.build.source-packet-import/v1"


@dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    """Verified projection of the authoritative import-event chain."""

    packets_by_id: Mapping[str, Mapping[str, Any]]
    packet_ids_by_sha256: Mapping[str, str]
    successor_by_packet_id: Mapping[str, str]
    head_packet_id_by_lineage: Mapping[tuple[str, str], str]
    source_sha256_by_id: Mapping[str, str]
    source_id_by_identity: Mapping[str, str]
    source_id_by_content_sha256: Mapping[str, str]
    memberships_by_source_id: Mapping[str, tuple[str, ...]]
    packet_ids_by_source_id: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class _ImportPlan:
    """Fully validated prospective import with no durable side effects."""

    packet_documents: tuple[dict[str, Any], ...]
    packet_sha256s: tuple[str, ...]
    source_documents: tuple[dict[str, Any], ...]
    source_sha256s: tuple[str, ...]
    event: dict[str, Any] | None
    replayed: bool
    memberships: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class RegistryImportResult:
    """Result from the sole locked and authoritative packet-import boundary."""

    import_event_sha256: str | None
    replayed: bool
    snapshot: RegistrySnapshot


class SourceRegistry:
    """Validate and publish canonical source records plus packet memberships."""

    def __init__(self, paths: BuildPaths, schemas: SchemaRegistry | None = None) -> None:
        self.paths = paths
        self.schemas = schemas if schemas is not None else SchemaRegistry(paths.repo_root)
        self.control_root = paths.repo_root / "build_control"
        self.registry_root = self.control_root / "source_registry"
        self.imports_path = self.registry_root / "imports.jsonl"

    def snapshot(self) -> RegistrySnapshot:
        """Replay and verify the complete authoritative registry index."""
        packets_by_id: dict[str, Mapping[str, Any]] = {}
        packet_ids_by_sha256: dict[str, str] = {}
        successor_by_packet_id: dict[str, str] = {}
        head_packet_id_by_lineage: dict[tuple[str, str], str] = {}
        source_sha256_by_id: dict[str, str] = {}
        source_id_by_identity: dict[str, str] = {}
        source_id_by_content: dict[str, str] = {}
        memberships: dict[str, set[str]] = {}
        packet_memberships: dict[str, set[str]] = {}

        for event in replay_events(self.imports_path):
            self._validate_event_shape(event)
            packet_entries = event["packets"]
            source_entries = event["sources"]
            assert isinstance(packet_entries, list) and isinstance(source_entries, list)
            batch_body = {
                "packet_sha256s": sorted(item["packet_sha256"] for item in packet_entries)
            }
            if event["batch_sha256"] != sha256_hex(canonical_json_bytes(batch_body)):
                raise ContractError("source import event has a batch digest mismatch")

            event_packets: dict[str, dict[str, Any]] = {}
            for entry in packet_entries:
                packet = self._load_packet_object(entry["packet_sha256"])
                validate_packet_bindings(self.paths, packet, self.schemas)
                if packet["packet_id"] != entry["packet_id"]:
                    raise ContractError("source import event packet identity mismatch")
                expected = {
                    "packet_id": packet["packet_id"],
                    "packet_sha256": entry["packet_sha256"],
                    "request_id": packet["request_id"],
                    "request_sha256": packet["request_sha256"],
                    "notebook_id": packet["notebook_id"],
                    "supersedes_packet_id": packet["supersedes_packet_id"],
                }
                if entry != expected:
                    raise ContractError("source import event packet projection mismatch")
                packet_id = packet["packet_id"]
                prior = packets_by_id.get(packet_id)
                if prior is not None:
                    raise ContractError(f"duplicate authoritative packet_id event: {packet_id}")
                digest = entry["packet_sha256"]
                if digest in packet_ids_by_sha256:
                    raise ContractError("one packet digest is indexed under multiple packet identities")
                predecessor = packet["supersedes_packet_id"]
                lineage = _lineage_key(packet)
                if predecessor is not None:
                    if predecessor not in packets_by_id:
                        raise ContractError("packet successor event precedes its predecessor")
                    if predecessor in successor_by_packet_id:
                        raise ContractError("packet predecessor has a forked successor")
                    predecessor_packet = packets_by_id[predecessor]
                    self._validate_successor(packet, predecessor_packet)
                    if head_packet_id_by_lineage.get(lineage) != predecessor:
                        raise ContractError(
                            "source packet successor does not supersede the active lineage head"
                        )
                    successor_by_packet_id[predecessor] = packet_id
                elif lineage in head_packet_id_by_lineage:
                    raise ContractError(
                        "source packet lineage contains more than one root for one request and notebook"
                    )
                packets_by_id[packet_id] = MappingProxyType(dict(packet))
                packet_ids_by_sha256[digest] = packet_id
                head_packet_id_by_lineage[lineage] = packet_id
                event_packets[packet_id] = packet

            projected_sources: set[str] = set()
            for entry in source_entries:
                source = self._load_source_object(entry["source_record_sha256"])
                source_id = source["source_id"]
                if source_id != entry["source_id"]:
                    raise ContractError("source import event source identity mismatch")
                if source_id in projected_sources:
                    raise ContractError("source import event repeats a source projection")
                projected_sources.add(source_id)
                digest = entry["source_record_sha256"]
                identity = _identity_key(source)
                existing_digest = source_sha256_by_id.get(source_id)
                if existing_digest is not None and existing_digest != digest:
                    raise ContractError(f"source_id {source_id} changed immutable metadata")
                existing_id = source_id_by_identity.get(identity)
                if existing_id is not None and existing_id != source_id:
                    raise ContractError(
                        f"canonical source identity is shared by {existing_id} and {source_id}"
                    )
                content_digest = source["content_sha256"]
                if content_digest is not None:
                    content_owner = source_id_by_content.get(content_digest)
                    if content_owner is not None and content_owner != source_id:
                        raise ContractError(
                            f"content digest is shared by source IDs {content_owner} and {source_id}"
                        )
                    source_id_by_content[content_digest] = source_id
                source_sha256_by_id[source_id] = digest
                source_id_by_identity[identity] = source_id

                entry_packet_ids = tuple(entry["packet_ids"])
                entry_notebooks = tuple(entry["notebook_ids"])
                expected_notebooks: set[str] = set()
                for packet_id in entry_packet_ids:
                    packet = event_packets.get(packet_id)
                    if packet is None:
                        raise ContractError("source import event references a packet outside its batch")
                    packet_source_ids = {item["source_id"] for item in packet["sources"]}
                    if source_id not in packet_source_ids:
                        raise ContractError("source import event packet membership mismatch")
                    expected_notebooks.add(packet["notebook_id"])
                if tuple(sorted(expected_notebooks)) != entry_notebooks:
                    raise ContractError("source import event notebook membership mismatch")
                memberships.setdefault(source_id, set()).update(entry_notebooks)
                packet_memberships.setdefault(source_id, set()).update(entry_packet_ids)

            expected_sources = {
                item["source_id"] for packet in event_packets.values() for item in packet["sources"]
            }
            if projected_sources != expected_sources:
                raise ContractError("source import event omits or adds source projections")

        return RegistrySnapshot(
            packets_by_id=MappingProxyType(packets_by_id),
            packet_ids_by_sha256=MappingProxyType(packet_ids_by_sha256),
            successor_by_packet_id=MappingProxyType(successor_by_packet_id),
            head_packet_id_by_lineage=MappingProxyType(head_packet_id_by_lineage),
            source_sha256_by_id=MappingProxyType(source_sha256_by_id),
            source_id_by_identity=MappingProxyType(source_id_by_identity),
            source_id_by_content_sha256=MappingProxyType(source_id_by_content),
            memberships_by_source_id=MappingProxyType(
                {key: tuple(sorted(value)) for key, value in memberships.items()}
            ),
            packet_ids_by_source_id=MappingProxyType(
                {key: tuple(sorted(value)) for key, value in packet_memberships.items()}
            ),
        )

    def import_packets(
        self,
        packets: Sequence[dict[str, Any]],
        packet_sha256s: Sequence[str],
    ) -> RegistryImportResult:
        """Validate bindings, publish objects, and index one batch under one shared lock."""
        with research_operation_lock(self.paths):
            if not packets or len(packets) != len(packet_sha256s):
                raise ContractError("source packet import requires a non-empty aligned batch")
            for packet, supplied_digest in zip(packets, packet_sha256s, strict=True):
                self.schemas.validate(packet, "source-packet-v1.schema.json")
                actual_digest = sha256_hex(canonical_json_bytes(packet))
                if supplied_digest != actual_digest:
                    raise ContractError("supplied SourcePacket digest does not match canonical bytes")
                validate_packet_bindings(self.paths, packet, self.schemas)
            plan = self._plan_import(packets, packet_sha256s)
            event_sha: str | None = None
            if plan.event is not None:
                source_store = ContentAddressedStore(self.registry_root)
                for source, expected_digest in zip(
                    plan.source_documents, plan.source_sha256s, strict=True
                ):
                    stored = source_store.put_json("sources", source)
                    if stored.sha256 != expected_digest:
                        raise ContractError("published source record digest mismatch")
                packet_store = ContentAddressedStore(self.control_root)
                for packet, expected_digest in zip(
                    plan.packet_documents, plan.packet_sha256s, strict=True
                ):
                    stored = packet_store.put_json("source_packets", packet)
                    if stored.sha256 != expected_digest:
                        raise ContractError("published source packet digest mismatch")
                event_sha = append_event(
                    self.imports_path, plan.event, self.paths.state_root
                )
            return RegistryImportResult(event_sha, plan.replayed, self.snapshot())

    def _plan_import(
        self,
        packets: Sequence[dict[str, Any]],
        packet_sha256s: Sequence[str],
    ) -> _ImportPlan:
        """Validate a complete packet batch without publishing any part of it."""
        if not packets or len(packets) != len(packet_sha256s):
            raise ContractError("source packet import requires a non-empty aligned batch")
        snapshot = self.snapshot()
        incoming_by_id: dict[str, tuple[dict[str, Any], str]] = {}
        for packet, digest in zip(packets, packet_sha256s, strict=True):
            packet_id = packet["packet_id"]
            prior = incoming_by_id.get(packet_id)
            if prior is not None and prior[1] != digest:
                raise ContractError(f"packet_id {packet_id} is reused with changed bytes")
            incoming_by_id[packet_id] = (packet, digest)

        prospective_packets: dict[str, Mapping[str, Any]] = dict(snapshot.packets_by_id)
        prospective_successors = dict(snapshot.successor_by_packet_id)
        prospective_heads = dict(snapshot.head_packet_id_by_lineage)
        new_packet_pairs: list[tuple[dict[str, Any], str]] = []
        pending = dict(incoming_by_id)
        while pending:
            progressed = False
            for packet_id in sorted(tuple(pending)):
                packet, digest = pending[packet_id]
                existing = snapshot.packets_by_id.get(packet_id)
                if existing is not None:
                    existing_digest = next(
                        key for key, value in snapshot.packet_ids_by_sha256.items() if value == packet_id
                    )
                    if existing_digest != digest:
                        raise ContractError(f"packet_id {packet_id} is reused with changed bytes")
                    del pending[packet_id]
                    progressed = True
                    continue
                predecessor_id = packet["supersedes_packet_id"]
                lineage = _lineage_key(packet)
                if predecessor_id == packet_id:
                    raise ContractError("a source packet cannot supersede itself")
                if predecessor_id is not None and predecessor_id not in prospective_packets:
                    continue
                if predecessor_id is not None:
                    if prospective_heads.get(lineage) != predecessor_id:
                        raise ContractError(
                            "new SourcePacket must explicitly supersede the active lineage head"
                        )
                    existing_successor = prospective_successors.get(predecessor_id)
                    if existing_successor is not None and existing_successor != packet_id:
                        raise ContractError(
                            f"packet predecessor {predecessor_id} has a forked successor"
                        )
                    self._validate_successor(packet, prospective_packets[predecessor_id])
                    prospective_successors[predecessor_id] = packet_id
                elif lineage in prospective_heads:
                    raise ContractError(
                        "parallel SourcePacket root is forbidden; supersede the active lineage head"
                    )
                prospective_packets[packet_id] = packet
                prospective_heads[lineage] = packet_id
                new_packet_pairs.append((packet, digest))
                del pending[packet_id]
                progressed = True
            if not progressed:
                missing = ", ".join(sorted(pending))
                raise ContractError(
                    f"source packet successor lineage is missing or cyclic for: {missing}"
                )

        source_by_id: dict[str, tuple[dict[str, Any], str]] = {}
        identity_to_id = dict(snapshot.source_id_by_identity)
        content_to_id = dict(snapshot.source_id_by_content_sha256)
        source_digest_by_id = dict(snapshot.source_sha256_by_id)
        new_memberships: dict[str, set[str]] = {}
        new_packet_memberships: dict[str, set[str]] = {}
        for packet, _ in new_packet_pairs:
            for raw_source in packet["sources"]:
                source = canonical_source_record(raw_source, self.schemas)
                digest = sha256_hex(canonical_json_bytes(source))
                source_id = source["source_id"]
                prior_digest = source_digest_by_id.get(source_id)
                if prior_digest is not None and prior_digest != digest:
                    raise ContractError(f"source_id {source_id} is reused with changed metadata")
                prior_batch = source_by_id.get(source_id)
                if prior_batch is not None and prior_batch[1] != digest:
                    raise ContractError(f"source_id {source_id} conflicts inside the import batch")
                identity = _identity_key(source)
                identity_owner = identity_to_id.get(identity)
                if identity_owner is not None and identity_owner != source_id:
                    raise ContractError(
                        f"canonical source identity already belongs to {identity_owner}, not {source_id}"
                    )
                content_digest = source["content_sha256"]
                if content_digest is not None:
                    content_owner = content_to_id.get(content_digest)
                    if content_owner is not None and content_owner != source_id:
                        raise ContractError(
                            f"source identity conflicts with content already owned by {content_owner}"
                        )
                    content_to_id[content_digest] = source_id
                source_by_id[source_id] = (source, digest)
                source_digest_by_id[source_id] = digest
                identity_to_id[identity] = source_id
                new_memberships.setdefault(source_id, set()).add(packet["notebook_id"])
                new_packet_memberships.setdefault(source_id, set()).add(packet["packet_id"])

        all_memberships = {
            key: set(value) for key, value in snapshot.memberships_by_source_id.items()
        }
        for source_id, notebooks in new_memberships.items():
            all_memberships.setdefault(source_id, set()).update(notebooks)

        if not new_packet_pairs:
            return _ImportPlan(
                packet_documents=tuple(packet for packet, _ in incoming_by_id.values()),
                packet_sha256s=tuple(digest for _, digest in incoming_by_id.values()),
                source_documents=(),
                source_sha256s=(),
                event=None,
                replayed=True,
                memberships=MappingProxyType(
                    {key: tuple(sorted(value)) for key, value in all_memberships.items()}
                ),
            )

        # Preserve the deterministic predecessor-before-successor order produced
        # by the pending-lineage walk. Sorting only by packet_id could place a
        # lexically earlier successor before its root and make valid batch replay
        # fail even though planning accepted the lineage.
        packet_entries = [
            {
                "packet_id": packet["packet_id"],
                "packet_sha256": digest,
                "request_id": packet["request_id"],
                "request_sha256": packet["request_sha256"],
                "notebook_id": packet["notebook_id"],
                "supersedes_packet_id": packet["supersedes_packet_id"],
            }
            for packet, digest in new_packet_pairs
        ]
        source_entries = [
            {
                "source_id": source_id,
                "source_record_sha256": source_by_id[source_id][1],
                "packet_ids": sorted(new_packet_memberships[source_id]),
                "notebook_ids": sorted(new_memberships[source_id]),
            }
            for source_id in sorted(new_memberships)
        ]
        event = {
            "protocol": IMPORT_PROTOCOL,
            "batch_sha256": sha256_hex(
                canonical_json_bytes(
                    {"packet_sha256s": sorted(item["packet_sha256"] for item in packet_entries)}
                )
            ),
            "packets": packet_entries,
            "sources": source_entries,
            "imported_at": _now(),
        }
        return _ImportPlan(
            packet_documents=tuple(packet for packet, _ in new_packet_pairs),
            packet_sha256s=tuple(digest for _, digest in new_packet_pairs),
            source_documents=tuple(source_by_id[key][0] for key in sorted(source_by_id)),
            source_sha256s=tuple(source_by_id[key][1] for key in sorted(source_by_id)),
            event=event,
            replayed=False,
            memberships=MappingProxyType(
                {key: tuple(sorted(value)) for key, value in all_memberships.items()}
            ),
        )

    def packet_by_digest(self, digest: str) -> dict[str, Any]:
        """Resolve one imported packet by exact digest."""
        snapshot = self.snapshot()
        if digest not in snapshot.packet_ids_by_sha256:
            raise ContractError(f"source packet digest is not authoritatively imported: {digest}")
        return self._load_packet_object(digest)

    def source_by_digest(self, digest: str) -> dict[str, Any]:
        """Resolve one indexed source record by exact digest."""
        snapshot = self.snapshot()
        if digest not in snapshot.source_sha256_by_id.values():
            raise ContractError(f"source record digest is not authoritatively indexed: {digest}")
        return self._load_source_object(digest)

    def _load_packet_object(self, digest: str) -> dict[str, Any]:
        return _load_cas_json(
            self.control_root / "source_packets", digest, self.schemas, "source-packet-v1.schema.json"
        )

    def _load_source_object(self, digest: str) -> dict[str, Any]:
        source = _load_cas_json(
            self.registry_root / "sources", digest, self.schemas, "source-record-v1.schema.json"
        )
        normalized = canonical_source_record(source, self.schemas)
        if normalized != source:
            raise ContractError("indexed source record is not identity-canonical")
        return source

    @staticmethod
    def _validate_successor(
        successor: Mapping[str, Any], predecessor: Mapping[str, Any]
    ) -> None:
        for field in (
            "request_id",
            "request_sha256",
            "notebook_id",
            "notebook_manifest_sha256",
            "athena_profile_sha256",
            "jurisdiction",
            "unit_system",
        ):
            if successor[field] != predecessor[field]:
                raise ContractError(f"source packet successor changes immutable {field}")

    @staticmethod
    def _validate_event_shape(event: Mapping[str, Any]) -> None:
        required = {
            "protocol",
            "batch_sha256",
            "packets",
            "sources",
            "imported_at",
            "sequence",
            "previous_event_sha256",
            "event_sha256",
        }
        if set(event) != required or event.get("protocol") != IMPORT_PROTOCOL:
            raise ContractError("source import event has an invalid shape or protocol")
        if not _is_digest(event.get("batch_sha256")):
            raise ContractError("source import event has an invalid batch digest")
        if not isinstance(event.get("packets"), list) or not event["packets"]:
            raise ContractError("source import event must index a non-empty packet batch")
        if not isinstance(event.get("sources"), list) or not event["sources"]:
            raise ContractError("source import event must index source projections")
        for entry in event["packets"]:
            if not isinstance(entry, dict) or set(entry) != {
                "packet_id",
                "packet_sha256",
                "request_id",
                "request_sha256",
                "notebook_id",
                "supersedes_packet_id",
            }:
                raise ContractError("source import event has an invalid packet entry")
            if not _is_digest(entry.get("packet_sha256")):
                raise ContractError("source import event has an invalid packet digest")
        for entry in event["sources"]:
            if not isinstance(entry, dict) or set(entry) != {
                "source_id",
                "source_record_sha256",
                "packet_ids",
                "notebook_ids",
            }:
                raise ContractError("source import event has an invalid source entry")
            if not _is_digest(entry.get("source_record_sha256")):
                raise ContractError("source import event has an invalid source digest")
            if (
                not isinstance(entry.get("packet_ids"), list)
                or not entry["packet_ids"]
                or entry["packet_ids"] != sorted(set(entry["packet_ids"]))
                or not isinstance(entry.get("notebook_ids"), list)
                or not entry["notebook_ids"]
                or entry["notebook_ids"] != sorted(set(entry["notebook_ids"]))
            ):
                raise ContractError("source import event memberships are not canonical")


def canonical_source_record(
    source: Mapping[str, Any], schemas: SchemaRegistry
) -> dict[str, Any]:
    """Normalize identity-bearing fields before hashing the canonical source."""
    if not isinstance(source, Mapping):
        raise ContractError("source record must be an object")
    normalized = copy.deepcopy(dict(source))
    schemas.validate(normalized, "source-record-v1.schema.json")
    for field in ("publication_date", "effective_date", "retrieval_date"):
        value = normalized[field]
        if value is not None:
            _validate_iso_calendar_date(field, value)
    if normalized["identity_kind"] == "PUBLIC_URL":
        normalized["original_source_url"] = _canonical_public_url(
            normalized["original_source_url"]
        )
    schemas.validate(normalized, "source-record-v1.schema.json")
    return normalized


def validate_packet_bindings(
    paths: BuildPaths,
    packet: dict[str, Any],
    schemas: SchemaRegistry,
) -> None:
    """Independently prove a packet's immutable request and policy bindings."""
    schemas.validate(packet, "source-packet-v1.schema.json")
    request = load_exported_request(paths, packet["request_sha256"], schemas)
    exact_pairs = (
        ("request_id", "request_id"),
        ("notebook_id", "target_notebook_id"),
        ("notebook_manifest_sha256", "notebook_manifest_sha256"),
        ("athena_profile_sha256", "athena_profile_sha256"),
        ("jurisdiction", "jurisdiction"),
        ("unit_system", "unit_system"),
    )
    for packet_field, request_field in exact_pairs:
        if packet[packet_field] != request[request_field]:
            raise ContractError(
                f"SourcePacket {packet_field} does not match its exported ResearchRequest"
            )
    if packet["request_sha256"] != sha256_hex(canonical_json_bytes(request)):
        raise ContractError("SourcePacket request_sha256 does not match its exported request")
    manifest = load_notebook_manifest(
        paths, packet["notebook_manifest_sha256"], schemas
    )
    profile = load_athena_profile(paths, packet["athena_profile_sha256"], schemas)
    notebook_ids = {item["notebook_id"] for item in manifest["notebooks"]}
    if packet["notebook_id"] not in notebook_ids:
        raise ContractError("SourcePacket notebook_id is not in its bound notebook manifest")
    if request["data_class"] not in profile["allowed_data_classes"]:
        raise ContractError("bound ResearchRequest data_class is not allowed by ATHENA")
    if request["data_class"] in request["excluded_data_classes"]:
        raise ContractError("bound ResearchRequest excludes its own data_class")
    if request["required_output_protocol"] not in profile["allowed_output_protocols"]:
        raise ContractError(
            "bound ResearchRequest output protocol is not allowed by ATHENA"
        )

    sources: dict[str, dict[str, Any]] = {}
    for raw_source in packet["sources"]:
        source = canonical_source_record(raw_source, schemas)
        source_id = source["source_id"]
        if source_id in sources:
            raise ContractError(f"SourcePacket repeats source_id {source_id}")
        if source_id in request["excluded_source_ids"]:
            raise ContractError(f"SourcePacket includes excluded source_id {source_id}")
        if source["data_class"] in request["excluded_data_classes"]:
            raise ContractError(
                f"SourcePacket includes excluded source data class {source['data_class']}"
            )
        sources[source_id] = source

    citations: dict[str, dict[str, Any]] = {}
    for citation in packet["citations"]:
        citation_id = citation["citation_id"]
        if citation_id in citations:
            raise ContractError(f"SourcePacket repeats citation_id {citation_id}")
        if citation["source_id"] not in sources:
            raise ContractError(f"citation {citation_id} references an unknown source")
        if citation["resolution"] == "RESOLVED" and citation["locator"] is None:
            raise ContractError(f"resolved citation {citation_id} requires a precise locator")
        if citation["resolution"] == "UNRESOLVED" and citation["limitation"] is None:
            raise ContractError(f"unresolved citation {citation_id} requires a limitation")
        citations[citation_id] = citation

    finding_ids: set[str] = set()
    supports_required_authority = False
    for finding in packet["findings"]:
        finding_id = finding["finding_id"]
        if finding_id in finding_ids:
            raise ContractError(f"SourcePacket repeats finding_id {finding_id}")
        finding_ids.add(finding_id)
        for citation_id in finding["citation_ids"]:
            citation = citations.get(citation_id)
            if citation is None:
                raise ContractError(f"finding {finding_id} references an unknown citation")
            if (
                citation["resolution"] == "RESOLVED"
                and sources[citation["source_id"]]["authority_class"]
                == request["required_source_authority_class"]
            ):
                supports_required_authority = True
    for conflict in packet["conflicts"]:
        for citation_id in conflict["citation_ids"]:
            if citation_id not in citations:
                raise ContractError("SourcePacket conflict references an unknown citation")
    if not supports_required_authority:
        raise ContractError(
            "SourcePacket findings lack a resolved citation of the requested authority class"
        )


def load_exported_request(
    paths: BuildPaths, digest: str, schemas: SchemaRegistry
) -> dict[str, Any]:
    """Load one canonical request object from its exact content digest."""
    if not _is_digest(digest):
        raise ContractError("ResearchRequest reference is not a lowercase SHA-256 digest")
    path = (
        paths.repo_root
        / "build_control/tasks/research_requests/sha256"
        / digest[:2]
        / f"{digest}.json"
    )
    request = _load_verified_json(path, expected_digest=digest)
    schemas.validate(request, "research-request-v1.schema.json")
    return request


def load_notebook_manifest(
    paths: BuildPaths, expected_digest: str, schemas: SchemaRegistry
) -> dict[str, Any]:
    """Resolve exactly one canonical logical-notebook manifest by digest."""
    matches: list[dict[str, Any]] = []
    for path in sorted(
        (paths.repo_root / "build_control/source_registry").glob("notebooks.v*.json")
    ):
        payload = _load_verified_json(path)
        schemas.validate(payload, "notebook-manifest-v1.schema.json")
        notebook_ids = [item["notebook_id"] for item in payload["notebooks"]]
        if len(notebook_ids) != len(set(notebook_ids)):
            raise ContractError(f"notebook manifest repeats a notebook_id: {path}")
        if sha256_hex(canonical_json_bytes(payload)) == expected_digest:
            matches.append(payload)
    if len(matches) != 1:
        raise ContractError("notebook_manifest_sha256 does not resolve exactly")
    return matches[0]


def load_athena_profile(
    paths: BuildPaths, expected_digest: str, schemas: SchemaRegistry
) -> dict[str, Any]:
    """Resolve the canonical profile and its exact instruction bytes."""
    profile_path = paths.repo_root / "build_control/worker_profiles/athena.v1.json"
    profile = _load_verified_json(profile_path)
    schemas.validate(profile, "athena-worker-profile-v1.schema.json")
    if sha256_hex(canonical_json_bytes(profile)) != expected_digest:
        raise ContractError("athena_profile_sha256 does not match the committed profile")
    instructions_path = paths.repo_root / "build_control/worker_profiles/athena.instructions.v1.md"
    try:
        instructions_digest = sha256_hex(instructions_path.read_bytes())
    except OSError as error:
        raise ContractError(f"cannot read ATHENA instructions: {error}") from error
    if instructions_digest != profile["instructions_sha256"]:
        raise ContractError("ATHENA profile instructions_sha256 is stale")
    return profile


@contextmanager
def research_operation_lock(paths: BuildPaths) -> Iterator[None]:
    """Serialize all request exports and packet imports with one POSIX lock."""
    state_descriptor: int | None = None
    locks_descriptor: int | None = None
    lock_descriptor: int | None = None
    try:
        state_descriptor = _open_secure_state_root(paths.state_root)
        try:
            os.mkdir("locks", mode=0o700, dir_fd=state_descriptor)
            os.fsync(state_descriptor)
        except FileExistsError:
            # Concurrent first-use creation is expected; the descriptor open
            # below independently verifies ownership, type, and exact mode.
            pass
        except OSError as error:
            raise ContractError(
                f"cannot create shared research lock directory: {error}"
            ) from error
        locks_descriptor = _open_private_child_directory(state_descriptor, "locks")
        if (
            not hasattr(os, "O_CLOEXEC")
            or not hasattr(os, "O_NOFOLLOW")
            or not hasattr(os, "O_NONBLOCK")
        ):
            raise ContractError(
                "shared research locking requires no-follow nonblocking file opens"
            )
        lock_descriptor = os.open(
            "research-control.lock",
            os.O_RDWR
            | os.O_CREAT
            | os.O_CLOEXEC
            | os.O_NOFOLLOW
            | os.O_NONBLOCK,
            0o600,
            dir_fd=locks_descriptor,
        )
        metadata = os.fstat(lock_descriptor)
        current_uid = os.getuid() if hasattr(os, "getuid") else None
        if (
            current_uid is None
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != current_uid
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ContractError(
                "shared research lock must be a current-owner regular file with mode 0600"
            )
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
    except BaseException as error:
        for descriptor in (lock_descriptor, locks_descriptor, state_descriptor):
            if descriptor is not None:
                os.close(descriptor)
        if isinstance(error, ContractError):
            raise
        if isinstance(error, OSError):
            raise ContractError(
                f"cannot acquire shared research operation lock: {error}"
            ) from error
        raise
    assert lock_descriptor is not None
    try:
        yield
    finally:
        release_error: OSError | None = None
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
        except OSError as error:
            release_error = error
        for descriptor in (lock_descriptor, locks_descriptor, state_descriptor):
            try:
                os.close(descriptor)
            except OSError as error:
                if release_error is None:
                    release_error = error
        if release_error is not None:
            raise ContractError(
                f"cannot release shared research operation lock: {release_error}"
            ) from release_error


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


def _lineage_key(packet: Mapping[str, Any]) -> tuple[str, str]:
    return str(packet["request_sha256"]), str(packet["notebook_id"])


def _validate_iso_calendar_date(field: str, value: object) -> None:
    if not isinstance(value, str):
        raise ContractError(f"{field} must be an ISO calendar date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ContractError(f"{field} is not a real ISO calendar date") from error
    if parsed.isoformat() != value:
        raise ContractError(f"{field} is not a canonical ISO calendar date")


def _canonical_public_url(value: object) -> str:
    if not isinstance(value, str):
        raise ContractError("original_source_url must be an absolute HTTP(S) URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ContractError("original_source_url has an invalid authority") from error
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ContractError("original_source_url must be an absolute credential-free HTTP(S) URL")
    hostname = parsed.hostname.lower()
    if (scheme, port) in {("http", 80), ("https", 443)}:
        port = None
    authority = hostname if port is None else f"{hostname}:{port}"
    return urlunsplit(SplitResult(scheme, authority, parsed.path or "/", parsed.query, ""))


def _identity_key(source: Mapping[str, Any]) -> str:
    if source["identity_kind"] == "PUBLIC_URL":
        return "PUBLIC_URL\0" + _canonical_public_url(source["original_source_url"])
    return (
        "AUTHORIZED_DOCUMENT\0"
        + str(source["publisher"])
        + "\0"
        + str(source["authorized_document_id"])
    )


def _load_cas_json(
    collection_root: Path,
    digest: str,
    schemas: SchemaRegistry,
    schema_name: str,
) -> dict[str, Any]:
    if not _is_digest(digest):
        raise ContractError("content-addressed reference is not a lowercase SHA-256 digest")
    path = collection_root / "sha256" / digest[:2] / f"{digest}.json"
    payload = load_strict_json(path)
    content = canonical_json_bytes(payload)
    if sha256_hex(content) != digest or path.read_bytes() != content:
        raise ContractError(f"content-addressed object is not canonical at {path}")
    schemas.validate(payload, schema_name)
    return payload


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
