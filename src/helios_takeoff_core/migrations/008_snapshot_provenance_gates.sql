-- A takeoff snapshot must belong to the same project as its frozen baseline.
CREATE TRIGGER takeoff_versions_require_matching_frozen_baseline
BEFORE INSERT ON takeoff_versions
WHEN NOT EXISTS (
    SELECT 1 FROM revision_sets
    WHERE id = NEW.revision_set_id
      AND project_id = NEW.project_id
      AND status = 'FROZEN'
)
BEGIN
    SELECT RAISE(ABORT, 'takeoff versions require a matching frozen revision set');
END;

-- An approved assertion is still not eligible for a snapshot until it has an
-- immutable baseline evidence allocation.
CREATE TRIGGER takeoff_lines_require_evidence_allocation
BEFORE INSERT ON takeoff_lines
WHEN NOT EXISTS (
    SELECT 1 FROM quantity_evidence_allocations
    WHERE quantity_assertion_id = NEW.source_quantity_assertion_id
)
BEGIN
    SELECT RAISE(ABORT, 'takeoff lines require a source quantity evidence allocation');
END;

-- An estimate always belongs to an already approved takeoff from the same project.
CREATE TRIGGER estimate_versions_require_matching_approved_takeoff
BEFORE INSERT ON estimate_versions
WHEN NOT EXISTS (
    SELECT 1
    FROM takeoff_versions AS takeoff
    JOIN takeoff_approval_events AS approval ON approval.takeoff_version_id = takeoff.id
    WHERE takeoff.id = NEW.takeoff_version_id
      AND takeoff.project_id = NEW.project_id
)
BEGIN
    SELECT RAISE(ABORT, 'estimate versions require a matching approved takeoff version');
END;

-- Estimate-line quantities and prices must be a snapshot of exactly the selected
-- takeoff line and quote line, never a cross-project or arbitrary direct insert.
CREATE TRIGGER estimate_lines_require_matching_takeoff_quote_snapshot
BEFORE INSERT ON estimate_lines
WHEN NOT EXISTS (
    SELECT 1
    FROM estimate_versions AS estimate
    JOIN takeoff_lines AS takeoff_line ON takeoff_line.id = NEW.takeoff_line_id
    JOIN quote_lines AS quote_line ON quote_line.id = NEW.quote_line_id
    JOIN quote_revisions AS quote ON quote.id = quote_line.quote_revision_id
    WHERE estimate.id = NEW.estimate_version_id
      AND takeoff_line.takeoff_version_id = estimate.takeoff_version_id
      AND quote.project_id = estimate.project_id
      AND takeoff_line.uom = NEW.uom
      AND quote_line.uom = NEW.uom
      AND takeoff_line.quantity = NEW.quantity
      AND quote_line.unit_price = NEW.unit_price
      AND NEW.extended_price = helios_decimal_product(NEW.quantity, NEW.unit_price)
)
BEGIN
    SELECT RAISE(ABORT, 'estimate lines require matching takeoff and quote snapshots');
END;

CREATE TRIGGER estimate_approvals_require_complete_estimate_snapshot
BEFORE INSERT ON estimate_approval_events
WHEN EXISTS (
    SELECT 1
    FROM estimate_versions AS estimate
    JOIN takeoff_lines AS takeoff_line ON takeoff_line.takeoff_version_id = estimate.takeoff_version_id
    WHERE estimate.id = NEW.estimate_version_id
      AND NOT EXISTS (
          SELECT 1 FROM estimate_lines AS estimate_line
          WHERE estimate_line.estimate_version_id = estimate.id
            AND estimate_line.takeoff_line_id = takeoff_line.id
      )
)
OR EXISTS (
    SELECT 1
    FROM estimate_lines AS estimate_line
    JOIN estimate_versions AS estimate ON estimate.id = estimate_line.estimate_version_id
    JOIN takeoff_lines AS takeoff_line ON takeoff_line.id = estimate_line.takeoff_line_id
    WHERE estimate.id = NEW.estimate_version_id
      AND takeoff_line.takeoff_version_id <> estimate.takeoff_version_id
)
BEGIN
    SELECT RAISE(ABORT, 'estimate approval requires one matching line for every takeoff line');
END;

CREATE TRIGGER bid_releases_recheck_selected_quote_validity
BEFORE INSERT ON bid_releases
WHEN EXISTS (
    SELECT 1
    FROM estimate_lines AS estimate_line
    JOIN quote_lines AS quote_line ON quote_line.id = estimate_line.quote_line_id
    JOIN quote_revisions AS quote ON quote.id = quote_line.quote_revision_id
    WHERE estimate_line.estimate_version_id = NEW.estimate_version_id
      AND quote.valid_through < date('now')
)
BEGIN
    SELECT RAISE(ABORT, 'bid release requires all selected quote inputs to remain valid');
END;
