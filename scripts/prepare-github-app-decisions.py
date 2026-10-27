#!/usr/bin/env python3
"""Prepare a private, unresolved owner draft bound to visible main.

This local command never changes accounts or grants owner or release authority.
Concurrent writers must remain quiescent: Git refs and filesystem publication
cannot be locked together. Observed changes fail closed; existing outputs are
never replaced.
"""

import errno
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile


sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location(
    "apply_github_app_decisions", Path(__file__).with_name("apply-github-app-decisions.py"))
applier = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(applier)
release = applier.release
state = applier.state
StateError = applier.StateError
require = applier.require
sha256 = applier.sha256
INBOX = "OWNER_ACTION_REQUIRED"
DRAFT_FIELDS = ("name", "disposition", "version_or_digest", "permissions", "egress",
                "evaluation", "decision_evidence", "installation_evidence")


def directory_identity(path):
    info = release.canonical(path).lstat()
    require(stat.S_ISDIR(info.st_mode), "invalid_output", "Output directory must already exist.")
    return info.st_dev, info.st_ino, info.st_mode


def output_path(root, value):
    target = release.canonical(value)
    parent = release.canonical(root / INBOX)
    require(target.parent == parent and target.suffix == ".json",
            "invalid_output", "Output must be a direct JSON child of the owner directory.")
    directory_identity(parent)
    require(not state.git(root, "ls-files", "-z", "--", str(target))[1]
            and state.git(root, "check-ignore", "--quiet", "--no-index", str(target),
                          allowed=(0, 1, 128))[0] == 0,
            "invalid_output", "Output must be ignored and untracked.")
    absent(target)
    return target


def absent(target):
    try:
        target.lstat()
    except FileNotFoundError:
        return
    raise StateError("output_exists", "Output already exists; select a fresh filename.")


def check_source(root, head, committed, registry, original=None):
    if os.name != "nt":
        tree = state.git(root, "ls-tree", "-z", head, "--", release.APP_PATH)[1]
        require(bool(applier.identity(registry)[2] & 0o111) == tree.startswith(b"100755 "),
                "registry_drift", "Registry executable mode differs from its committed mode.")
    # An empty proposal cannot match a strictly validated registry. This keeps
    # the applier's already-applied exception unavailable to draft preparation.
    current, identity, already = applier.check_main(
        root, head, committed, b"", registry, original)
    require(current == committed and not already, "registry_drift",
            "Working registry must match the committed regular file.")
    return identity


def recheck(root, head, committed, registry, original, target, parent_identity):
    try:
        check_source(root, head, committed, registry, original)
        require(directory_identity(target.parent) == parent_identity,
                "state_changed", "Output directory changed during preparation.")
        output_path(root, target)
        require(state.git_text(root, "rev-parse", "--verify", "HEAD^{commit}") == head
                and state.branch_name(root) == "main"
                and applier.identity(registry) == original
                and release.read_file(registry) == committed,
                "state_changed", "Main or registry changed during preparation.")
    except (StateError, OSError) as error:
        if isinstance(error, StateError) and error.code == "output_exists":
            raise
        raise StateError("state_changed", "Source or destination changed during preparation.") from None


def same_inode(left, right):
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def verify_temporary(path, descriptor, initial, raw):
    info = path.lstat()
    opened = os.fstat(descriptor)
    require(stat.S_ISREG(info.st_mode) and same_inode(info, initial)
            and same_inode(opened, initial) and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o600
            and release.read_file(path) == raw,
            "state_changed", "Private temporary draft changed during preparation.")


