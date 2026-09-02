"""Bounded local subprocess execution shared by preflight and work attempts."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


MAX_CAPTURE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ProcessResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool


def run_bounded_process(
    argv: list[str] | tuple[str, ...],
    *,
    input_bytes: bytes,
    cwd: Path,
    env: dict[str, str],
    timeout_seconds: int,
) -> ProcessResult:
    """Run one trusted local executable with bounded capture and kill its session on timeout."""
    process = subprocess.Popen(
        list(argv),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        shell=False,
        start_new_session=True,
    )
    stdout_buffer = _CappedBuffer()
    stderr_buffer = _CappedBuffer()
    stdout_thread = threading.Thread(target=_drain, args=(process.stdout, stdout_buffer), daemon=True)
    stderr_thread = threading.Thread(target=_drain, args=(process.stderr, stderr_buffer), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    try:
        if process.stdin is not None:
            try:
                process.stdin.write(input_bytes)
                process.stdin.flush()
            except BrokenPipeError:
                pass
            finally:
                process.stdin.close()
        timed_out = False
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_session(process)
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        return ProcessResult(
            returncode=process.returncode,
            stdout=bytes(stdout_buffer.content),
            stderr=bytes(stderr_buffer.content),
            timed_out=timed_out,
            stdout_truncated=stdout_buffer.truncated,
            stderr_truncated=stderr_buffer.truncated,
        )
    finally:
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


class _CappedBuffer:
    def __init__(self) -> None:
        self.content = bytearray()
        self.truncated = False

    def add(self, chunk: bytes) -> None:
        remaining = MAX_CAPTURE_BYTES - len(self.content)
        if remaining > 0:
            self.content.extend(chunk[:remaining])
        if len(chunk) > remaining:
            self.truncated = True


def _drain(stream: BinaryIO | None, buffer: _CappedBuffer) -> None:
    if stream is None:
        return
    while chunk := stream.read(64 * 1024):
        buffer.add(chunk)


def _terminate_session(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
