CREATE TABLE suppliers (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, name)
);

CREATE TRIGGER suppliers_are_immutable
BEFORE UPDATE ON suppliers
BEGIN
    SELECT RAISE(ABORT, 'suppliers are immutable');
END;

CREATE TRIGGER suppliers_cannot_be_deleted
BEFORE DELETE ON suppliers
BEGIN
    SELECT RAISE(ABORT, 'suppliers cannot be deleted');
END;

CREATE TABLE quote_revisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    supplier_id TEXT NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    source_document_revision_id TEXT NOT NULL REFERENCES document_revisions(id) ON DELETE RESTRICT,
    quote_number TEXT NOT NULL,
    issued_at TEXT NOT NULL,
    valid_through TEXT NOT NULL,
    currency TEXT NOT NULL,
    terms TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK(valid_through >= issued_at)
);

CREATE TRIGGER quote_revisions_are_immutable
BEFORE UPDATE ON quote_revisions
BEGIN
    SELECT RAISE(ABORT, 'quote revisions are immutable');
END;

CREATE TRIGGER quote_revisions_cannot_be_deleted
BEFORE DELETE ON quote_revisions
BEGIN
    SELECT RAISE(ABORT, 'quote revisions cannot be deleted');
END;

CREATE TABLE quote_lines (
    id TEXT PRIMARY KEY,
    quote_revision_id TEXT NOT NULL REFERENCES quote_revisions(id) ON DELETE RESTRICT,
    supplier_part_number TEXT NOT NULL,
    description TEXT NOT NULL,
    uom TEXT NOT NULL,
    unit_price TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER quote_lines_are_immutable
BEFORE UPDATE ON quote_lines
BEGIN
    SELECT RAISE(ABORT, 'quote lines are immutable');
END;

CREATE TRIGGER quote_lines_cannot_be_deleted
BEFORE DELETE ON quote_lines
BEGIN
    SELECT RAISE(ABORT, 'quote lines cannot be deleted');
END;

CREATE TABLE estimate_versions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    takeoff_version_id TEXT NOT NULL REFERENCES takeoff_versions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL
);

CREATE TRIGGER estimate_versions_are_immutable
BEFORE UPDATE ON estimate_versions
BEGIN
    SELECT RAISE(ABORT, 'estimate versions are immutable');
END;

CREATE TRIGGER estimate_versions_cannot_be_deleted
BEFORE DELETE ON estimate_versions
BEGIN
    SELECT RAISE(ABORT, 'estimate versions cannot be deleted');
END;

CREATE TABLE estimate_lines (
    id TEXT PRIMARY KEY,
    estimate_version_id TEXT NOT NULL REFERENCES estimate_versions(id) ON DELETE RESTRICT,
    takeoff_line_id TEXT NOT NULL REFERENCES takeoff_lines(id) ON DELETE RESTRICT,
    quote_line_id TEXT NOT NULL REFERENCES quote_lines(id) ON DELETE RESTRICT,
    uom TEXT NOT NULL,
    quantity TEXT NOT NULL,
    unit_price TEXT NOT NULL,
    extended_price TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(estimate_version_id, takeoff_line_id)
);

CREATE TRIGGER estimate_lines_are_immutable
BEFORE UPDATE ON estimate_lines
BEGIN
    SELECT RAISE(ABORT, 'estimate lines are immutable');
END;

CREATE TRIGGER estimate_lines_cannot_be_deleted
BEFORE DELETE ON estimate_lines
BEGIN
    SELECT RAISE(ABORT, 'estimate lines cannot be deleted');
END;

CREATE TABLE estimate_approval_events (
    id TEXT PRIMARY KEY,
    estimate_version_id TEXT NOT NULL UNIQUE REFERENCES estimate_versions(id) ON DELETE RESTRICT,
    actor_id TEXT NOT NULL REFERENCES actors(id) ON DELETE RESTRICT,
    role_grant_id TEXT NOT NULL REFERENCES role_grants(id) ON DELETE RESTRICT,
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER estimate_approval_events_are_immutable
BEFORE UPDATE ON estimate_approval_events
BEGIN
    SELECT RAISE(ABORT, 'estimate approvals are immutable');
END;

CREATE TRIGGER estimate_approval_events_cannot_be_deleted
BEFORE DELETE ON estimate_approval_events
BEGIN
    SELECT RAISE(ABORT, 'estimate approvals cannot be deleted');
END;

CREATE TABLE bid_releases (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    takeoff_version_id TEXT NOT NULL REFERENCES takeoff_versions(id) ON DELETE RESTRICT,
    estimate_version_id TEXT NOT NULL REFERENCES estimate_versions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    UNIQUE(estimate_version_id)
);

CREATE TRIGGER bid_releases_are_immutable
BEFORE UPDATE ON bid_releases
BEGIN
    SELECT RAISE(ABORT, 'bid releases are immutable');
END;

CREATE TRIGGER bid_releases_cannot_be_deleted
BEFORE DELETE ON bid_releases
BEGIN
    SELECT RAISE(ABORT, 'bid releases cannot be deleted');
END;

CREATE TABLE bid_release_approval_events (
    id TEXT PRIMARY KEY,
    bid_release_id TEXT NOT NULL REFERENCES bid_releases(id) ON DELETE RESTRICT,
    actor_id TEXT NOT NULL REFERENCES actors(id) ON DELETE RESTRICT,
    role_grant_id TEXT NOT NULL REFERENCES role_grants(id) ON DELETE RESTRICT,
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(bid_release_id, role_grant_id)
);

CREATE TRIGGER bid_release_approval_events_are_immutable
BEFORE UPDATE ON bid_release_approval_events
BEGIN
    SELECT RAISE(ABORT, 'bid release approvals are immutable');
END;

CREATE TRIGGER bid_release_approval_events_cannot_be_deleted
BEFORE DELETE ON bid_release_approval_events
BEGIN
    SELECT RAISE(ABORT, 'bid release approvals cannot be deleted');
END;

CREATE TABLE bid_release_void_events (
    id TEXT PRIMARY KEY,
    bid_release_id TEXT NOT NULL UNIQUE REFERENCES bid_releases(id) ON DELETE RESTRICT,
    actor_id TEXT NOT NULL REFERENCES actors(id) ON DELETE RESTRICT,
    role_grant_id TEXT NOT NULL REFERENCES role_grants(id) ON DELETE RESTRICT,
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER bid_release_void_events_are_immutable
BEFORE UPDATE ON bid_release_void_events
BEGIN
    SELECT RAISE(ABORT, 'bid release void events are immutable');
END;

CREATE TRIGGER bid_release_void_events_cannot_be_deleted
BEFORE DELETE ON bid_release_void_events
BEGIN
    SELECT RAISE(ABORT, 'bid release void events cannot be deleted');
END;
