-- Lock mutable membership once an assertion enters human review.
CREATE TRIGGER quantity_allocations_lock_after_first_review
BEFORE INSERT ON quantity_evidence_allocations
WHEN EXISTS (
    SELECT 1 FROM quantity_review_events
    WHERE quantity_assertion_id = NEW.quantity_assertion_id
)
BEGIN
    SELECT RAISE(ABORT, 'reviewed quantity assertions cannot receive new evidence allocations');
END;

CREATE TRIGGER claim_evidence_locks_after_quantity_use
BEFORE INSERT ON claim_evidence
WHEN EXISTS (
    SELECT 1 FROM quantity_evidence_allocations
    WHERE extraction_claim_id = NEW.claim_id
)
BEGIN
    SELECT RAISE(ABORT, 'claims used by a quantity assertion cannot receive new evidence links');
END;

-- A review decision must refer to the actor's actual active estimator grant and
-- must follow the append-only candidate -> verified -> approved/rejected state machine.
CREATE TRIGGER quantity_reviews_require_active_reviewer_grant
BEFORE INSERT ON quantity_review_events
WHEN NOT EXISTS (
    SELECT 1
    FROM quantity_assertions AS assertion
    JOIN revision_sets AS baseline ON baseline.id = assertion.revision_set_id
    JOIN role_grants AS grant ON grant.id = NEW.role_grant_id
    LEFT JOIN role_grant_revocations AS revocation ON revocation.role_grant_id = grant.id
    WHERE assertion.id = NEW.quantity_assertion_id
      AND grant.actor_id = NEW.actor_id
      AND grant.role = 'ESTIMATOR_REVIEWER'
      AND (grant.project_id = baseline.project_id OR grant.project_id IS NULL)
      AND grant.valid_from <= NEW.created_at
      AND (grant.valid_until IS NULL OR grant.valid_until >= NEW.created_at)
      AND revocation.id IS NULL
)
BEGIN
    SELECT RAISE(ABORT, 'quantity review requires an active estimator reviewer grant');
END;

CREATE TRIGGER quantity_reviews_enforce_state_machine
BEFORE INSERT ON quantity_review_events
WHEN (
    (NOT EXISTS (
        SELECT 1 FROM quantity_review_events WHERE quantity_assertion_id = NEW.quantity_assertion_id
    ) AND NEW.outcome NOT IN ('VERIFIED', 'REJECTED'))
    OR
    ((SELECT outcome FROM quantity_review_events
      WHERE quantity_assertion_id = NEW.quantity_assertion_id
      ORDER BY created_at DESC, rowid DESC LIMIT 1) = 'VERIFIED'
     AND NEW.outcome NOT IN ('APPROVED', 'REJECTED'))
    OR
    ((SELECT outcome FROM quantity_review_events
      WHERE quantity_assertion_id = NEW.quantity_assertion_id
      ORDER BY created_at DESC, rowid DESC LIMIT 1) IN ('APPROVED', 'REJECTED'))
)
BEGIN
    SELECT RAISE(ABORT, 'quantity review outcome is not a valid state transition');
END;

-- Every snapshot line must be an approved assertion in the same frozen baseline.
CREATE TRIGGER takeoff_lines_require_approved_same_baseline_assertion
BEFORE INSERT ON takeoff_lines
WHEN NOT EXISTS (
    SELECT 1
    FROM takeoff_versions AS takeoff
    JOIN revision_sets AS baseline ON baseline.id = takeoff.revision_set_id
    JOIN quantity_assertions AS assertion ON assertion.id = NEW.source_quantity_assertion_id
    WHERE takeoff.id = NEW.takeoff_version_id
      AND baseline.status = 'FROZEN'
      AND assertion.revision_set_id = takeoff.revision_set_id
      AND assertion.subject_kind = NEW.subject_kind
      AND assertion.subject_key = NEW.subject_key
      AND assertion.uom = NEW.uom
      AND assertion.quantity = NEW.quantity
      AND assertion.scope_state = NEW.scope_state
      AND (SELECT outcome FROM quantity_review_events
           WHERE quantity_assertion_id = assertion.id
           ORDER BY created_at DESC, rowid DESC LIMIT 1) = 'APPROVED'
)
BEGIN
    SELECT RAISE(ABORT, 'takeoff lines require approved assertions from the same frozen baseline');
END;

CREATE TRIGGER takeoff_lines_lock_after_approval
BEFORE INSERT ON takeoff_lines
WHEN EXISTS (
    SELECT 1 FROM takeoff_approval_events
    WHERE takeoff_version_id = NEW.takeoff_version_id
)
BEGIN
    SELECT RAISE(ABORT, 'approved takeoff versions cannot receive new lines');
END;

CREATE TRIGGER takeoff_approvals_require_active_reviewer_grant
BEFORE INSERT ON takeoff_approval_events
WHEN NOT EXISTS (
    SELECT 1
    FROM takeoff_versions AS takeoff
    JOIN role_grants AS grant ON grant.id = NEW.role_grant_id
    LEFT JOIN role_grant_revocations AS revocation ON revocation.role_grant_id = grant.id
    WHERE takeoff.id = NEW.takeoff_version_id
      AND grant.actor_id = NEW.actor_id
      AND grant.role = 'ESTIMATOR_REVIEWER'
      AND (grant.project_id = takeoff.project_id OR grant.project_id IS NULL)
      AND grant.valid_from <= NEW.created_at
      AND (grant.valid_until IS NULL OR grant.valid_until >= NEW.created_at)
      AND revocation.id IS NULL
)
BEGIN
    SELECT RAISE(ABORT, 'takeoff approval requires an active estimator reviewer grant');
