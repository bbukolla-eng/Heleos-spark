CREATE TRIGGER projects_are_immutable
BEFORE UPDATE ON projects
BEGIN
    SELECT RAISE(ABORT, 'projects are immutable');
END;

CREATE TRIGGER projects_cannot_be_deleted
BEFORE DELETE ON projects
BEGIN
    SELECT RAISE(ABORT, 'projects cannot be deleted');
END;

CREATE TRIGGER actors_are_immutable
BEFORE UPDATE ON actors
BEGIN
    SELECT RAISE(ABORT, 'actors are immutable');
END;

CREATE TRIGGER actors_cannot_be_deleted
BEFORE DELETE ON actors
BEGIN
    SELECT RAISE(ABORT, 'actors cannot be deleted');
END;
