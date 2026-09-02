"""Finite subprocess execution with bounded concurrent stream capture."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


_TERM_GRACE_SECONDS = 0.5
_CLEANUP_SECONDS = 1.0


class ProcessLaunchError(OSError):
    """The verified executable could not be launched by ``Popen``."""

    def __init__(self, error: OSError, *, stage: str) -> None:
        super().__init__(error.errno, error.strerror or str(error), error.filename)
        self.stage = stage
        self.filename2 = getattr(error, "filename2", None)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Sanitization-neutral result; callers keep raw bytes outside Git."""

    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool
    capture_incomplete: bool = False
    io_errors: tuple[str, ...] = ()


def run_bounded_process(
    argv: Sequence[str],
    input_bytes: bytes,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
    max_capture_bytes: int,
    *,
    pass_fds: Sequence[int] = (),
) -> ProcessResult:
    """Launch once and apply one absolute deadline to the child and pipe workers."""
    _validate_arguments(
        argv, input_bytes, cwd, env, timeout_seconds, max_capture_bytes, pass_fds
    )
    deadline = time.monotonic() + timeout_seconds
    pipe_fds = _create_process_pipes()
    stdin_read, stdin_write, stdout_read, stdout_write, stderr_read, stderr_write = pipe_fds
    parent_handles = (
        _OwnedFD(stdin_write),
        _OwnedFD(stdout_read),
        _OwnedFD(stderr_read),
    )
    try:
        try:
            process = subprocess.Popen(
                list(argv),
                stdin=stdin_read,
                stdout=stdout_write,
                stderr=stderr_write,
                cwd=cwd,
                env=env,
                shell=False,
                start_new_session=True,
                close_fds=True,
                pass_fds=tuple(pass_fds),
            )
        except OSError as error:
            stage = _popen_error_stage(error, cwd)
            raise ProcessLaunchError(error, stage=stage) from error
    except BaseException:
        for descriptor in pipe_fds:
            _close_descriptor(descriptor)
        raise
    finally:
        _close_descriptor(stdin_read)
        _close_descriptor(stdout_write)
        _close_descriptor(stderr_write)

    stdout_buffer = _CappedBuffer(max_capture_bytes)
    stderr_buffer = _CappedBuffer(max_capture_bytes)
    errors = _IOErrors()
    input_thread = threading.Thread(
        target=_write_input,
        args=(parent_handles[0], input_bytes, errors),
        name="helios-build-stdin",
        daemon=True,
    )
    stdout_thread = threading.Thread(
        target=_drain,
        args=(parent_handles[1], stdout_buffer, errors, "stdout"),
        name="helios-build-stdout",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_drain,
        args=(parent_handles[2], stderr_buffer, errors, "stderr"),
        name="helios-build-stderr",
        daemon=True,
    )
    threads = (input_thread, stdout_thread, stderr_thread)
    started_threads: list[threading.Thread] = []
    try:
        for thread in threads:
            thread.start()
            started_threads.append(thread)
    except RuntimeError as error:
        errors.record("thread-start", error)
        _terminate_process_group(process, started_threads, parent_handles)
        io_errors = errors.snapshot()
        return ProcessResult(
            returncode=process.returncode,
            stdout=bytes(stdout_buffer.content),
            stderr=bytes(stderr_buffer.content),
            timed_out=True,
            stdout_truncated=stdout_buffer.truncated,
            stderr_truncated=stderr_buffer.truncated,
            capture_incomplete=True,
            io_errors=io_errors,
        )

    timed_out = False
    try:
        try:
            process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            timed_out = True
        if not timed_out:
            _join_until(threads, deadline)
            timed_out = any(thread.is_alive() for thread in threads)
        if timed_out:
            _terminate_process_group(process, threads, parent_handles)
        else:
            for handle in parent_handles:
                handle.close()
        if any(thread.is_alive() for thread in threads):
            errors.record("cleanup", TimeoutError("pipe worker did not terminate"))
        io_errors = errors.snapshot()
        return ProcessResult(
            returncode=process.returncode,
            stdout=bytes(stdout_buffer.content),
            stderr=bytes(stderr_buffer.content),
            timed_out=timed_out,
            stdout_truncated=stdout_buffer.truncated,
            stderr_truncated=stderr_buffer.truncated,
            capture_incomplete=bool(io_errors),
            io_errors=io_errors,
        )
    finally:
        for handle in parent_handles:
            handle.close(cleanup=True)


class _OwnedFD:
    """One descriptor that a pipe thread and timeout cleanup may both close."""

    def __init__(self, descriptor: int) -> None:
        self.descriptor = descriptor
        self._closed = False
        self._cleanup_started = False
        self._lock = threading.Lock()

    @property
    def cleanup_started(self) -> bool:
        with self._lock:
            return self._cleanup_started

    def mark_cleanup(self) -> None:
        with self._lock:
            self._cleanup_started = True

    def close(self, *, cleanup: bool = False) -> None:
        with self._lock:
            self._cleanup_started = self._cleanup_started or cleanup
            if self._closed:
                return
            self._closed = True
            _close_descriptor(self.descriptor)


