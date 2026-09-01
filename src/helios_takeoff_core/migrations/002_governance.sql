CREATE TABLE role_grants (
    id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL REFERENCES actors(id) ON DELETE RESTRICT,
    project_id TEXT REFERENCES projects(id) ON DELETE RESTRICT,
    role TEXT NOT NULL CHECK(role IN ('ESTIMATOR_REVIEWER', 'AUTHORIZED_BIDDER', 'SUBMITTER')),
    valid_from TEXT NOT NULL,
    valid_until TEXT,
    created_at TEXT NOT NULL,
    CHECK(valid_until IS NULL OR valid_until >= valid_from)
);

CREATE TRIGGER role_grants_are_immutable
BEFORE UPDATE ON role_grants
BEGIN
    SELECT RAISE(ABORT, 'role grants are immutable');
END;

CREATE TRIGGER role_grants_cannot_be_deleted
BEFORE DELETE ON role_grants
BEGIN
    SELECT RAISE(ABORT, 'role grants cannot be deleted');
END;

CREATE TABLE role_grant_revocations (
    id TEXT PRIMARY KEY,
    role_grant_id TEXT NOT NULL UNIQUE REFERENCES role_grants(id) ON DELETE RESTRICT,
    revoked_by_actor_id TEXT NOT NULL REFERENCES actors(id) ON DELETE RESTRICT,
    rationale TEXT NOT NULL,
    revoked_at TEXT NOT NULL
);

CREATE TRIGGER role_grant_revocations_are_immutable
BEFORE UPDATE ON role_grant_revocations
BEGIN
    SELECT RAISE(ABORT, 'role grant revocations are immutable');
END;

CREATE TRIGGER role_grant_revocations_cannot_be_deleted
BEFORE DELETE ON role_grant_revocations
BEGIN
    SELECT RAISE(ABORT, 'role grant revocations cannot be deleted');
END;

CREATE TABLE idempotency_requests (
    id TEXT PRIMARY KEY,
    operation_scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    result_resource_type TEXT NOT NULL,
    result_resource_id TEXT NOT NULL,
    result_payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(operation_scope, idempotency_key)
);

CREATE TRIGGER idempotency_requests_are_immutable
BEFORE UPDATE ON idempotency_requests
BEGIN
    SELECT RAISE(ABORT, 'idempotency records are immutable');
END;

CREATE TRIGGER idempotency_requests_cannot_be_deleted
BEFORE DELETE ON idempotency_requests
BEGIN
    SELECT RAISE(ABORT, 'idempotency records cannot be deleted');
END;
