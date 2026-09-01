CREATE TABLE revision_set_impacts (
    successor_revision_set_id TEXT PRIMARY KEY REFERENCES revision_sets(id) ON DELETE RESTRICT,
    predecessor_revision_set_id TEXT NOT NULL REFERENCES revision_sets(id) ON DELETE RESTRICT,
    triggering_document_revision_id TEXT NOT NULL REFERENCES document_revisions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    CHECK(successor_revision_set_id <> predecessor_revision_set_id)
);

CREATE TRIGGER revision_set_impacts_are_immutable
BEFORE UPDATE ON revision_set_impacts
BEGIN
    SELECT RAISE(ABORT, 'revision set impacts are immutable');
END;

CREATE TRIGGER revision_set_impacts_cannot_be_deleted
BEFORE DELETE ON revision_set_impacts
BEGIN
    SELECT RAISE(ABORT, 'revision set impacts cannot be deleted');
END;

CREATE TABLE conflicts (
    id TEXT PRIMARY KEY,
    revision_set_id TEXT NOT NULL REFERENCES revision_sets(id) ON DELETE RESTRICT,
    conflict_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    description TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER conflicts_are_immutable
BEFORE UPDATE ON conflicts
BEGIN
    SELECT RAISE(ABORT, 'conflicts are immutable');
END;

CREATE TRIGGER conflicts_cannot_be_deleted
BEFORE DELETE ON conflicts
BEGIN
    SELECT RAISE(ABORT, 'conflicts cannot be deleted');
END;

CREATE TABLE rfis (
    id TEXT PRIMARY KEY,
    revision_set_id TEXT NOT NULL REFERENCES revision_sets(id) ON DELETE RESTRICT,
    question TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER rfis_are_immutable
BEFORE UPDATE ON rfis
BEGIN
    SELECT RAISE(ABORT, 'RFIs are immutable');
END;

CREATE TRIGGER rfis_cannot_be_deleted
BEFORE DELETE ON rfis
BEGIN
    SELECT RAISE(ABORT, 'RFIs cannot be deleted');
END;

CREATE TABLE rfi_responses (
    id TEXT PRIMARY KEY,
    rfi_id TEXT NOT NULL REFERENCES rfis(id) ON DELETE RESTRICT,
    response_document_revision_id TEXT NOT NULL REFERENCES document_revisions(id) ON DELETE RESTRICT,
    response_text TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER rfi_responses_are_immutable
BEFORE UPDATE ON rfi_responses
BEGIN
    SELECT RAISE(ABORT, 'RFI responses are immutable');
END;

CREATE TRIGGER rfi_responses_cannot_be_deleted
BEFORE DELETE ON rfi_responses
BEGIN
    SELECT RAISE(ABORT, 'RFI responses cannot be deleted');
END;

CREATE TABLE conflict_dispositions (
    id TEXT PRIMARY KEY,
    conflict_id TEXT NOT NULL UNIQUE REFERENCES conflicts(id) ON DELETE RESTRICT,
    rfi_response_id TEXT NOT NULL REFERENCES rfi_responses(id) ON DELETE RESTRICT,
    actor_id TEXT NOT NULL REFERENCES actors(id) ON DELETE RESTRICT,
    role_grant_id TEXT NOT NULL REFERENCES role_grants(id) ON DELETE RESTRICT,
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER conflict_dispositions_are_immutable
BEFORE UPDATE ON conflict_dispositions
BEGIN
    SELECT RAISE(ABORT, 'conflict dispositions are immutable');
END;

CREATE TRIGGER conflict_dispositions_cannot_be_deleted
BEFORE DELETE ON conflict_dispositions
BEGIN
    SELECT RAISE(ABORT, 'conflict dispositions cannot be deleted');
END;

CREATE TABLE takeoff_versions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    revision_set_id TEXT NOT NULL REFERENCES revision_sets(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL
);

CREATE TRIGGER takeoff_versions_are_immutable
BEFORE UPDATE ON takeoff_versions
BEGIN
    SELECT RAISE(ABORT, 'takeoff versions are immutable');
END;

CREATE TRIGGER takeoff_versions_cannot_be_deleted
BEFORE DELETE ON takeoff_versions
BEGIN
    SELECT RAISE(ABORT, 'takeoff versions cannot be deleted');
END;

CREATE TABLE takeoff_lines (
    id TEXT PRIMARY KEY,
    takeoff_version_id TEXT NOT NULL REFERENCES takeoff_versions(id) ON DELETE RESTRICT,
    source_quantity_assertion_id TEXT NOT NULL REFERENCES quantity_assertions(id) ON DELETE RESTRICT,
    subject_kind TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    uom TEXT NOT NULL,
    quantity TEXT NOT NULL,
    scope_state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(takeoff_version_id, source_quantity_assertion_id)
);

CREATE TRIGGER takeoff_lines_are_immutable
BEFORE UPDATE ON takeoff_lines
BEGIN
    SELECT RAISE(ABORT, 'takeoff lines are immutable');
END;

CREATE TRIGGER takeoff_lines_cannot_be_deleted
BEFORE DELETE ON takeoff_lines
BEGIN
    SELECT RAISE(ABORT, 'takeoff lines cannot be deleted');
END;

CREATE TABLE takeoff_approval_events (
    id TEXT PRIMARY KEY,
    takeoff_version_id TEXT NOT NULL UNIQUE REFERENCES takeoff_versions(id) ON DELETE RESTRICT,
    actor_id TEXT NOT NULL REFERENCES actors(id) ON DELETE RESTRICT,
    role_grant_id TEXT NOT NULL REFERENCES role_grants(id) ON DELETE RESTRICT,
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER takeoff_approval_events_are_immutable
BEFORE UPDATE ON takeoff_approval_events
BEGIN
    SELECT RAISE(ABORT, 'takeoff approvals are immutable');
END;

CREATE TRIGGER takeoff_approval_events_cannot_be_deleted
BEFORE DELETE ON takeoff_approval_events
BEGIN
    SELECT RAISE(ABORT, 'takeoff approvals cannot be deleted');
END;
