"""Bounded, relocatable workspace payloads; no runtime or project-data copying.

Package hashes identify content and completeness, not signatures or acceptance.
Only the explicit application closure below is admitted. Python 3.9+ stdlib.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import zipfile


FORMAT = "heleos.workspace-preview"
VERSION = 1
ENTRYPOINT = "scripts/drawing-workspace.py"
TARGET_PLATFORMS = ["macos-arm64", "windows-x86_64"]
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024
MAX_METADATA_BYTES = 1024 * 1024
# Finder/Explorer metadata only, at otherwise admitted directories. No hidden
# directory, Python cache, extension or arbitrary dotfile exemption is permitted.
OS_METADATA = frozenset((".DS_Store", "desktop.ini", "Thumbs.db"))
SOURCE_FILES = (
    "apps/drawing-workspace/app.js",
    "apps/drawing-workspace/ducts.js",
    "apps/drawing-workspace/air_devices.js",
    "apps/drawing-workspace/arc_family_editor.js",
    "apps/drawing-workspace/revision_checks_editor.js",
    "apps/drawing-workspace/equipment.js",
    "apps/drawing-workspace/fonts-macos.conf",
    "apps/drawing-workspace/index.html",
    "apps/drawing-workspace/path_editor.js",
    "apps/drawing-workspace/styles.css",
    "apps/drawing-workspace/workflow.js",
    "docs/superpowers/specs/2026-09-13-duct-measurement-rules.md",
    "scripts/document_layout.py",
    "scripts/air_device_attributes.py",
    "scripts/air_device_calculation.py",
    "scripts/air_device_rules.py",
    "scripts/air_device_source_producer.py",
    "scripts/local_air_device_vision.py",
    "scripts/project_air_device_takeoff.py",
    "scripts/document_pipeline.py",
    "scripts/document_requirements.py",
    "scripts/document_schedule.py",
    "scripts/drawing-workspace.py",
    "scripts/drawing_scale_labels.py",
    "scripts/duct_calculation.py",
    "scripts/duct_arc_geometry.py",
    "scripts/duct_arc_cover.py",
    "scripts/duct_curve_distance.py",
    "scripts/duct_path_scoring.py",
    "scripts/duct_source_producer.py",
    "scripts/equipment_takeoff.py",
    "scripts/local_duct_vision.py",
    "scripts/local_duct_vision_v2.py",
    "scripts/local_duct_vision_v3.py",
    "scripts/duct_topology.py",
    "scripts/local_mechanical_vision.py",
    "scripts/mechanical_knowledge.py",
    "scripts/mechanical_model_baseline.py",
    "scripts/mechanical_model_inference.py",
    "scripts/mechanical_model_scoring.py",
    "scripts/mechanical_rule_admission_store.py",
    "scripts/mechanical_rule_applicability.py",
    "scripts/mechanical_rule_context.py",
    "scripts/mechanical_rule_decision_store.py",
    "scripts/mechanical_rule_resolution.py",
    "scripts/mechanical_rule_scope.py",
    "scripts/mechanical_rule_validation.py",
    "scripts/mechanical_taxonomy.py",
    "scripts/project_duct_evaluation.py",
    "scripts/project_duct_reading_links.py",
    "scripts/project_duct_refresh.py",
    "scripts/project_duct_revision.py",
    "scripts/project_duct_revision_checks.py",
    "scripts/project_duct_takeoff.py",
    "scripts/project_duct_arc_repartition.py",
    "scripts/duct_arc_obligations.py",
    "scripts/project_knowledge.py",
    "scripts/project_rule_admission.py",
    "scripts/project_rule_resolution.py",
    "scripts/project_schedule_reconciliation.py",
    "scripts/schedule_reconciliation.py",
    "scripts/sheet_geometry.py",
    "scripts/sheet_scale.py",
    "scripts/takeoff_workflow.py",
    "tests/fixtures/duct-takeoff/2026-09-13-rule-examples.json",
    "tests/fixtures/duct-takeoff/2026-09-14-owner-decision.json",
    "tests/fixtures/air-device-takeoff/2026-09-14-rule-examples.json",
    "tests/fixtures/air-device-takeoff/2026-09-15-owner-decision.json",
    "tests/fixtures/air-device-takeoff/2026-09-16-rule-binding.json",
    "tests/fixtures/air-device-takeoff/approved-rule-packet.md",
)
PAYLOAD_SOURCES = {path: path for path in SOURCE_FILES}
PAYLOAD_SOURCES.update({
    "scripts/workspace_package.py": "scripts/workspace_package.py",
    "launch.py": "scripts/launch-drawing-workspace.py",
    "README.txt": "apps/drawing-workspace/package-readme.txt",
})
PAYLOAD_PATHS = tuple(sorted(PAYLOAD_SOURCES))


class PackageError(Exception):
    """An incomplete, changed or unsafe package cannot be consumed."""


def _packed(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True,
                      allow_nan=False, separators=(",", ":")).encode("ascii")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _linked(info):
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _directory(path):
    info = path.lstat()
    if _linked(info) or not stat.S_ISDIR(info.st_mode):
        raise PackageError("Use a regular directory without symlinks or reparse points: " + str(path))


def _root(value):
    try:
        # Canonicalize the containing location (macOS /var and /tmp are aliases).
        # Every payload component below this root is checked without following links.
        path = Path(value).expanduser().resolve(strict=True)
        _directory(path)
        return path
    except (OSError, TypeError, ValueError) as error:
        raise PackageError("The package/source directory is unavailable: " + str(error)) from None


def _relative(value):
    if (not isinstance(value, str) or not value or len(value) > 240 or
            "\\" in value or ":" in value or value.startswith("/") or
            any(part in ("", ".", "..") for part in value.split("/")) or
            any(ord(char) < 32 or ord(char) > 126 for char in value)):
        raise PackageError("Package paths must be bounded ordinary relative paths.")
    return value


def _stat_key(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _read_regular(root, relative, limit=MAX_FILE_BYTES):
    relative = _relative(relative)
    path = root.joinpath(*relative.split("/"))
    for parent in reversed(tuple(path.parents)):
        if parent == root or root in parent.parents:
            _directory(parent)
    before = path.lstat()
    if _linked(before) or not stat.S_ISREG(before.st_mode):
        raise PackageError("Payload must be a regular file, never a link: " + relative)
    if before.st_size > limit:
        raise PackageError("Payload exceeds its byte limit: " + relative)
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        if _stat_key(os.fstat(stream.fileno())) != _stat_key(before):
            raise PackageError("Payload changed while opening: " + relative)
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (len(raw) != before.st_size or len(raw) > limit or
            _stat_key(after) != _stat_key(before) or _stat_key(path.lstat()) != _stat_key(before)):
        raise PackageError("Payload changed while reading: " + relative)
    return raw


def _directories(paths):
    return {str(parent) for path in paths for parent in PurePosixPath(path).parents if str(parent) != "."}


def _tree(root, include_manifest):
    expected = set(PAYLOAD_PATHS) | ({"package.json"} if include_manifest else set())
    directories = _directories(expected)
    found, visited, folded = set(), 0, set()
    pending = [root]
    while pending:
        directory = pending.pop()
        _directory(directory)
        with os.scandir(directory) as iterator:
            for entry in iterator:
                visited += 1
                if visited > 512:
                    raise PackageError("Package tree exceeds its bounded file inventory.")
                relative = Path(entry.path).relative_to(root).as_posix()
                key = relative.casefold()
                if key in folded:
                    raise PackageError("Package paths collide under case folding: " + relative)
                folded.add(key)
                info = entry.stat(follow_symlinks=False)
                if _linked(info):
                    raise PackageError("Package contains a symlink or reparse point: " + relative)
                if stat.S_ISDIR(info.st_mode):
                    if relative not in directories:
                        raise PackageError("Unexpected package directory: " + relative)
                    pending.append(Path(entry.path))
                elif stat.S_ISREG(info.st_mode):
                    if relative in expected:
                        found.add(relative)
                    elif entry.name in OS_METADATA:
                        _read_regular(root, relative, MAX_METADATA_BYTES)
                    else:
                        raise PackageError("Unexpected package file: " + relative)
                else:
                    raise PackageError("Package contains a nonregular entry: " + relative)
    if found != expected:
        raise PackageError("Package is incomplete; missing: " + ", ".join(sorted(expected - found)))


def _manifest(payloads):
    identity = {"format": FORMAT, "version": VERSION, "entrypoint": ENTRYPOINT,
                "target_platforms": list(TARGET_PLATFORMS),
                "files": [{"path": name, "bytes": len(payloads[name]), "sha256": _sha(payloads[name])}
                          for name in PAYLOAD_PATHS]}
    return dict(identity, package_id="workspace_" + _sha(_packed(identity)))


def _unique_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise PackageError("Package manifest contains duplicate JSON keys.")
        value[key] = item
    return value


def _load_manifest(root):
    raw = _read_regular(root, "package.json", MAX_MANIFEST_BYTES)
    value = json.loads(raw, object_pairs_hook=_unique_pairs)
    if (not isinstance(value, dict) or set(value) !=
            {"format", "version", "entrypoint", "target_platforms", "files", "package_id"} or
            value["format"] != FORMAT or type(value["version"]) is not int or value["version"] != VERSION or
            value["entrypoint"] != ENTRYPOINT or value["target_platforms"] != TARGET_PLATFORMS or
            not isinstance(value["files"], list) or len(value["files"]) != len(PAYLOAD_PATHS)):
        raise PackageError("Package manifest does not match the supported application format.")
    paths, total = [], 0
    for item in value["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "bytes", "sha256"}:
            raise PackageError("Package manifest payload entry is invalid.")
        paths.append(_relative(item["path"]))
        if (type(item["bytes"]) is not int or not 0 <= item["bytes"] <= MAX_FILE_BYTES or
                not isinstance(item["sha256"], str) or len(item["sha256"]) != 64 or
                any(char not in "0123456789abcdef" for char in item["sha256"])):
            raise PackageError("Package manifest payload size or hash is invalid.")
        total += item["bytes"]
    if tuple(paths) != PAYLOAD_PATHS or len({path.casefold() for path in paths}) != len(paths):
        raise PackageError("Package manifest must retain the exact ordered application closure.")
    if total > MAX_TOTAL_BYTES:
        raise PackageError("Package exceeds its total byte limit.")
    identity = {key: item for key, item in value.items() if key != "package_id"}
    if value["package_id"] != "workspace_" + _sha(_packed(identity)) or raw != _packed(value):
        raise PackageError("Package manifest identity or canonical bytes changed.")
    return value


def verify_package(root):
    """Read and verify exact payload closure. Never import application code."""
    try:
        root = _root(root)
        _tree(root, True)
        manifest = _load_manifest(root)
        for item in manifest["files"]:
            raw = _read_regular(root, item["path"])
            if len(raw) != item["bytes"] or _sha(raw) != item["sha256"]:
                raise PackageError("Package payload changed: " + item["path"])
        _tree(root, True)
        return manifest
    except PackageError:
        raise
    except (OSError, ValueError, TypeError, RecursionError) as error:
        raise PackageError("Cannot verify the application package: " + str(error)) from None


def _new_destination(value):
    path = Path(value).expanduser().absolute()
    if os.path.lexists(path):
        raise PackageError("Destination already exists; retain it and choose a new path: " + str(path))
    # Parent creation is explicit; links and reparse points remain disallowed.
    ancestor = path.parent
    while not ancestor.exists() and not ancestor.is_symlink():
        ancestor = ancestor.parent
    _root(ancestor)
    path.parent.mkdir(parents=True, exist_ok=True)
    _root(path.parent)
    return path.parent.resolve(strict=True) / path.name


def _write_new(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _write_archive(path, manifest, payloads):
    entries = dict(payloads, **{"package.json": _packed(manifest)})
    with path.open("xb") as stream:
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED, allowZip64=False) as archive:
            for name in sorted(entries):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, entries[name])
        stream.flush()
        os.fsync(stream.fileno())


def build_package(source, output, archive=None):
    """Capture explicit inputs, assemble once, publish the manifest last."""
    try:
        source = _root(source)
        output = _new_destination(output)
        archive = _new_destination(archive) if archive is not None else None
        if archive is not None and (archive == output or output in archive.parents):
            raise PackageError("Place the optional ZIP outside the application package.")
        payloads, total = {}, 0
        for name in PAYLOAD_PATHS:
            raw = _read_regular(source, PAYLOAD_SOURCES[name])
            total += len(raw)
            if total > MAX_TOTAL_BYTES:
                raise PackageError("Application input exceeds its total byte limit.")
            payloads[name] = raw
        manifest = _manifest(payloads)
        if len(_packed(manifest)) > MAX_MANIFEST_BYTES:
            raise PackageError("Application manifest exceeds its byte limit.")
        output.mkdir()
        for name in PAYLOAD_PATHS:
            target = output.joinpath(*name.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            _root(target.parent)
            _write_new(target, payloads[name])
        for name in PAYLOAD_PATHS:
            if _read_regular(source, PAYLOAD_SOURCES[name]) != payloads[name]:
                raise PackageError("Application source changed during assembly: " + PAYLOAD_SOURCES[name])
            if _read_regular(output, name) != payloads[name]:
                raise PackageError("Application output changed during assembly: " + name)
        _tree(output, False)
        _write_new(output / "package.json", _packed(manifest))
        verify_package(output)
        if archive is not None:
            _write_archive(archive, manifest, payloads)
        return manifest
    except PackageError:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise PackageError("Application package could not be assembled: " + str(error)) from None