END;

CREATE TRIGGER takeoff_approvals_require_complete_unconflicted_snapshot
BEFORE INSERT ON takeoff_approval_events
WHEN NOT EXISTS (
    SELECT 1 FROM takeoff_lines WHERE takeoff_version_id = NEW.takeoff_version_id
)
OR EXISTS (
    SELECT 1
    FROM conflicts AS conflict
    JOIN takeoff_versions AS takeoff ON takeoff.revision_set_id = conflict.revision_set_id
    LEFT JOIN conflict_dispositions AS disposition ON disposition.conflict_id = conflict.id
    WHERE takeoff.id = NEW.takeoff_version_id AND disposition.id IS NULL
)
BEGIN
    SELECT RAISE(ABORT, 'takeoff approval requires a complete snapshot with no unresolved conflicts');
END;

-- Freeze estimate contents after review and require the stored grant used by a
-- direct insert to have the same project/actor/role semantics as service code.
CREATE TRIGGER estimate_lines_lock_after_approval
BEFORE INSERT ON estimate_lines
WHEN EXISTS (
    SELECT 1 FROM estimate_approval_events
    WHERE estimate_version_id = NEW.estimate_version_id
)
OR EXISTS (
    SELECT 1
    FROM bid_releases
    WHERE estimate_version_id = NEW.estimate_version_id
)
BEGIN
    SELECT RAISE(ABORT, 'approved or released estimates cannot receive new lines');
END;

CREATE TRIGGER estimate_approvals_require_active_reviewer_grant
BEFORE INSERT ON estimate_approval_events
WHEN NOT EXISTS (
    SELECT 1
    FROM estimate_versions AS estimate
    JOIN role_grants AS grant ON grant.id = NEW.role_grant_id
    LEFT JOIN role_grant_revocations AS revocation ON revocation.role_grant_id = grant.id
    WHERE estimate.id = NEW.estimate_version_id
      AND grant.actor_id = NEW.actor_id
      AND grant.role = 'ESTIMATOR_REVIEWER'
      AND (grant.project_id = estimate.project_id OR grant.project_id IS NULL)
      AND grant.valid_from <= NEW.created_at
      AND (grant.valid_until IS NULL OR grant.valid_until >= NEW.created_at)
      AND revocation.id IS NULL
)
BEGIN
    SELECT RAISE(ABORT, 'estimate approval requires an active estimator reviewer grant');
END;

-- A release and its decision events must refer to the exact approved estimate /
-- takeoff pair and roles; the void is also a role-bound append-only decision.
CREATE TRIGGER bid_releases_require_matching_approved_snapshots
BEFORE INSERT ON bid_releases
WHEN NOT EXISTS (
    SELECT 1
    FROM takeoff_versions AS takeoff
    JOIN estimate_versions AS estimate ON estimate.id = NEW.estimate_version_id
    JOIN takeoff_approval_events AS takeoff_approval ON takeoff_approval.takeoff_version_id = takeoff.id
    JOIN estimate_approval_events AS estimate_approval ON estimate_approval.estimate_version_id = estimate.id
    WHERE takeoff.id = NEW.takeoff_version_id
      AND takeoff.project_id = NEW.project_id
      AND estimate.project_id = NEW.project_id
      AND estimate.takeoff_version_id = takeoff.id
)
BEGIN
    SELECT RAISE(ABORT, 'bid release requires matching approved takeoff and estimate snapshots');
END;

CREATE TRIGGER bid_release_approvals_require_matching_active_role
BEFORE INSERT ON bid_release_approval_events
WHEN NOT EXISTS (
    SELECT 1
    FROM bid_releases AS release
    JOIN role_grants AS grant ON grant.id = NEW.role_grant_id
    LEFT JOIN role_grant_revocations AS revocation ON revocation.role_grant_id = grant.id
    WHERE release.id = NEW.bid_release_id
      AND grant.actor_id = NEW.actor_id
      AND grant.role IN ('AUTHORIZED_BIDDER', 'SUBMITTER')
      AND (grant.project_id = release.project_id OR grant.project_id IS NULL)
      AND grant.valid_from <= NEW.created_at
      AND (grant.valid_until IS NULL OR grant.valid_until >= NEW.created_at)
      AND revocation.id IS NULL
)
OR EXISTS (
    SELECT 1
    FROM bid_release_approval_events AS existing
    JOIN role_grants AS existing_grant ON existing_grant.id = existing.role_grant_id
    JOIN role_grants AS new_grant ON new_grant.id = NEW.role_grant_id
    WHERE existing.bid_release_id = NEW.bid_release_id
      AND existing_grant.role = new_grant.role
)
BEGIN
    SELECT RAISE(ABORT, 'bid release approval requires one active decision for each release role');
END;

CREATE TRIGGER bid_release_voids_require_active_bidder_grant
BEFORE INSERT ON bid_release_void_events
WHEN NOT EXISTS (
    SELECT 1
    FROM bid_releases AS release
    JOIN role_grants AS grant ON grant.id = NEW.role_grant_id
    LEFT JOIN role_grant_revocations AS revocation ON revocation.role_grant_id = grant.id
    WHERE release.id = NEW.bid_release_id
      AND grant.actor_id = NEW.actor_id
      AND grant.role = 'AUTHORIZED_BIDDER'
      AND (grant.project_id = release.project_id OR grant.project_id IS NULL)
      AND grant.valid_from <= NEW.created_at
      AND (grant.valid_until IS NULL OR grant.valid_until >= NEW.created_at)
      AND revocation.id IS NULL
)
BEGIN
    SELECT RAISE(ABORT, 'bid release void requires an active authorized bidder grant');
END;
