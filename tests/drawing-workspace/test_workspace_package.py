"""Relocatable package closure and publication using original local byte fixtures."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts/workspace_package.py"
INVENTORY = ROOT / "tests/fixtures/workspace-package/initial-runtime-paths.json"


def load_helper():
    if not HELPER.exists():
        return None
    spec = importlib.util.spec_from_file_location("fixture_workspace_package", HELPER)
    module = importlib.util.module_from_spec(spec)
    exec(compile(HELPER.read_bytes(), str(HELPER), "exec"), module.__dict__)
    return module


class WorkspacePackageTests(unittest.TestCase):
    def setUp(self):
        self.package = load_helper()
        self.assertIsNotNone(self.package, "Relocatable application package builder is not implemented")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "Original source with spaces"
        self.source.mkdir()
        self.mapping = {item["path"]: item["path"] for item in json.loads(INVENTORY.read_text())["files"]}
        # Preserve the original inventory and enumerate admitted runtime
        # extensions independently of the builder's own payload map.
        self.mapping.update({name: name for name in (
            "scripts/duct_arc_geometry.py", "scripts/duct_arc_cover.py", "scripts/duct_curve_distance.py", "scripts/local_duct_vision_v2.py", "scripts/local_duct_vision_v3.py", "scripts/duct_topology.py",
            "scripts/project_duct_arc_repartition.py", "scripts/duct_arc_obligations.py",
            "scripts/project_duct_revision_checks.py",
            "apps/drawing-workspace/arc_family_editor.js",
            "apps/drawing-workspace/revision_checks_editor.js")})
        self.mapping.update({name: name for name in (
            "apps/drawing-workspace/air_devices.js",
            "scripts/air_device_attributes.py", "scripts/air_device_calculation.py",
            "scripts/air_device_rules.py", "scripts/air_device_source_producer.py",
            "scripts/local_air_device_vision.py", "scripts/project_air_device_takeoff.py",
            "scripts/takeoff_workbook.py", "scripts/xlsx_workbook.py",
            "scripts/mechanical_scope.py",
            "tests/fixtures/air-device-takeoff/2026-09-14-rule-examples.json",
            "tests/fixtures/air-device-takeoff/2026-09-15-owner-decision.json",
            "tests/fixtures/air-device-takeoff/2026-09-16-rule-binding.json",
            "tests/fixtures/air-device-takeoff/approved-rule-packet.md")})
        self.mapping.update({"scripts/workspace_package.py": "scripts/workspace_package.py",
            "launch.py": "scripts/launch-drawing-workspace.py",
            "README.txt": "apps/drawing-workspace/package-readme.txt"})
        self.expected = {}
        for destination, origin in self.mapping.items():
            raw = ("Original synthetic payload: " + origin + "\n").encode()
            path = self.source / origin
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            self.expected[destination] = raw

    def build(self, name="Package preview", archive=None):
        output = self.base / name
        return output, self.package.build_package(self.source, output, archive)

    def test_build_relocates_exact_required_closure_and_keeps_data_outside(self):
        secret = self.source / ".heleos/private-workspace/vault/private.pdf"
        secret.parent.mkdir(parents=True)
        secret.write_bytes(b"Do not package project data")
        output, manifest = self.build()
        self.assertEqual(manifest["entrypoint"], "scripts/drawing-workspace.py")
        self.assertEqual(len(manifest["files"]), 76)
        self.assertEqual({item["path"] for item in manifest["files"]}, set(self.expected))
        self.assertIn("tests/fixtures/duct-takeoff/2026-09-14-owner-decision.json", self.expected)
        for item in manifest["files"]:
            raw = self.expected[item["path"]]
            self.assertEqual((output / item["path"]).read_bytes(), raw)
            self.assertEqual(item["bytes"], len(raw))
            self.assertEqual(item["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertFalse((output / ".heleos").exists())
        moved = self.base / "Relocated preview"
        output.rename(moved)
        shutil.rmtree(self.source)
        self.assertEqual(self.package.verify_package(moved), manifest)

    def test_identical_payloads_have_identical_manifest_and_zip_bytes(self):
        first_zip, second_zip = self.base / "one.zip", self.base / "two.zip"
        first, left = self.build("first", first_zip)
        for path in self.source.rglob("*"):
            if path.is_file():
                os.utime(path, (1234567890, 1234567890))
        second, right = self.build("second", second_zip)
        self.assertEqual(left, right)
        self.assertEqual((first / "package.json").read_bytes(), (second / "package.json").read_bytes())
        self.assertEqual(first_zip.read_bytes(), second_zip.read_bytes())
        with zipfile.ZipFile(first_zip) as archive:
            self.assertEqual(archive.namelist(), sorted(list(self.expected) + ["package.json"]))
            self.assertTrue(all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist()))
            self.assertEqual(archive.read("launch.py"), self.expected["launch.py"])

    def test_changed_payload_gets_different_package_identity(self):
        _, before = self.build("before")
        (self.source / "apps/drawing-workspace/app.js").write_bytes(b"Changed original fixture")
        _, after = self.build("after")
        self.assertNotEqual(before["package_id"], after["package_id"])

    def test_existing_output_and_archive_are_never_overwritten(self):
        output, manifest = self.build()
        before = (output / "package.json").read_bytes()
        with self.assertRaises(self.package.PackageError):
            self.package.build_package(self.source, output)
        self.assertEqual((output / "package.json").read_bytes(), before)
        archive = self.base / "retained.zip"
        archive.write_bytes(b"Retained original evidence")
        with self.assertRaises(self.package.PackageError):
            self.package.build_package(self.source, self.base / "other", archive)
        self.assertEqual(archive.read_bytes(), b"Retained original evidence")

    def test_missing_changed_and_injected_code_are_rejected(self):
        for action in ("missing", "changed", "extra", "pyc", "empty-cache"):
            with self.subTest(action=action):
                output, _ = self.build(action)
                if action == "missing":
                    (output / "scripts/sheet_geometry.py").unlink()
                elif action == "changed":
                    (output / "scripts/sheet_geometry.py").write_bytes(b"foreign code")
                elif action == "extra":
                    (output / "scripts/sitecustomize.py").write_text("raise RuntimeError()")
                elif action == "pyc":
                    (output / "scripts/duct_calculation.pyc").write_bytes(b"old cached code")
                else:
                    (output / "scripts/__pycache__").mkdir()
                with self.assertRaises(self.package.PackageError):
                    self.package.verify_package(output)

    def test_manifest_cannot_declare_traversal_or_injected_files(self):
        for name in ("../outside.py", "C:/outside.py", "scripts\\foreign.py", "scripts/extra.py"):
            with self.subTest(name=name):
                output, _ = self.build("case-" + str(len(list(self.base.iterdir()))))
                path = output / "package.json"
                value = json.loads(path.read_text())
                value["files"][0]["path"] = name
                path.write_text(json.dumps(value))
                with self.assertRaises(self.package.PackageError):
                    self.package.verify_package(output)

    def test_noncanonical_duplicate_and_oversize_manifests_reject(self):
        output, _ = self.build()
        path = output / "package.json"
        original = path.read_bytes()
        for raw in (b"{\"version\":1,\"version\":1}", original + b" ", b" " * (128 * 1024 + 1)):
            path.write_bytes(raw)
            with self.assertRaises(self.package.PackageError):
                self.package.verify_package(output)

    def test_only_named_regular_os_metadata_is_ignored(self):
        output, manifest = self.build()
        for name in (".DS_Store", "desktop.ini", "Thumbs.db"):
            (output / name).write_bytes(b"Original OS metadata fixture")
        self.assertEqual(self.package.verify_package(output), manifest)
        (output / ".hidden-code").write_bytes(b"undeclared")
        with self.assertRaises(self.package.PackageError):
            self.package.verify_package(output)

    def test_source_symlink_and_symlinked_parent_are_rejected(self):
        for parent in (False, True):
            with self.subTest(parent=parent):
                source = self.base / ("alias-source-" + str(parent))
                shutil.copytree(self.source, source)
                target = source / ("scripts" if parent else "scripts/sheet_geometry.py")
                original = target.with_name(target.name + "-original")
                target.rename(original)
                target.symlink_to(original, target_is_directory=parent)
                with self.assertRaises(self.package.PackageError):
                    self.package.build_package(source, self.base / ("alias-output-" + str(parent)))

    def test_package_symlink_and_manifest_symlink_are_rejected(self):
        for relative in ("scripts/sheet_geometry.py", "package.json", ".DS_Store"):
            with self.subTest(relative=relative):
                output, _ = self.build("case-" + str(len(list(self.base.iterdir()))))
                path = output / relative
                if path.exists():
                    path.unlink()
                path.symlink_to(self.source / "scripts/sheet_geometry.py")
                with self.assertRaises(self.package.PackageError):
                    self.package.verify_package(output)

    def test_oversized_payload_is_rejected_before_copy(self):
        path = self.source / "scripts/sheet_geometry.py"
        with path.open("wb") as stream:
            stream.truncate(8 * 1024 * 1024 + 1)
        output = self.base / "oversized"
        with self.assertRaises(self.package.PackageError):
            self.package.build_package(self.source, output)
        self.assertFalse((output / "package.json").exists())

    def test_cli_builds_and_verifies_without_running_application(self):
        output = self.base / "CLI output"
        result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "scripts/package-drawing-workspace.py"),
            "--source", str(self.source), "--output", str(output), "--zip", str(self.base / "CLI.zip")],
            cwd=self.base, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.package.verify_package(output))

    def test_source_change_during_copy_leaves_incomplete_output_without_manifest(self):
        output = self.base / "Interrupted source assembly"
        write = self.package._write_new
        changed = [False]
        def write_then_change(path, raw):
            write(path, raw)
            if not changed[0]:
                changed[0] = True
                (self.source / "scripts/sheet_geometry.py").write_bytes(b"changed while assembling")
        with mock.patch.object(self.package, "_write_new", side_effect=write_then_change):
            with self.assertRaises(self.package.PackageError):
                self.package.build_package(self.source, output)
        self.assertTrue(output.is_dir())
        self.assertTrue(any(output.iterdir()))
        self.assertFalse((output / "package.json").exists())
        with self.assertRaises(self.package.PackageError):
            self.package.verify_package(output)

    def test_copy_failure_keeps_evidence_and_never_publishes_manifest(self):
        output = self.base / "Interrupted output assembly"
        write = self.package._write_new
        def limited_write(path, raw):
            if path.name == "launch.py":
                raise OSError("Original out-of-space fixture")
            write(path, raw)
        with mock.patch.object(self.package, "_write_new", side_effect=limited_write):
            with self.assertRaises(self.package.PackageError):
                self.package.build_package(self.source, output)
        self.assertTrue((output / "README.txt").is_file())
        self.assertFalse((output / "package.json").exists())

    def test_total_byte_budget_rejects_before_output_publication(self):
        output = self.base / "Aggregate overflow"
        with mock.patch.object(self.package, "MAX_TOTAL_BYTES", 256):
            with self.assertRaises(self.package.PackageError):
                self.package.build_package(self.source, output)
        self.assertFalse((output / "package.json").exists())

    def test_output_and_archive_paths_cannot_alias_each_other(self):
        for relative in ("Alias output", "Alias output/package.zip"):
            with self.subTest(relative=relative):
                output = self.base / "Alias output"
                with self.assertRaises(self.package.PackageError):
                    self.package.build_package(self.source, output, self.base / relative)
                self.assertFalse((output / "package.json").exists())

    def test_case_variant_injection_is_not_accepted_as_os_metadata(self):
        output, _ = self.build()
        (output / ".DS_Store").write_bytes(b"Original metadata")
        # The spelling is exact even on case-insensitive filesystems.
        (output / ".DS_Store").rename(output / ".ds_store")
        with self.assertRaises(self.package.PackageError):
            self.package.verify_package(output)

    def test_windows_reparse_attribute_rejects_payload_file_and_directory(self):
        original = Path.lstat
        class ReparseInfo:
            def __init__(self, value):
                self.value = value
                self.st_file_attributes = 0x400
            def __getattr__(self, key):
                return getattr(self.value, key)
        for relative in ("scripts", "scripts/sheet_geometry.py"):
            target = (self.source / relative).resolve()
            def marked(path):
                value = original(path)
                return ReparseInfo(value) if path == target else value
            with self.subTest(relative=relative), mock.patch.object(Path, "lstat", new=marked):
                with self.assertRaises(self.package.PackageError):
                    self.package.build_package(self.source, self.base / "reparse-output")
            self.assertFalse((self.base / "reparse-output/package.json").exists())


if __name__ == "__main__":
    unittest.main()