class _IOErrors:
    def __init__(self) -> None:
        self._items: list[str] = []
        self._lock = threading.Lock()

    def record(self, stream_name: str, error: BaseException) -> None:
        with self._lock:
            self._items.append(f"{stream_name}:{type(error).__name__}")

    def snapshot(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._items))


class _CappedBuffer:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.content = bytearray()
        self.truncated = False

    def add(self, chunk: bytes) -> None:
        remaining = self.maximum - len(self.content)
        if remaining > 0:
            self.content.extend(chunk[:remaining])
        if len(chunk) > remaining:
            self.truncated = True


def _drain(
    handle: _OwnedFD,
    buffer: _CappedBuffer,
    errors: _IOErrors,
    stream_name: str,
) -> None:
    try:
        while chunk := _read_from_fd(handle.descriptor):
            buffer.add(chunk)
    except (OSError, ValueError) as error:
        if not handle.cleanup_started:
            errors.record(stream_name, error)
    finally:
        handle.close()


def _write_input(handle: _OwnedFD, content: bytes, errors: _IOErrors) -> None:
    try:
        view = memoryview(content)
        while view:
            written = _write_to_fd(handle.descriptor, view)
            if written <= 0:
                raise OSError("stdin write made no progress")
            view = view[written:]
    except (BrokenPipeError, OSError, ValueError) as error:
        if not handle.cleanup_started:
            errors.record("stdin", error)
    finally:
        handle.close()


def _read_from_fd(descriptor: int) -> bytes:
    return os.read(descriptor, 64 * 1024)


def _write_to_fd(descriptor: int, content: memoryview) -> int:
    return os.write(descriptor, content)


def _create_process_pipes() -> tuple[int, int, int, int, int, int]:
    descriptors: list[int] = []
    try:
        for _ in range(3):
            if hasattr(os, "pipe2"):
                pair = os.pipe2(getattr(os, "O_CLOEXEC", 0))
            else:
                pair = os.pipe()
                os.set_inheritable(pair[0], False)
                os.set_inheritable(pair[1], False)
            descriptors.extend(pair)
    except BaseException:
        for descriptor in descriptors:
            _close_descriptor(descriptor)
        raise
    return tuple(descriptors)  # type: ignore[return-value]


def _join_until(threads: Sequence[threading.Thread], deadline: float) -> None:
    for thread in threads:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))


def _terminate_process_group(
    process: subprocess.Popen[bytes],
    threads: Sequence[threading.Thread],
    handles: Sequence[_OwnedFD],
) -> None:
    process_group = process.pid
    for handle in handles:
        handle.mark_cleanup()
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        pass
    grace_deadline = time.monotonic() + _TERM_GRACE_SECONDS
    while time.monotonic() < grace_deadline:
        if not _process_group_exists(process_group):
            break
        time.sleep(min(0.01, max(0.0, grace_deadline - time.monotonic())))
    if _process_group_exists(process_group):
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    for handle in handles:
        handle.close(cleanup=True)
    cleanup_deadline = time.monotonic() + _CLEANUP_SECONDS
    _join_until(threads, cleanup_deadline)
    if process.poll() is None:
        try:
            process.wait(timeout=max(0.0, cleanup_deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass


def _process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _validate_arguments(
    argv: Sequence[str],
    input_bytes: bytes,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
    max_capture_bytes: int,
    pass_fds: Sequence[int],
) -> None:
    if not argv or any(not isinstance(argument, str) or not argument for argument in argv):
        raise ValueError("argv must contain non-empty string arguments")
    if not isinstance(input_bytes, bytes):
        raise TypeError("input_bytes must be bytes")
    if not isinstance(cwd, Path):
        raise TypeError("cwd must be a Path")
    if not isinstance(env, dict) or any(
        not isinstance(name, str) or not isinstance(value, str) for name, value in env.items()
    ):
        raise TypeError("env must map strings to strings")
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be a positive integer")
    if (
        not isinstance(max_capture_bytes, int)
        or isinstance(max_capture_bytes, bool)
        or max_capture_bytes <= 0
    ):
        raise ValueError("max_capture_bytes must be a positive integer")
    if any(not isinstance(descriptor, int) or descriptor < 0 for descriptor in pass_fds):
        raise ValueError("pass_fds must contain non-negative descriptors")


def _popen_error_stage(error: OSError, cwd: Path) -> str:
    filename = error.filename
    if filename is not None:
        try:
            if os.path.normpath(os.fspath(filename)) == os.path.normpath(os.fspath(cwd)):
                return "cwd"
        except TypeError:
            pass
    return "executable"


def _close_descriptor(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass
