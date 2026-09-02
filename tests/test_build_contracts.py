from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.build_fabric_support import valid_task_manifest
from tools.helios_build.canonical import canonical_json_bytes, load_strict_json
from tools.helios_build.errors import ContractError, StateRootError
from tools.helios_build.paths import BuildPaths
from tools.helios_build.schemas import SchemaRegistry


class BuildContractsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[1]
        self.schemas = SchemaRegistry(self.repo_root)
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_strict_boundaries(self) -> None:
        duplicate = self.root / "duplicate.json"
        duplicate.write_text('{"protocol":"a","protocol":"b"}', encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "duplicate object key"):
            load_strict_json(duplicate)
        with self.assertRaisesRegex(ContractError, "floating-point"):
            canonical_json_bytes({"confidence": 0.9})
        invalid = valid_task_manifest() | {"unexpected": True}
        with self.assertRaises(ContractError):
            self.schemas.validate(invalid, "task-manifest-v1.schema.json")
        with self.assertRaisesRegex(StateRootError, "outside the repository"):
            BuildPaths.discover(self.repo_root, self.repo_root / "state")

    def test_state_root_rejects_filesystem_root_and_home(self) -> None:
        with self.assertRaisesRegex(StateRootError, "filesystem root"):
            BuildPaths.discover(self.repo_root, Path(Path.cwd().anchor))
        with self.assertRaisesRegex(StateRootError, "home directory"):
            BuildPaths.discover(self.repo_root, Path.home())

    def test_common_schema_is_closed(self) -> None:
        self.schemas.validate({}, "common-v1.schema.json")
        with self.assertRaises(ContractError):
            self.schemas.validate({"unexpected": True}, "common-v1.schema.json")

    def test_timestamps_require_real_utc_values(self) -> None:
        valid = valid_task_manifest()
        self.schemas.validate(valid, "task-manifest-v1.schema.json")
        for timestamp in (
            "2026-02-30T00:00:00Z",
            "2026-09-01T24:00:00Z",
            "2026-09-01T00:00:00+00:00",
        ):
            with self.subTest(timestamp=timestamp):
                invalid = valid | {"created_at": timestamp}
                with self.assertRaises(ContractError):
                    self.schemas.validate(invalid, "task-manifest-v1.schema.json")
