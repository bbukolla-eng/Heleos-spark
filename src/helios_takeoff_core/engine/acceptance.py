"""Finite blank-database acceptance for the HELIOS P1B engine foundation."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from ..agentic.acceptance import P0_BID_IMPACTING_TABLES
from ..db import Database
from ..errors import ConflictError
from .compiler import compile_domain_pack
from .evaluator import evaluate_subject, resolve_assembly
from .repository import EngineRepository


P1A_EXECUTION_TABLES = (
    "agent_adapter_revisions",
    "agent_artifacts",
    "agent_execution_attempt_artifacts",
    "agent_execution_attempts",
    "agent_jobs",
    "agent_work_item_dependencies",
    "agent_work_item_events",
    "agent_work_items",
    "agent_worker_adapter_assignments",
    "agent_worker_availability_events",
    "agent_worker_capabilities",
    "agent_workers",
    "agent_workflow_runs",
)

DEMO_DOMAIN_PACK: dict[str, Any] = {
    "protocol": "helios.p1b.domain-pack/v1",
    "pack_code": "DIV23-ENGINE-DEMO",
    "version": "1.0.0",
    "title": "NON-AUTHORITATIVE DEMO engine definitions",
    "jurisdiction": "DEMO-ONLY",
    "provenance": [
        {"kind": "PUBLIC_URL", "reference": "https://example.test/non-authoritative-demo"}
    ],
    "catalog": [
        {"code": "SYS-AIR", "type": "SYSTEM", "title": "Demo air system", "uom": "EA"},
        {"code": "ASM-ROOT", "type": "ASSEMBLY", "title": "Demo root assembly", "uom": "EA"},
        {"code": "ASM-NESTED", "type": "ASSEMBLY", "title": "Demo nested assembly", "uom": "EA"},
        {"code": "CMP-SHARED", "type": "COMPONENT", "title": "Demo component", "uom": "EA"},
        {"code": "MAT-SHEET", "type": "MATERIAL", "title": "Demo sheet metal", "uom": "SF"},
    ],
    "relations": [
        {"from_code": "CMP-SHARED", "relation": "PART_OF", "to_code": "SYS-AIR"},
        {"from_code": "ASM-ROOT", "relation": "COMPOSED_OF", "to_code": "ASM-NESTED"},
        {"from_code": "CMP-SHARED", "relation": "USES_MATERIAL", "to_code": "MAT-SHEET"},
        {"from_code": "ASM-NESTED", "relation": "REQUIRES", "to_code": "CMP-SHARED"},
    ],
    "rules": [
        {
            "code": "Z-BLOCKED-RULE",
            "type": "CLASSIFY",
            "subject_code": "CMP-SHARED",
            "required_observations": ["MODEL"],
            "required_evidence": ["SCHEDULE"],
            "output_claim_type": "EQUIPMENT_CLASS",
        },
        {
            "code": "A-READY-RULE",
            "type": "MEASURE",
            "subject_code": "CMP-SHARED",
            "required_observations": ["COUNT"],
            "required_evidence": ["MECHANICAL_PLAN"],
            "output_claim_type": "EQUIPMENT_COUNT",
            "output_uom": "EA",
        },
    ],
}


def _table_counts(database: Database, tables: tuple[str, ...]) -> dict[str, int]:
    with database.connection() as connection:
        return {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in tables
        }


def _reordered_demo_pack() -> dict[str, Any]:
    reordered = deepcopy(DEMO_DOMAIN_PACK)
    for field in ("provenance", "catalog", "relations", "rules"):
        reordered[field].reverse()
    for rule in reordered["rules"]:
        rule["required_observations"].reverse()
        rule["required_evidence"].reverse()
    return reordered


def build_engine_foundation_acceptance(work_root: Path) -> dict[str, object]:
    """Run one real, finite, non-authoritative engine proof on a new database."""
    work_root = Path(work_root)
    work_root.mkdir(parents=True, exist_ok=True)
    database_path = work_root / "helios-p1b-engine.sqlite3"
    if database_path.exists():
        raise FileExistsError(f"refusing to overwrite existing database: {database_path}")

    compiled = compile_domain_pack(deepcopy(DEMO_DOMAIN_PACK))
    reordered = compile_domain_pack(_reordered_demo_pack())
    if compiled.sha256 != reordered.sha256 or compiled.canonical_bytes != reordered.canonical_bytes:
        raise RuntimeError("domain-pack compilation was not deterministic across input order")

    database = Database(database_path)
    database.initialize()
    repository = EngineRepository(database)
    p0_before = _table_counts(database, P0_BID_IMPACTING_TABLES)
    p1a_before = _table_counts(database, P1A_EXECUTION_TABLES)

    pack_id = repository.import_domain_pack(compiled)
    replay_pack_id = repository.import_domain_pack(reordered)
    queried_pack = repository.get_domain_pack(
        pack_code="DIV23-ENGINE-DEMO", version="1.0.0"
    )
    item = repository.get_catalog_item(
        pack_code="DIV23-ENGINE-DEMO", version="1.0.0", item_code="CMP-SHARED"
    )
    relations = repository.list_catalog_relations(
        pack_code="DIV23-ENGINE-DEMO", version="1.0.0", item_code="CMP-SHARED"
    )
    stored_pack = compile_domain_pack(queried_pack)
    evaluation = evaluate_subject(
        stored_pack,
        subject_code="CMP-SHARED",
        observations={"COUNT": 2},
        evidence_kinds=["MECHANICAL_PLAN"],
    )
    assembly = resolve_assembly(stored_pack, item_code="ASM-ROOT")

    conflict_document = deepcopy(DEMO_DOMAIN_PACK)
    conflict_document["title"] = "Different immutable content"
    try:
        repository.import_domain_pack(compile_domain_pack(conflict_document))
    except ConflictError:
        conflict_rejected = True
    else:
        raise RuntimeError("conflicting same-version domain-pack content was accepted")

    p0_after = _table_counts(database, P0_BID_IMPACTING_TABLES)
    p1a_after = _table_counts(database, P1A_EXECUTION_TABLES)
    worker_attempt_delta = (
        p1a_after["agent_execution_attempts"] - p1a_before["agent_execution_attempts"]
    )
    if p0_before != p0_after:
        raise RuntimeError("P1B changed a P0 bid-impacting table")
    if p1a_before != p1a_after:
        raise RuntimeError("P1B changed a P1A execution table")
    if pack_id != replay_pack_id:
        raise RuntimeError("identical domain-pack replay did not preserve identity")
    expected_pack = json.loads(compiled.canonical_bytes)
    expected_item = {
        "code": "CMP-SHARED",
        "type": "COMPONENT",
        "title": "Demo component",
        "uom": "EA",
    }
    expected_relations = [
        {"from_code": "ASM-NESTED", "relation": "REQUIRES", "to_code": "CMP-SHARED"},
        {"from_code": "CMP-SHARED", "relation": "PART_OF", "to_code": "SYS-AIR"},
        {"from_code": "CMP-SHARED", "relation": "USES_MATERIAL", "to_code": "MAT-SHEET"},
    ]
    expected_evaluation = {
        "pack_code": "DIV23-ENGINE-DEMO",
        "version": "1.0.0",
        "sha256": compiled.sha256,
        "subject_code": "CMP-SHARED",
        "candidate_claims": [
            {
                "rule_code": "A-READY-RULE",
                "rule_type": "MEASURE",
                "subject_code": "CMP-SHARED",
                "output_claim_type": "EQUIPMENT_COUNT",
                "output_uom": "EA",
                "observations": {"COUNT": 2},
                "evidence_kinds": ["MECHANICAL_PLAN"],
            }
        ],
        "blocked_rules": [
            {
                "rule_code": "Z-BLOCKED-RULE",
                "rule_type": "CLASSIFY",
                "subject_code": "CMP-SHARED",
                "output_claim_type": "EQUIPMENT_CLASS",
                "missing_observations": ["MODEL"],
                "missing_evidence_kinds": ["SCHEDULE"],
            }
        ],
    }
    expected_assembly = {
        "pack_code": "DIV23-ENGINE-DEMO",
        "version": "1.0.0",
        "sha256": compiled.sha256,
        "item_code": "ASM-ROOT",
        "items": [
            {"code": "ASM-NESTED", "type": "ASSEMBLY", "title": "Demo nested assembly", "uom": "EA"},
            {"code": "ASM-ROOT", "type": "ASSEMBLY", "title": "Demo root assembly", "uom": "EA"},
            {"code": "CMP-SHARED", "type": "COMPONENT", "title": "Demo component", "uom": "EA"},
            {"code": "MAT-SHEET", "type": "MATERIAL", "title": "Demo sheet metal", "uom": "SF"},
        ],
        "relations": [
            {"from_code": "ASM-NESTED", "relation": "REQUIRES", "to_code": "CMP-SHARED"},
            {"from_code": "ASM-ROOT", "relation": "COMPOSED_OF", "to_code": "ASM-NESTED"},
            {"from_code": "CMP-SHARED", "relation": "USES_MATERIAL", "to_code": "MAT-SHEET"},
        ],
    }
    exact_results = (
        (queried_pack, expected_pack, "stored canonical pack"),
        (item, expected_item, "catalog query"),
        (relations, expected_relations, "relation query"),
        (evaluation, expected_evaluation, "rule evaluation"),
        (assembly, expected_assembly, "assembly resolution"),
    )
    for actual, expected, name in exact_results:
        if actual != expected:
            raise RuntimeError(f"{name} did not match the exact acceptance contract")
    if worker_attempt_delta != 0:
        raise RuntimeError("engine acceptance unexpectedly executed a worker attempt")

    return {
        "acceptance": "P1B_ENGINE_FOUNDATION_SUCCEEDED",
        "database": str(database_path),
        "fixture": {
            "authority": "NON_AUTHORITATIVE_DEMO",
            "pack_code": "DIV23-ENGINE-DEMO",
            "version": "1.0.0",
        },
        "compile": {"deterministic": True, "sha256": compiled.sha256},
        "import": {
            "idempotent_replay": True,
            "pack_id": pack_id,
            "replay_pack_id": replay_pack_id,
            "same_version_conflict_rejected": conflict_rejected,
        },
        "query": {"pack": queried_pack, "item": item, "relations": relations},
        "evaluation": evaluation,
        "assembly": assembly,
        "authority_tables": {
            "p0_bid_impacting": {
                "before": p0_before,
                "after": p0_after,
                "unchanged": p0_before == p0_after,
            },
            "p1a_execution": {
                "before": p1a_before,
                "after": p1a_after,
                "unchanged": p1a_before == p1a_after,
            },
        },
        "execution": {
            "worker_attempts": {
                "before": p1a_before["agent_execution_attempts"],
                "after": p1a_after["agent_execution_attempts"],
                "delta": worker_attempt_delta,
            },
        },
        "scope": {
            "accepted": "deterministic reusable engine definitions and pure evaluation only",
            "not_accepted": [
                "project drawing ingestion",
                "project quantities",
                "pricing",
                "approval or bid release",
                "provider or worker execution",
                "background processing",
            ],
        },
    }
