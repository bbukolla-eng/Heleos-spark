"""Descriptor-custodied detached Git worktrees for Build Fabric runs."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from tools.helios_build.doctor import _open_private_child_directory, _open_secure_state_root
from tools.helios_build.errors import ContractError
from tools.helios_build.paths import BuildPaths


_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)


def create_detached_worktree(
    paths: BuildPaths, run_identity_sha256: str, base_commit_sha: str
) -> Path:
    """Create exactly ``worktrees/<run identity>`` at the requested commit."""
    if not isinstance(paths, BuildPaths):
        raise ContractError("worktree creation requires BuildPaths")
    _require_hex(run_identity_sha256, 64, "run identity")
    _require_hex(base_commit_sha, 40, "base commit")
    state = _open_secure_state_root(paths.state_root)
    try:
        worktrees = _open_private_child_directory(state, "worktrees")
        try:
            try:
                os.stat(run_identity_sha256, dir_fd=worktrees, follow_symlinks=False)
            except FileNotFoundError:
                pass
            except OSError as error:
                raise ContractError(f"cannot inspect dispatch worktree slot: {error}") from error
            else:
                raise ContractError(f"dispatch worktree already exists: {run_identity_sha256}")
            target = paths.state_root / "worktrees" / run_identity_sha256
            result = _git(
                paths.repo_root,
                "worktree",
                "add",
                "--detach",
                str(target),
                base_commit_sha,
            )
            if result.returncode != 0:
                raise ContractError("cannot create detached dispatch worktree: git worktree add failed")
            try:
                descriptor = os.open(run_identity_sha256, _DIRECTORY_FLAGS, dir_fd=worktrees)
            except OSError as error:
                raise ContractError(f"cannot pin created dispatch worktree: {error}") from error
            try:
                metadata = _verify_recoverable_directory_descriptor(
                    descriptor, "dispatch worktree"
                )
                _verify_git_descriptor(
                    descriptor,
                    paths.repo_root,
                    base_commit_sha,
                    require_clean=True,
                    require_exact_head=True,
                )
                try:
                    if stat.S_IMODE(metadata.st_mode) != 0o700:
                        os.fchmod(descriptor, 0o700)
                    os.fsync(descriptor)
                except OSError as error:
                    raise ContractError(f"cannot pin created dispatch worktree: {error}") from error
                _verify_directory_descriptor(descriptor, "dispatch worktree")
                _verify_git_descriptor(
                    descriptor,
                    paths.repo_root,
                    base_commit_sha,
                    require_clean=True,
                    require_exact_head=True,
                )
            finally:
                os.close(descriptor)
            os.fsync(worktrees)
        finally:
            os.close(worktrees)
    finally:
        os.close(state)
    verify_detached_worktree(paths, target, base_commit_sha, require_clean=True)
    return target


def verify_detached_worktree(
    paths: BuildPaths,
    worktree: Path,
    base_commit_sha: str,
    *,
    expected_inode: tuple[int, int] | None = None,
    require_clean: bool,
    require_exact_head: bool = True,
) -> tuple[int, int]:
    """Pin and verify ownership, inode, detached HEAD/base, and optional cleanliness."""
    descriptor = open_verified_worktree_descriptor(
        paths,
        worktree,
        base_commit_sha,
        expected_inode=expected_inode,
        require_clean=require_clean,
        require_exact_head=require_exact_head,
    )
    try:
        metadata = os.fstat(descriptor)
        return metadata.st_dev, metadata.st_ino
    finally:
        os.close(descriptor)


def open_verified_worktree_descriptor(
    paths: BuildPaths,
    worktree: Path,
    base_commit_sha: str,
    *,
    expected_inode: tuple[int, int] | None = None,
    require_clean: bool,
    require_exact_head: bool = True,
) -> int:
    """Open once, verify through that descriptor, and retain it for launch."""
    _require_hex(base_commit_sha, 40, "base commit")
    expected = paths.state_root / "worktrees"
    try:
        relative = worktree.relative_to(expected)
    except ValueError as error:
        raise ContractError("worktree is outside external worktree custody") from error
    if len(relative.parts) != 1 or relative.name in {"", ".", ".."}:
        raise ContractError("worktree key is unsafe")
    state = _open_secure_state_root(paths.state_root)
    try:
        parent = _open_private_child_directory(state, "worktrees")
        try:
            try:
                descriptor = os.open(relative.name, _DIRECTORY_FLAGS, dir_fd=parent)
            except OSError as error:
                raise ContractError(f"cannot safely pin dispatch worktree: {error}") from error
            try:
                metadata = _verify_directory_descriptor(descriptor, "dispatch worktree")
                inode = (metadata.st_dev, metadata.st_ino)
                if expected_inode is not None and inode != expected_inode:
                    raise ContractError("dispatch worktree inode changed after reservation")
                _verify_git_descriptor(
                    descriptor,
                    paths.repo_root,
                    base_commit_sha,
                    require_clean=require_clean,
                    require_exact_head=require_exact_head,
                )
                return descriptor
            except BaseException:
                os.close(descriptor)
                raise
        finally:
            os.close(parent)
    finally:
        os.close(state)


def recover_or_create_detached_worktree(
    paths: BuildPaths, run_identity_sha256: str, base_commit_sha: str
) -> tuple[Path, tuple[int, int], bool]:
    """Recover one exact orphan or create it once; never delete an uncertain path."""
    target = paths.state_root / "worktrees" / run_identity_sha256
    _require_hex(run_identity_sha256, 64, "run identity")
    _require_hex(base_commit_sha, 40, "base commit")
    recovered = _recover_existing_worktree(paths, run_identity_sha256, base_commit_sha)
    if recovered is not None:
        return target, recovered, False
    try:
        created = create_detached_worktree(paths, run_identity_sha256, base_commit_sha)
        inode = verify_detached_worktree(paths, created, base_commit_sha, require_clean=True)
        return created, inode, True
    except ContractError:
        # A concurrent or torn creator may have published the exact keyed child.
        # Re-prove it once; no uncertain path is changed or removed.
        recovered = _recover_existing_worktree(paths, run_identity_sha256, base_commit_sha)
        if recovered is None:
            raise
        return target, recovered, False


def open_worktree_descriptor(paths: BuildPaths, worktree: Path) -> int:
    """Return an exact descriptor after walking the external root without following links."""
    expected = paths.state_root / "worktrees"
    try:
        relative = worktree.relative_to(expected)
    except ValueError as error:
        raise ContractError("worktree is outside external custody") from error
    if len(relative.parts) != 1:
        raise ContractError("worktree key is unsafe")
    state = _open_secure_state_root(paths.state_root)
    try:
        parent = _open_private_child_directory(state, "worktrees")
        try:
            descriptor = os.open(relative.name, _DIRECTORY_FLAGS, dir_fd=parent)
            _verify_directory_descriptor(descriptor, "dispatch worktree")
            return descriptor
        except BaseException:
            if "descriptor" in locals():
                os.close(descriptor)
            raise
        finally:
            os.close(parent)
    finally:
        os.close(state)


def _verify_directory_descriptor(descriptor: int, label: str) -> os.stat_result:
    metadata = _verify_recoverable_directory_descriptor(descriptor, label)
    if stat.S_IMODE(metadata.st_mode) != 0o700:
        raise ContractError(f"{label} must be a current-owner directory with mode 0700")
    return metadata


def _verify_recoverable_directory_descriptor(descriptor: int, label: str) -> os.stat_result:
    metadata = os.fstat(descriptor)
    current_uid = os.getuid() if hasattr(os, "getuid") else None
    if (
        current_uid is None
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != current_uid
    ):
        raise ContractError(f"{label} must be a current-owner directory")
    return metadata


def _recover_existing_worktree(
    paths: BuildPaths, run_identity_sha256: str, base_commit_sha: str
) -> tuple[int, int] | None:
    state = _open_secure_state_root(paths.state_root)
    try:
        parent = _open_private_child_directory(state, "worktrees")
        try:
            try:
                descriptor = os.open(run_identity_sha256, _DIRECTORY_FLAGS, dir_fd=parent)
            except FileNotFoundError:
                return None
            except OSError as error:
                raise ContractError(f"cannot safely pin reserved worktree: {error}") from error
            try:
                metadata = _verify_recoverable_directory_descriptor(
                    descriptor, "reserved dispatch worktree"
                )
                inode = (metadata.st_dev, metadata.st_ino)
                if inode[0] <= 0 or inode[1] <= 0:
                    raise ContractError("reserved dispatch worktree inode is invalid")
                _verify_git_descriptor(
                    descriptor,
                    paths.repo_root,
                    base_commit_sha,
                    require_clean=True,
                    require_exact_head=True,
                )
                if stat.S_IMODE(metadata.st_mode) != 0o700:
                    try:
                        os.fchmod(descriptor, 0o700)
                        os.fsync(descriptor)
                        os.fsync(parent)
                    except OSError as error:
                        raise ContractError(
                            f"cannot harden proved orphan worktree: {error}"
                        ) from error
                verified = _verify_directory_descriptor(
                    descriptor, "reserved dispatch worktree"
                )
                if (verified.st_dev, verified.st_ino) != inode:
                    raise ContractError("reserved dispatch worktree inode changed while hardening")
                _verify_git_descriptor(
                    descriptor,
                    paths.repo_root,
                    base_commit_sha,
                    require_clean=True,
                    require_exact_head=True,
                )
                return inode
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)
    finally:
        os.close(state)


def _verify_git_descriptor(
    descriptor: int,
    repo_root: Path,
    base_commit_sha: str,
    *,
    require_clean: bool,
    require_exact_head: bool,
) -> None:
    descriptor_path = Path("/dev/fd") / str(descriptor)
    _verify_repository_binding(descriptor, descriptor_path, repo_root)
    head = _git_at(descriptor_path, "rev-parse", "HEAD", pass_fd=descriptor)
    if head.returncode != 0:
        raise ContractError("cannot resolve dispatch worktree HEAD")
    try:
        exact_head = head.stdout.decode("ascii", errors="strict").strip()
    except UnicodeDecodeError as error:
        raise ContractError("dispatch worktree HEAD is malformed") from error
    if require_exact_head and exact_head != base_commit_sha:
        raise ContractError("dispatch worktree HEAD does not match its exact base")
    symbolic = _git_at(descriptor_path, "symbolic-ref", "-q", "HEAD", pass_fd=descriptor)
    if symbolic.returncode == 0:
        raise ContractError("dispatch worktree is not detached")
    if symbolic.returncode not in {0, 1}:
        raise ContractError("cannot prove dispatch worktree detached state")
    if require_clean:
        status_result = _git_at(
            descriptor_path,
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            pass_fd=descriptor,
        )
        if status_result.returncode != 0 or status_result.stdout:
            raise ContractError("dispatch worktree is not clean before execution")


def _verify_repository_binding(
    descriptor: int, descriptor_path: Path, repo_root: Path
) -> None:
    worktree_common = _git_at(
        descriptor_path,
        "rev-parse",
        "--path-format=absolute",
        "--git-common-dir",
        pass_fd=descriptor,
    )
    repository_common = _git(
        repo_root, "rev-parse", "--path-format=absolute", "--git-common-dir"
    )
    worktree_common_path = _absolute_git_path(
        worktree_common, "dispatch worktree Git common directory"
    )
    repository_common_path = _absolute_git_path(
        repository_common, "repository Git common directory"
    )
    resolved_worktree_common, worktree_common_inode = _directory_identity(
        worktree_common_path, "dispatch worktree Git common directory"
    )
    resolved_repository_common, repository_common_inode = _directory_identity(
        repository_common_path, "repository Git common directory"
    )
    if (
        resolved_worktree_common != resolved_repository_common
        or worktree_common_inode != repository_common_inode
    ):
        raise ContractError("dispatch worktree belongs to a different Git repository")

    top_level = _git_at(
        descriptor_path,
        "rev-parse",
        "--path-format=absolute",
        "--show-toplevel",
        pass_fd=descriptor,
    )
    resolved_top, top_inode = _directory_identity(
        _absolute_git_path(top_level, "dispatch worktree top level"),
        "dispatch worktree top level",
    )
    descriptor_metadata = os.fstat(descriptor)
    descriptor_inode = (descriptor_metadata.st_dev, descriptor_metadata.st_ino)
    if top_inode != descriptor_inode:
        raise ContractError("descriptor does not pin the registered worktree top level")

    registrations = _git(repo_root, "worktree", "list", "--porcelain", "-z")
    if registrations.returncode != 0:
        raise ContractError("cannot resolve repository worktree registrations")
    registered = False
    for field in registrations.stdout.split(b"\0"):
        if not field.startswith(b"worktree "):
            continue
        try:
            candidate = Path(os.fsdecode(field[len(b"worktree "):])).resolve(strict=True)
        except (OSError, ValueError):
            continue
        if candidate != resolved_top:
            continue
        _, candidate_inode = _directory_identity(candidate, "registered worktree")
        if candidate_inode == descriptor_inode:
            registered = True
            break
    if not registered:
        raise ContractError("dispatch worktree is not registered to the exact repository")


def _absolute_git_path(
    result: subprocess.CompletedProcess[bytes], label: str
) -> Path:
    if result.returncode != 0:
        raise ContractError(f"cannot resolve {label}")
    try:
        value = os.fsdecode(result.stdout.rstrip(b"\n"))
    except UnicodeError as error:
        raise ContractError(f"{label} is malformed") from error
    path = Path(value)
    if not value or not path.is_absolute():
        raise ContractError(f"{label} is not absolute")
    return path


def _directory_identity(path: Path, label: str) -> tuple[Path, tuple[int, int]]:
    try:
        resolved = path.resolve(strict=True)
        descriptor = os.open(resolved, _DIRECTORY_FLAGS)
    except OSError as error:
        raise ContractError(f"cannot pin {label}: {error}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise ContractError(f"{label} is not a directory")
        return resolved, (metadata.st_dev, metadata.st_ino)
    finally:
        os.close(descriptor)


def _git(repo_root: Path, *argv: str) -> subprocess.CompletedProcess[bytes]:
    return _run_git(["git", "-C", str(repo_root), *argv])


def _git_at(worktree: Path, *argv: str, pass_fd: int | None = None) -> subprocess.CompletedProcess[bytes]:
    return _run_git(["git", "-C", str(worktree), *argv], pass_fd=pass_fd)


def _run_git(argv: list[str], *, pass_fd: int | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"},
            shell=False,
            check=False,
            timeout=30,
            pass_fds=() if pass_fd is None else (pass_fd,),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ContractError(f"cannot operate exact detached worktree: {error}") from error


def _require_hex(value: object, length: int, label: str) -> None:
    if not isinstance(value, str) or len(value) != length or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ContractError(f"{label} must be lowercase hexadecimal")
