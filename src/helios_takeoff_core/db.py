"""SQLite connection and migration management for the canonical P0 store."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator


class Database:
    """Owns SQLite connections and applies ordered, immutable schema migrations."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._local = threading.local()

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.create_function("helios_decimal_product", 2, self._decimal_product, deterministic=True)
        connection.create_function(
            "helios_domain_pack_is_canonical",
            2,
            self._domain_pack_is_canonical,
            deterministic=True,
        )
        return connection

    @staticmethod
    def _decimal_product(left: object, right: object) -> str | None:
        """Return canonical decimal multiplication for SQLite snapshot triggers."""
        try:
            return format(Decimal(str(left)) * Decimal(str(right)), "f")
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _domain_pack_is_canonical(canonical_json: object, sha256: object) -> int:
        """Fail closed unless text and digest are exact strict compiler output."""
        try:
            if not isinstance(canonical_json, str) or not isinstance(sha256, str):
                return 0

            from .engine.compiler import compile_domain_pack

            def reject_constant(value: str) -> object:
                raise ValueError(f"unsupported JSON constant: {value}")

            document = json.loads(canonical_json, parse_constant=reject_constant)
            compiled = compile_domain_pack(document)
            return int(
                compiled.canonical_bytes.decode("utf-8") == canonical_json
                and compiled.sha256 == sha256
            )
        except Exception:
            return 0

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """Yield a connection with foreign keys enabled and transactional cleanup."""
        active_connection = getattr(self._local, "transaction_connection", None)
        if active_connection is not None:
            yield active_connection
            return
        connection = self._new_connection()
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield a re-entrant write transaction that obtains a lock before domain writes.

        Nested repository/service calls become SQLite savepoints on one connection.
        That lets an API command, its domain side effects, and its idempotency
        record commit or roll back as one unit.
        """
        active_connection = getattr(self._local, "transaction_connection", None)
        if active_connection is not None:
            depth = getattr(self._local, "transaction_depth", 0) + 1
            savepoint = f"helios_nested_{depth}"
            self._local.transaction_depth = depth
            active_connection.execute(f"SAVEPOINT {savepoint}")
            try:
                yield active_connection
            except BaseException:
                active_connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                active_connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
            else:
                active_connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            finally:
                self._local.transaction_depth = depth - 1
            return

        connection = self._new_connection()
        self._local.transaction_connection = connection
        self._local.transaction_depth = 1
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            self._local.transaction_connection = None
            self._local.transaction_depth = 0
            connection.close()

    def initialize(self) -> None:
        """Create the migration ledger and apply every unapplied numbered SQL migration."""
        with self.connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            applied_versions = {
                row[0] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for migration_path in sorted(self._migration_directory().glob("[0-9][0-9][0-9]_*.sql")):
                version = int(migration_path.name.split("_", 1)[0])
                if version in applied_versions:
                    continue
                self._apply_migration(connection, migration_path, version)

    @classmethod
    def _apply_migration(
        cls,
        connection: sqlite3.Connection,
        migration_path: Path,
        version: int,
    ) -> None:
        """Apply one migration and its ledger row in the same transaction."""
        script = migration_path.read_text(encoding="utf-8")
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in cls._sql_statements(script):
                connection.execute(statement)
            violation = connection.execute("PRAGMA foreign_key_check").fetchone()
            if violation is not None:
                raise sqlite3.IntegrityError(
                    f"migration {migration_path.name} introduced a foreign key violation"
                )
            connection.execute(
                "INSERT INTO schema_migrations(version, name) VALUES (?, ?)",
                (version, migration_path.name),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys = ON")

    @staticmethod
    def _sql_statements(script: str) -> Iterator[str]:
        """Yield complete SQLite statements without executescript's implicit commit."""
        pending = ""
        for line in script.splitlines(keepends=True):
            pending += line
            if sqlite3.complete_statement(pending):
                statement = pending.strip()
                if statement:
                    yield statement
                pending = ""
        if pending.strip():
            raise sqlite3.OperationalError("migration ends with an incomplete SQL statement")

    @staticmethod
    def _migration_directory() -> Path:
        return Path(__file__).resolve().parent / "migrations"
