CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE actors (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK(actor_type IN ('USER', 'SERVICE')),
    created_at TEXT NOT NULL
);

CREATE TABLE document_revisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    document_type TEXT NOT NULL CHECK(document_type IN ('DRAWING', 'SPECIFICATION', 'SCHEDULE', 'ADDENDUM', 'RFI_RESPONSE', 'OTHER')),
    document_number TEXT NOT NULL,
    title TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64 AND sha256 GLOB '[0-9a-f]*'),
    issue_date TEXT NOT NULL,
    received_at TEXT NOT NULL,
    supersedes_document_revision_id TEXT REFERENCES document_revisions(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, sha256)
);

CREATE TRIGGER document_revisions_are_immutable
BEFORE UPDATE ON document_revisions
BEGIN
    SELECT RAISE(ABORT, 'document revisions are immutable');
END;

CREATE TRIGGER document_revisions_cannot_be_deleted
BEFORE DELETE ON document_revisions
BEGIN
    SELECT RAISE(ABORT, 'document revisions cannot be deleted');
END;

CREATE TABLE revision_sets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('OPEN', 'FROZEN')),
    created_at TEXT NOT NULL,
    frozen_at TEXT,
    UNIQUE(project_id, name)
);

CREATE TRIGGER revision_sets_only_freeze_once
BEFORE UPDATE ON revision_sets
WHEN OLD.status <> 'OPEN'
  OR NEW.status <> 'FROZEN'
  OR NEW.id <> OLD.id
  OR NEW.project_id <> OLD.project_id
  OR NEW.name <> OLD.name
  OR NEW.created_at <> OLD.created_at
BEGIN
    SELECT RAISE(ABORT, 'revision sets may only transition from OPEN to FROZEN');
END;

CREATE TRIGGER frozen_revision_sets_cannot_be_deleted
BEFORE DELETE ON revision_sets
WHEN OLD.status = 'FROZEN'
BEGIN
    SELECT RAISE(ABORT, 'frozen revision sets cannot be deleted');
END;

CREATE TABLE revision_set_documents (
    revision_set_id TEXT NOT NULL REFERENCES revision_sets(id) ON DELETE RESTRICT,
    document_revision_id TEXT NOT NULL REFERENCES document_revisions(id) ON DELETE RESTRICT,
    included_at TEXT NOT NULL,
    PRIMARY KEY(revision_set_id, document_revision_id)
);

CREATE TRIGGER frozen_revision_sets_reject_document_insert
BEFORE INSERT ON revision_set_documents
WHEN (SELECT status FROM revision_sets WHERE id = NEW.revision_set_id) <> 'OPEN'
BEGIN
    SELECT RAISE(ABORT, 'frozen revision sets cannot receive documents');
END;

CREATE TRIGGER revision_set_documents_are_immutable
BEFORE UPDATE ON revision_set_documents
BEGIN
    SELECT RAISE(ABORT, 'revision set membership is immutable');
END;

CREATE TRIGGER frozen_revision_sets_reject_document_delete
BEFORE DELETE ON revision_set_documents
WHEN (SELECT status FROM revision_sets WHERE id = OLD.revision_set_id) = 'FROZEN'
BEGIN
    SELECT RAISE(ABORT, 'frozen revision set membership cannot be deleted');
END;
