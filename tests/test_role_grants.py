"""Tests that role-grant bounds are real instants before authorization."""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from helios_takeoff_core.db import Database
from helios_takeoff_core.errors import AuthorizationError, ValidationError
from helios_takeoff_core.repository import TakeoffRepository
from helios_takeoff_core.services import TakeoffService


class RoleGrantTimestampTests(unittest.TestCase):
    """Offset and malformed bounds must not widen an active role grant."""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary_directory.name) / "p0.sqlite3")
        self.database.initialize()
        self.repository = TakeoffRepository(self.database)
        self.project_id = self.repository.create_project(code="P-400", name="Lab Renovation")
        self.actor_id = self.repository.create_actor(display_name="Estimator", actor_type="USER")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_offset_valid_from_is_stored_as_utc_and_not_active_early(self) -> None:
        future = datetime.now(UTC) + timedelta(hours=2)
        offered = future.astimezone(timezone(timedelta(hours=-5))).isoformat()
        self.assertLess(offered, datetime.now(UTC).isoformat())

        grant_id = self.repository.grant_role(
            actor_id=self.actor_id,
            project_id=self.project_id,
            role="ESTIMATOR_REVIEWER",
            valid_from=offered,
        )

        with self.database.connection() as connection:
            stored = connection.execute(
                "SELECT valid_from FROM role_grants WHERE id = ?",
                (grant_id,),
            ).fetchone()["valid_from"]
            self.assertEqual(stored, future.astimezone(UTC).isoformat())
            with self.assertRaises(AuthorizationError):
                TakeoffService._require_active_role_grant(
                    connection,
                    actor_id=self.actor_id,
                    project_id=self.project_id,
                    role="ESTIMATOR_REVIEWER",
                )

    def test_reviewer_offset_example_is_stored_as_canonical_utc(self) -> None:
        grant_id = self.repository.grant_role(
            actor_id=self.actor_id,
            project_id=self.project_id,
            role="ESTIMATOR_REVIEWER",
            valid_from="2026-09-03T10:00:00-05:00",
        )

        with self.database.connection() as connection:
            stored = connection.execute(
                "SELECT valid_from FROM role_grants WHERE id = ?",
                (grant_id,),
            ).fetchone()["valid_from"]
        self.assertEqual(stored, "2026-09-03T15:00:00+00:00")

    def test_offset_valid_until_does_not_stay_active_after_the_instant(self) -> None:
        started = datetime.now(UTC) - timedelta(days=1)
        expired = datetime.now(UTC) - timedelta(hours=1)
        offered = expired.astimezone(timezone(timedelta(hours=5))).isoformat()
        self.assertGreater(offered, datetime.now(UTC).isoformat())

        self.repository.grant_role(
            actor_id=self.actor_id,
            project_id=self.project_id,
            role="ESTIMATOR_REVIEWER",
            valid_from=started.isoformat(),
            valid_until=offered,
        )

        with self.database.connection() as connection:
            with self.assertRaises(AuthorizationError):
                TakeoffService._require_active_role_grant(
                    connection,
                    actor_id=self.actor_id,
                    project_id=self.project_id,
                    role="ESTIMATOR_REVIEWER",
                )

    def test_a_past_offset_grant_stays_active(self) -> None:
        started = datetime.now(UTC) - timedelta(hours=3)
        offered = started.astimezone(timezone(timedelta(hours=-5))).isoformat()

        self.repository.grant_role(
            actor_id=self.actor_id,
            project_id=self.project_id,
            role="ESTIMATOR_REVIEWER",
            valid_from=offered,
        )

        with self.database.connection() as connection:
            grant_id = TakeoffService._require_active_role_grant(
                connection,
                actor_id=self.actor_id,
                project_id=self.project_id,
                role="ESTIMATOR_REVIEWER",
            )
        self.assertTrue(grant_id)

    def test_malformed_role_bounds_are_rejected(self) -> None:
        cases = (
            ("valid_from", "not-a-timestamp", None),
            ("valid_from", "2026-09-03T10:00:00", None),
            ("valid_from", "2026-02-31T10:00:00+00:00", None),
            ("valid_until", None, "zzz"),
            ("valid_until", None, "2026-09-03T15:00:00"),
        )
        for field_name, valid_from, valid_until in cases:
            with self.subTest(field_name=field_name, valid_from=valid_from, valid_until=valid_until):
                with self.assertRaises(ValidationError) as caught:
                    self.repository.grant_role(
                        actor_id=self.actor_id,
                        project_id=self.project_id,
                        role="ESTIMATOR_REVIEWER",
                        valid_from=valid_from,
                        valid_until=valid_until,
                    )
                self.assertEqual(str(caught.exception), f"{field_name} must be a timezone-aware timestamp")
        with self.database.connection() as connection:
            count = connection.execute("SELECT COUNT(*) FROM role_grants").fetchone()[0]
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
