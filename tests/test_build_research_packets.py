"""Contract and end-to-end tests for file-based ATHENA source packets."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from tools.helios_build.canonical import canonical_json_bytes, sha256_hex
from tools.helios_build.cli import main as cli_main
from tools.helios_build.errors import ContractError
from tools.helios_build.paths import BuildPaths
from tools.helios_build.research import export_research_request, import_source_packets
from tools.helios_build.schemas import SchemaRegistry
from tools.helios_build.source_registry import SourceRegistry, canonical_source_record
from tools.helios_build.store import ContentAddressedStore


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_NOTEBOOKS = [
    ("N01", "Takeoff methodology and drawing interpretation"),
    ("N02", "Duct, fittings, and SMACNA"),
    ("N03", "Hydronic, refrigerant, and condensate piping"),
    ("N04", "Equipment, schedules, and manufacturer literature"),
    ("N05", "Insulation, supports, seismic, and vibration"),
    ("N06", "NYC/NYS codes, public work, labor, and tax"),
    ("N07", "Pricing, procurement, and vendor intelligence"),
    ("N08", "OCR, computer vision, PDF/CAD, and AI models"),
    ("N09", "Estimating, bidding, proposals, and risk"),
    ("N10", "Project management, submittals, TAB, and closeout"),
    ("N11", "HELIOS software architecture, database, and agent engineering"),
]


def write_json(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))
    return path


def valid_source(
    source_id: str = "SRC-OFFICIAL-001",
    *,
    url: str = "https://example.test/official-guidance",
) -> dict[str, object]:
    return {
        "protocol": "helios.build.source-record/v1",
        "source_id": source_id,
        "identity_kind": "PUBLIC_URL",
        "original_source_url": url,
        "authorized_document_id": None,
        "publisher": "Example Public Authority",
        "title": "Official HVAC Guidance",
        "publication_date": "2026-01-15",
        "effective_date": "2026-02-01",
        "retrieval_date": "2026-09-01",
        "jurisdiction": "USA-NY-NYC",
        "data_class": "PUBLIC",
        "authority_class": "PRIMARY_PUBLIC",
        "license_use_notes": "Public citation metadata only; no source body stored.",
        "content_sha256": "3" * 64,
    }


def valid_request(
    manifest_sha256: str,
    profile_sha256: str,
    *,
    request_id: str = "REQ-N02-001",
    notebook_id: str = "N02",
) -> dict[str, object]:
    return {
        "protocol": "helios.build.research-request/v1",
        "request_id": request_id,
        "task_id": "div23-registry-research",
        "target_engine_component_id": "DIV23_V2_REGISTRY",
        "question": "Which primary sources define the requested bounded engine behavior?",
        "jurisdiction": "USA-NY-NYC",
        "unit_system": "US_CUSTOMARY",
        "data_class": "PUBLIC",
        "required_source_authority_class": "PRIMARY_PUBLIC",
        "target_notebook_id": notebook_id,
        "excluded_source_ids": [],
        "excluded_data_classes": ["SECRET", "PROJECT_CONFIDENTIAL"],
        "required_output_protocol": "helios.build.source-packet/v1",
        "priority": "HIGH",
        "deadline": "2026-09-03T17:00:00Z",
        "issued_at": "2026-09-01T18:00:00Z",
        "issuer": {"worker_id": "CODEX", "role": "CODEX_CONTROL"},
        "notebook_manifest_sha256": manifest_sha256,
        "athena_profile_sha256": profile_sha256,
    }


def valid_packet(
    request: dict[str, object],
    request_sha256: str,
    *,
    packet_id: str = "PACKET-N02-001",
    source: dict[str, object] | None = None,
    supersedes_packet_id: str | None = None,
) -> dict[str, object]:
    selected = copy.deepcopy(source if source is not None else valid_source())
    return {
        "protocol": "helios.build.source-packet/v1",
        "packet_id": packet_id,
        "request_id": request["request_id"],
        "request_sha256": request_sha256,
        "supersedes_packet_id": supersedes_packet_id,
        "notebook_id": request["target_notebook_id"],
        "notebook_manifest_sha256": request["notebook_manifest_sha256"],
        "athena_profile_sha256": request["athena_profile_sha256"],
        "jurisdiction": request["jurisdiction"],
        "unit_system": "US_CUSTOMARY",
        "generated_at": "2026-09-01T18:10:00Z",
        "sources": [selected],
        "citations": [
            {
                "citation_id": f"CITE-{packet_id}",
                "source_id": selected["source_id"],
                "resolution": "RESOLVED",
                "locator": "Section 4.2, Table 4-1",
                "limitation": None,
            }
        ],
        "findings": [
            {
                "finding_id": f"FINDING-{packet_id}",
                "statement": "A bounded, cited research finding for builder evaluation.",
                "citation_ids": [f"CITE-{packet_id}"],
                "applicability": "NYC work when the cited source and project scope apply.",
                "confidence": "HIGH",
                "proposed_engine_implication": "Candidate input for a later reviewed engine definition.",
            }
        ],
        "conflicts": [],
        "limitations": ["Original-source verification is outside R1A."],
        "unanswered_questions": ["Does the project specification impose a stricter requirement?"],
        "synthesis_provenance": {
            "provider": "ATHENA_NOTEBOOKLM",
            "query_sha256": "4" * 64,
            "response_sha256": "5" * 64,
            "external_evidence_sha256": "6" * 64,
            "observed_at": "2026-09-01T18:10:00Z",
        },
    }


class ResearchFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        self.state = Path(self.temporary.name) / "state"
        self.root.mkdir()
        self.state.mkdir(mode=0o700)
        shutil.copy(ROOT / "pyproject.toml", self.root / "pyproject.toml")
        shutil.copytree(ROOT / "build_control" / "schemas", self.root / "build_control" / "schemas")
        shutil.copytree(
            ROOT / "build_control" / "worker_profiles",
            self.root / "build_control" / "worker_profiles",
        )
        shutil.copytree(
            ROOT / "build_control" / "source_registry",
            self.root / "build_control" / "source_registry",
        )
        (self.root / "build_control" / "tasks").mkdir(parents=True)
        (self.root / "build_control" / "graph").mkdir(parents=True)
        self.paths = BuildPaths(self.root, self.state)
        manifest = (self.root / "build_control/source_registry/notebooks.v1.json").read_bytes()
        profile = (self.root / "build_control/worker_profiles/athena.v1.json").read_bytes()
        self.manifest_sha = sha256_hex(manifest)
        self.profile_sha = sha256_hex(profile)

    def export(
        self,
        *,
        request_id: str = "REQ-N02-001",
        notebook_id: str = "N02",
    ) -> tuple[dict[str, object], str]:
        request = valid_request(
            self.manifest_sha,
            self.profile_sha,
            request_id=request_id,
            notebook_id=notebook_id,
        )
        stored = export_research_request(
            self.paths, write_json(Path(self.temporary.name) / f"{request_id}.json", request)
        )
        return request, stored.sha256


class ResearchPacketTests(ResearchFixture):
    def test_direct_registry_import_cannot_bypass_cross_object_bindings(self) -> None:
        request, request_sha = self.export()
        packet = valid_packet(request, request_sha)
        packet["notebook_id"] = "N03"
        registry = SourceRegistry(self.paths)
        self.assertFalse(hasattr(registry, "plan_import"))
        self.assertFalse(hasattr(registry, "publish_import"))
        with self.assertRaisesRegex(ContractError, "notebook"):
            registry.import_packets(
                [packet], [sha256_hex(canonical_json_bytes(packet))]
            )
        self.assertFalse((self.root / "build_control/source_registry/imports.jsonl").exists())

    def test_registry_replay_revalidates_request_manifest_and_profile_bindings(self) -> None:
        request, request_sha = self.export()
        packet = valid_packet(request, request_sha)
        import_source_packets(
            self.paths, [write_json(Path(self.temporary.name) / "packet.json", packet)]
        )
        request_path = (
            self.root
            / "build_control/tasks/research_requests/sha256"
            / request_sha[:2]
            / f"{request_sha}.json"
        )
        tampered = copy.deepcopy(request)
        tampered["question"] = "Tampered after import."
        write_json(request_path, tampered)
        with self.assertRaisesRegex(ContractError, "digest|canonical"):
            SourceRegistry(self.paths).snapshot()

    def test_registry_replay_fails_when_bound_manifest_or_profile_is_missing(self) -> None:
        request, request_sha = self.export()
        packet = valid_packet(request, request_sha)
        import_source_packets(
            self.paths, [write_json(Path(self.temporary.name) / "packet.json", packet)]
        )
        profile = self.root / "build_control/worker_profiles/athena.v1.json"
        profile_bytes = profile.read_bytes()
        profile.unlink()
        with self.assertRaisesRegex(ContractError, "profile|read"):
            SourceRegistry(self.paths).snapshot()
        profile.write_bytes(profile_bytes)
        manifest = self.root / "build_control/source_registry/notebooks.v1.json"
        manifest.unlink()
        with self.assertRaisesRegex(ContractError, "manifest"):
            SourceRegistry(self.paths).snapshot()

    def test_one_request_notebook_lineage_rejects_parallel_roots_and_stale_heads(self) -> None:
        request, request_sha = self.export()
        root = valid_packet(request, request_sha)
        import_source_packets(
            self.paths, [write_json(Path(self.temporary.name) / "root.json", root)]
        )
        parallel = valid_packet(request, request_sha, packet_id="PACKET-N02-PARALLEL")
        with self.assertRaisesRegex(ContractError, "supersede|lineage|head"):
            import_source_packets(
                self.paths,
                [write_json(Path(self.temporary.name) / "parallel.json", parallel)],
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
        stale = valid_packet(
            request,
            request_sha,
            packet_id="PACKET-N02-STALE",
            supersedes_packet_id="PACKET-N02-001",
        )
        with self.assertRaisesRegex(ContractError, "head|fork"):
            import_source_packets(
                self.paths, [write_json(Path(self.temporary.name) / "stale.json", stale)]
            )

        request_n03, request_n03_sha = self.export(
            request_id="REQ-N03-TOPOLOGY", notebook_id="N03"
        )
        lexical_root = valid_packet(
            request_n03,
            request_n03_sha,
            packet_id="PACKET-Z-ROOT",
        )
        lexical_successor = valid_packet(
            request_n03,
            request_n03_sha,
            packet_id="PACKET-A-SUCCESSOR",
            supersedes_packet_id="PACKET-Z-ROOT",
        )
        import_source_packets(
            self.paths,
            [
                write_json(Path(self.temporary.name) / "lexical-successor.json", lexical_successor),
                write_json(Path(self.temporary.name) / "lexical-root.json", lexical_root),
            ],
        )
        snapshot = SourceRegistry(self.paths).snapshot()
        self.assertEqual(
            snapshot.head_packet_id_by_lineage[(request_n03_sha, "N03")],
            "PACKET-A-SUCCESSOR",
        )

    def test_concurrent_changed_requests_with_one_id_publish_exactly_one_identity(self) -> None:
        first = valid_request(self.manifest_sha, self.profile_sha, request_id="REQ-RACE-001")
        second = copy.deepcopy(first)
        second["question"] = "A different immutable research question."
        first_path = write_json(Path(self.temporary.name) / "request-race-a.json", first)
        second_path = write_json(Path(self.temporary.name) / "request-race-b.json", second)

        def export(path: Path) -> str:
            try:
                return export_research_request(self.paths, path).sha256
            except ContractError as error:
                return f"ERROR:{error}"

        original_put_json = ContentAddressedStore.put_json
        publication_barrier = threading.Barrier(2)

        def synchronized_put_json(
            store: ContentAddressedStore,
            collection: str,
            payload: dict[str, object],
        ):
            if collection == "research_requests":
                try:
                    publication_barrier.wait(timeout=0.5)
                except threading.BrokenBarrierError:
                    pass
            return original_put_json(store, collection, payload)

        with mock.patch.object(ContentAddressedStore, "put_json", synchronized_put_json):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(export, (first_path, second_path)))
        self.assertEqual(sum(not item.startswith("ERROR:") for item in results), 1)
        self.assertTrue(any("request_id" in item for item in results if item.startswith("ERROR:")))
        published = tuple(
            (self.root / "build_control/tasks/research_requests/sha256").glob("*/*.json")
        )
        self.assertEqual(len(published), 1)

    def test_source_dates_are_real_iso_calendar_dates(self) -> None:
        schemas = SchemaRegistry(self.root)
        leap_day = valid_source()
        leap_day["publication_date"] = "2024-02-29"
        leap_day["effective_date"] = "2024-02-29"
        leap_day["retrieval_date"] = "2024-02-29"
        self.assertEqual(
            canonical_source_record(leap_day, schemas)["retrieval_date"], "2024-02-29"
        )
        for field, value in (
            ("publication_date", "2025-02-29"),
            ("effective_date", "2026-04-31"),
            ("retrieval_date", "2026-13-01"),
            ("retrieval_date", "0000-01-01"),
        ):
            source = valid_source()
            source[field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaisesRegex(ContractError, field):
                    schemas.validate(source, "source-record-v1.schema.json")
                with self.assertRaisesRegex(ContractError, field):
                    canonical_source_record(source, schemas)

    def test_contracts_manifest_and_profile_are_strict_and_bounded(self) -> None:
        schemas = SchemaRegistry(ROOT)
        manifest_path = ROOT / "build_control/source_registry/notebooks.v1.json"
        profile_path = ROOT / "build_control/worker_profiles/athena.v1.json"
        instructions_path = ROOT / "build_control/worker_profiles/athena.instructions.v1.md"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        schemas.validate(manifest, "notebook-manifest-v1.schema.json")
        schemas.validate(profile, "athena-worker-profile-v1.schema.json")
        self.assertEqual(
            [(item["notebook_id"], item["name"]) for item in manifest["notebooks"]],
            EXPECTED_NOTEBOOKS,
        )
        self.assertEqual(profile["allowed_output_protocols"], ["helios.build.source-packet/v1"])
        self.assertEqual(
            profile["instructions_sha256"], hashlib.sha256(instructions_path.read_bytes()).hexdigest()
        )
        forbidden = set(profile["forbidden_capabilities"])
        self.assertTrue(
            {
                "AUTHOR_CODE",
                "WRITE_HELIOS_DATABASE",
                "AUTHOR_ENGINE_RULE",
                "CREATE_QUANTITY",
                "SET_PRICE",
                "APPROVE_ESTIMATE",
                "RELEASE_BID",
                "BROWSER_AUTOMATION",
            }
            <= forbidden
        )
        serialized = canonical_json_bytes(manifest) + canonical_json_bytes(profile)
        for forbidden_text in (b"notebook.google.com", b"provider_notebook_id", b"cookie", b"token"):
            self.assertNotIn(forbidden_text, serialized.lower())

    def test_valid_multi_notebook_batch_deduplicates_one_source_identity(self) -> None:
        request_a, request_sha_a = self.export()
        request_b, request_sha_b = self.export(request_id="REQ-N03-001", notebook_id="N03")
        packet_a = valid_packet(request_a, request_sha_a)
        packet_b = valid_packet(
            request_b,
            request_sha_b,
            packet_id="PACKET-N03-001",
        )
        result = import_source_packets(
            self.paths,
            [
                write_json(Path(self.temporary.name) / "packet-a.json", packet_a),
                write_json(Path(self.temporary.name) / "packet-b.json", packet_b),
            ],
        )
        self.assertEqual(result.packet_ids, ("PACKET-N02-001", "PACKET-N03-001"))
        self.assertEqual(result.source_ids, ("SRC-OFFICIAL-001",))
        self.assertEqual(result.notebook_memberships["SRC-OFFICIAL-001"], ("N02", "N03"))
        source_files = tuple((self.root / "build_control/source_registry/sources/sha256").glob("*/*.json"))
        self.assertEqual(len(source_files), 1)
        self.assertTrue((self.root / "build_control/source_registry/imports.jsonl").is_file())

    def test_invalid_member_prevents_the_entire_batch_from_becoming_authoritative(self) -> None:
        request_a, request_sha_a = self.export()
        request_b, request_sha_b = self.export(request_id="REQ-N03-001", notebook_id="N03")
        packet_a = valid_packet(request_a, request_sha_a)
        packet_b = valid_packet(request_b, request_sha_b, packet_id="PACKET-N03-001")
        packet_b["notebook_id"] = "N04"
        with self.assertRaisesRegex(ContractError, "notebook"):
            import_source_packets(
                self.paths,
                [
                    write_json(Path(self.temporary.name) / "good.json", packet_a),
                    write_json(Path(self.temporary.name) / "bad.json", packet_b),
                ],
            )
        self.assertFalse((self.root / "build_control/source_registry/imports.jsonl").exists())
        self.assertFalse((self.root / "build_control/source_packets/sha256").exists())
        self.assertFalse((self.root / "build_control/source_registry/sources/sha256").exists())

    def test_packet_and_source_identity_are_immutable_with_explicit_successors(self) -> None:
        request, request_sha = self.export()
        packet = valid_packet(request, request_sha)
        path = write_json(Path(self.temporary.name) / "packet.json", packet)
        first = import_source_packets(self.paths, [path])
        replay = import_source_packets(self.paths, [path])
        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(first.packet_sha256s, replay.packet_sha256s)

        changed = copy.deepcopy(packet)
        changed["findings"][0]["statement"] = "Changed bytes under the same packet identity."
        with self.assertRaisesRegex(ContractError, "packet_id"):
            import_source_packets(
                self.paths, [write_json(Path(self.temporary.name) / "changed.json", changed)]
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
        fork = copy.deepcopy(successor)
        fork["packet_id"] = "PACKET-N02-003"
        with self.assertRaisesRegex(ContractError, "supersed|successor|fork|head"):
            import_source_packets(
                self.paths, [write_json(Path(self.temporary.name) / "fork.json", fork)]
            )

        duplicate_source = valid_packet(
            request,
            request_sha,
            packet_id="PACKET-N02-004",
            source=valid_source("SRC-OFFICIAL-OTHER", url="HTTPS://EXAMPLE.TEST:443/official-guidance#x"),
            supersedes_packet_id="PACKET-N02-002",
        )
        with self.assertRaisesRegex(ContractError, "canonical source|source identity"):
            import_source_packets(
                self.paths,
                [write_json(Path(self.temporary.name) / "duplicate-source.json", duplicate_source)],
            )

    def test_packet_binding_citations_and_sanitized_shape_fail_closed(self) -> None:
        request, request_sha = self.export()
        cases: list[tuple[str, dict[str, object], str]] = []
        wrong_request = valid_packet(request, "0" * 64)
        cases.append(("request hash", wrong_request, "request"))
        wrong_units = valid_packet(request, request_sha)
        wrong_units["unit_system"] = "METRIC"
        cases.append(("units", wrong_units, "unit_system"))
        missing_locator = valid_packet(request, request_sha)
        missing_locator["citations"][0]["locator"] = None
        cases.append(("locator", missing_locator, "locator"))
        injected = valid_packet(request, request_sha)
        injected["code"] = "print('not permitted')"
        cases.append(("code", injected, "additional properties|code"))
        for label, packet, message in cases:
            with self.subTest(label=label), self.assertRaisesRegex(ContractError, message):
                import_source_packets(
                    self.paths,
                    [write_json(Path(self.temporary.name) / f"invalid-{label}.json", packet)],
                )
        self.assertFalse((self.root / "build_control/source_registry/imports.jsonl").exists())

    def test_research_cli_registers_the_three_finite_file_commands(self) -> None:
        request = valid_request(self.manifest_sha, self.profile_sha)
        request_path = write_json(Path(self.temporary.name) / "request-cli.json", request)
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli_main(
                [
                    "--repo-root",
                    str(self.root),
                    "--state-root",
                    str(self.state),
                    "research",
                    "request",
                    "export",
                    str(request_path),
                ]
            )
        self.assertEqual((code, stderr.getvalue()), (0, ""))
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "EXPORTED")
        self.assertEqual(stdout.getvalue().count("\n"), 1)
        self.assertNotIn("provider_command", payload)


if __name__ == "__main__":
    unittest.main()