def publish(root, head, committed, registry, original, target, raw, report):
    temporary = None
    temporary_info = None
    descriptor = None
    directory_fd = None
    try:
        parent_identity = directory_identity(target.parent)
        directory_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                               | getattr(os, "O_NOFOLLOW", 0))
        opened_parent = os.fstat(directory_fd)
        require((opened_parent.st_dev, opened_parent.st_ino, opened_parent.st_mode)
                == parent_identity, "state_changed", "Output directory changed during preparation.")
        descriptor, name = tempfile.mkstemp(
            prefix=".github-app-decisions-", suffix=".tmp", dir=str(target.parent))
        temporary = Path(name)
        temporary_info = os.fstat(descriptor)
        # Retain the open descriptor through cleanup so the owned inode cannot
        # be recycled if another writer unlinks its temporary name.
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            os.fchmod(descriptor, 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(descriptor)
        recheck(root, head, committed, registry, original, target, parent_identity)
        verify_temporary(temporary, descriptor, temporary_info, raw)
        require(directory_identity(target.parent) == parent_identity
                and applier.identity(registry) == original,
                "state_changed", "Source or destination changed before publication.")
        # A hard link publishes the complete fsynced inode in one no-replace
        # operation. EEXIST also covers a competitor that wins after inspection.
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError:
            raise StateError("output_exists", "Output already exists; select a fresh filename.") from None
        report["write_performed"] = True
        final = target.lstat()
        require(directory_identity(target.parent) == parent_identity
                and stat.S_ISREG(final.st_mode) and same_inode(final, temporary_info)
                and stat.S_IMODE(final.st_mode) == 0o600
                and release.read_file(target) == raw
                and sha256(release.read_file(target)) == report["packet"]["sha256"],
                "write_verification_failed", "Published draft failed verification.")
    finally:
        try:
            if temporary is not None and temporary_info is not None:
                # Anchor cleanup to the opened parent; never follow a replaced
                # directory path or remove a competitor's temporary inode.
                try:
                    remaining = os.stat(temporary.name, dir_fd=directory_fd, follow_symlinks=False)
                    if same_inode(remaining, temporary_info):
                        os.unlink(temporary.name, dir_fd=directory_fd)
                    else:
                        raise StateError("state_changed", "Private temporary draft identity changed.")
                except FileNotFoundError:
                    require(not report["write_performed"], "state_changed",
                            "Private temporary draft disappeared during publication.")
            if directory_fd is not None:
                try:
                    os.fsync(directory_fd)
                except OSError as error:
                    if error.errno not in (errno.EINVAL, errno.ENOTSUP):
                        raise
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if directory_fd is not None:
                os.close(directory_fd)
    # Reverify after temporary-name cleanup and directory durability, including
    # the source snapshot. A post-publication failure retains write_performed
    # so the report never claims that a published file was not written.
    final = target.lstat()
    require(directory_identity(target.parent) == parent_identity
            and same_inode(final, temporary_info) and stat.S_ISREG(final.st_mode)
            and stat.S_IMODE(final.st_mode) == 0o600 and final.st_nlink == 1
            and release.read_file(target) == raw and sha256(release.read_file(target)) == sha256(raw),
            "write_verification_failed", "Published draft failed final verification.")
    try:
        check_source(root, head, committed, registry, original)
    except (StateError, OSError):
        raise StateError("state_changed", "Source changed during publication.") from None


def inspect(args, report):
    applier.reject_git_overrides()
    try:
        root, observed = release.main_checkout(release.canonical(args.repo))
    except StateError as error:
        if error.code == "main_unavailable":
            raise StateError("main_checkout_unavailable", "Exactly one main checkout is required.") from None
        raise
    head = release.digest(observed["head"], 40)
    registry = release.canonical(root / release.APP_PATH)
    committed, _ = release.committed_file(root, head, release.APP_PATH)
    _, records = applier.registry_lines(committed)
    original = check_source(root, head, committed, registry)
    require(not observed["tracked_changes"], "tracked_changes", "Main contains tracked changes.")
    target = output_path(root, args.output)
    apps = []
    for record in records:
        app = dict.fromkeys(DRAFT_FIELDS)
        app["name"] = record["values"]["name"]
        apps.append(app)
    draft = {"schema": "heleos.github-app-decisions/v1",
             "repository": "bbukolla-eng/Heleos-spark", "expected_head": head,
             "expected_registry_sha256": sha256(committed), "decision_owner": "Bekim Bukolla",
             "decision_date": None, "apps": apps}
    raw = (json.dumps(draft, ensure_ascii=True, indent=2) + "\n").encode("utf-8")
    report["main"] = {"path": str(root), "head": head}
    report["registry"] = {"path": str(registry), "sha256": sha256(committed)}
    report["packet"] = {"path": str(target), "sha256": sha256(raw)}
    publish(root, head, committed, registry, original, target, raw, report)
    report["status"] = "PREPARED"


def main(argv=None):
    report = {"schema_version": 1, "status": "FAIL", "main": {"path": None, "head": None},
              "registry": {"path": None, "sha256": None}, "packet": {"path": None, "sha256": None},
              "unresolved_fields": 29, "write_performed": False, "account_changes_performed": False,
              "owner_authenticity_verified": False, "release_authority": False, "errors": []}
    args = None
    try:
        parser = applier.Parser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--repo", default=".", help="checkout or nested directory (default: current directory)")
        parser.add_argument("--output", required=True, help="fresh ignored JSON path in the visible owner directory")
        parser.add_argument("--human", action="store_true", help="print a concise human-readable report")
        args = parser.parse_args(argv)
        inspect(args, report)
    except StateError as error:
        report["errors"].append({"code": error.code, "message": "Draft preparation failed validation."})
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError, StopIteration):
        report["errors"].append({"code": "preparation_failed", "message": "Local draft preparation failed."})
    if report["errors"]:
        report["status"] = "FAIL"
        # User-supplied paths may themselves contain sensitive strings. Failed
        # reports expose fixed diagnostics, never input paths or exception text.
        report["main"] = {"path": None, "head": None}
        report["registry"] = {"path": None, "sha256": None}
        report["packet"] = {"path": None, "sha256": None}
    if args is not None and args.human:
        print(report["status"])
        print("Unresolved fields: 29")
        print("Draft write: " + ("yes" if report["write_performed"] else "no") + "; account changes: no")
        print("Owner authenticity verified: no; release authority: no")
        for error in report["errors"]:
            print(error["code"] + ": " + error["message"])
    else:
        print(json.dumps(report, sort_keys=True, ensure_ascii=True, separators=(",", ":")))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
