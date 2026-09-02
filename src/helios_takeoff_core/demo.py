"""Runnable, fully governed P0 example for a single mechanical bid package."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .db import Database
from .repository import TakeoffRepository
from .services import TakeoffService


def build_demo_release(database_path: str | Path) -> dict[str, Any]:
    """Build a fresh P0 database through document evidence to a released bid.

    The example intentionally creates a new database only.  It refuses an existing
    path so a quick start cannot accidentally alter a real project record.
    """

    path = Path(database_path)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite an existing database: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)

    database = Database(path)
    database.initialize()
    repository = TakeoffRepository(database)
    service = TakeoffService(repository)

    project_id = repository.create_project(code="DEMO-001", name="HELIOS P0 Mechanical Demo")
    estimator_id = repository.create_actor(display_name="Demo Estimator", actor_type="USER")
    bidder_id = repository.create_actor(display_name="Demo Authorized Bidder", actor_type="USER")
    submitter_id = repository.create_actor(display_name="Demo Bid Submitter", actor_type="USER")
    for actor_id, role in (
        (estimator_id, "ESTIMATOR_REVIEWER"),
        (bidder_id, "AUTHORIZED_BIDDER"),
        (submitter_id, "SUBMITTER"),
    ):
        repository.grant_role(actor_id=actor_id, project_id=project_id, role=role)

    drawing_id = repository.register_document_revision(
        project_id=project_id,
        document_type="DRAWING",
        document_number="M-101",
        title="Supply Air Plan",
        sha256="a" * 64,
        issue_date="2026-08-27",
    )
    schedule_id = repository.register_document_revision(
        project_id=project_id,
        document_type="SCHEDULE",
        document_number="M-601",
        title="Mechanical Equipment Schedule",
        sha256="b" * 64,
        issue_date="2026-08-27",
    )
    revision_set_id = repository.create_revision_set(project_id=project_id, name="BID-01 Issued Set")
    repository.include_document_revision(revision_set_id, drawing_id)
    repository.include_document_revision(revision_set_id, schedule_id)
    repository.freeze_revision_set(revision_set_id)

    duct_evidence_id = repository.record_evidence_item(
        document_revision_id=drawing_id,
        sheet_id="M-101",
        page_number=1,
        geometry={"bbox": [124.0, 276.0, 480.0, 308.0]},
        content_sha256="c" * 64,
    )
    equipment_evidence_id = repository.record_evidence_item(
        document_revision_id=schedule_id,
        sheet_id="M-601",
        page_number=1,
        text_span="EF-1 | Exhaust Fan | 1 EA",
        content_sha256="d" * 64,
    )
    extraction_run_id = repository.start_extraction_run(
        project_id=project_id,
        plugin_id="helios.rule-based-demo",
        plugin_version="0.1.0",
        configuration_sha256="e" * 64,
        input_manifest_sha256="f" * 64,
    )
    duct_claim_id = repository.record_extraction_claim(
        extraction_run_id=extraction_run_id,
        evidence_item_ids=[duct_evidence_id],
        subject_kind="DUCT_SEGMENT",
        subject_key="M-101:SA-01",
        claim_type="MEASURED_LENGTH",
        payload={"size": "24x12", "system": "SUPPLY_AIR", "quantity": "42"},
        confidence="0.94",
    )
    equipment_claim_id = repository.record_extraction_claim(
        extraction_run_id=extraction_run_id,
        evidence_item_ids=[equipment_evidence_id],
        subject_kind="EQUIPMENT",
        subject_key="M-601:EF-1",
        claim_type="SCHEDULED_COUNT",
        payload={"tag": "EF-1", "quantity": "1"},
        confidence="0.99",
    )

    duct_assertion_id = service.create_quantity_assertion(
        revision_set_id=revision_set_id,
        subject_kind="DUCT_SEGMENT",
        subject_key="M-101:SA-01",
        uom="LF",
        quantity="42",
        scope_state="NEW",
        evidence_allocations=[
            {
                "evidence_item_id": duct_evidence_id,
                "claim_id": duct_claim_id,
                "allocation_quantity": "42",
            }
        ],
    )
    equipment_assertion_id = service.create_quantity_assertion(
        revision_set_id=revision_set_id,
        subject_kind="EQUIPMENT",
        subject_key="M-601:EF-1",
        uom="EA",
        quantity="1",
        scope_state="NEW",
        evidence_allocations=[
            {
                "evidence_item_id": equipment_evidence_id,
                "claim_id": equipment_claim_id,
                "allocation_quantity": "1",
            }
        ],
    )
    for assertion_id, rationale in (
        (duct_assertion_id, "Plan segment and measured extent reviewed."),
        (equipment_assertion_id, "Equipment schedule count reviewed."),
    ):
        service.record_quantity_review(
            assertion_id=assertion_id,
            actor_id=estimator_id,
            outcome="VERIFIED",
            rationale=rationale,
        )
        service.record_quantity_review(
            assertion_id=assertion_id,
            actor_id=estimator_id,
            outcome="APPROVED",
            rationale="Approved for the frozen bid baseline.",
        )

    takeoff_version_id = service.create_takeoff_version(revision_set_id=revision_set_id)
    service.approve_takeoff_version(
        takeoff_version_id=takeoff_version_id,
        actor_id=estimator_id,
        rationale="Every takeoff line has reviewed project evidence.",
    )
    takeoff_version = repository.get_takeoff_version(takeoff_version_id)
    takeoff_line_by_subject = {line["subject_key"]: line["id"] for line in takeoff_version["lines"]}

    quote_document_id = repository.register_document_revision(
        project_id=project_id,
        document_type="OTHER",
        document_number="QS-DEMO-001",
        title="Verified Supply Quote",
        sha256="0" * 64,
        issue_date="2026-08-27",
    )
    supplier_id = service.create_supplier(project_id=project_id, name="Verified Supply")
    quote_revision_id = service.create_quote_revision(
        project_id=project_id,
        supplier_id=supplier_id,
        source_document_revision_id=quote_document_id,
        quote_number="QS-DEMO-001",
        issued_at="2026-08-27",
        valid_through="2099-12-31",
        currency="USD",
        terms="Delivered to jobsite; freight included.",
    )
    duct_quote_line_id = service.add_quote_line(
        quote_revision_id=quote_revision_id,
        supplier_part_number="DUCT-24X12",
        description="24x12 supply-air duct",
        uom="LF",
        unit_price="88.00",
    )
    equipment_quote_line_id = service.add_quote_line(
        quote_revision_id=quote_revision_id,
        supplier_part_number="EF-1",
        description="Exhaust fan EF-1",
        uom="EA",
        unit_price="1500.00",
    )
    estimate_version_id = service.create_estimate_version(
        takeoff_version_id=takeoff_version_id,
        line_inputs=[
            {
                "takeoff_line_id": takeoff_line_by_subject["M-101:SA-01"],
                "quote_line_id": duct_quote_line_id,
            },
            {
                "takeoff_line_id": takeoff_line_by_subject["M-601:EF-1"],
                "quote_line_id": equipment_quote_line_id,
            },
        ],
    )
    service.approve_estimate_version(
        estimate_version_id=estimate_version_id,
        actor_id=estimator_id,
        rationale="Quantity and dated quote inputs reconciled.",
    )
    bid_release_id = service.release_bid(
        takeoff_version_id=takeoff_version_id,
        estimate_version_id=estimate_version_id,
        authorized_bidder_actor_id=bidder_id,
        submitter_actor_id=submitter_id,
        rationale="Released after controlled quantity, estimate, and authority review.",
    )

    return {
        "database_path": str(path),
        "project_id": project_id,
        "claim_ids": [duct_claim_id, equipment_claim_id],
        "takeoff_version": repository.get_takeoff_version(takeoff_version_id),
        "bid_release": repository.get_bid_release(bid_release_id),
    }
