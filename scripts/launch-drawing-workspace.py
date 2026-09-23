#!/usr/bin/env python3
"""Launch an installed-runtime workspace preview; use Python -I -B launch.py."""
import sys

# Before any package-shadowable imports. -B alone does not prevent pyc reads.
if __name__ == "__main__" and (not sys.flags.isolated or not sys.flags.dont_write_bytecode):
    sys.stderr.write("launcher: Start with the selected Python and -I -B: python -I -B /path/to/launch.py configure|check|run ...\n")
    raise SystemExit(2)
sys.dont_write_bytecode = True

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import stat
import subprocess
import tempfile
import time
import types

MAX_PROFILE_BYTES = 65536
MAX_MANIFEST_BYTES = 262144
MAX_HELPER_BYTES = 1048576
MAX_TOOL_BYTES = 1024 * 1024 * 1024
PROFILE_FORMAT = "heleos.workspace-runtime"
HEX = re.compile(r"[0-9a-f]{64}\Z")


class LauncherError(Exception):
    pass


# Windows SDK/windows-sys 0.61.2 types: never use host C long for DWORD/BOOL.
class WindowsBasicLimits(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", ctypes.c_uint32), ("SchedulingClass", ctypes.c_uint32)]


class WindowsIoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                   "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class WindowsExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", WindowsBasicLimits), ("IoInfo", WindowsIoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class WindowsAccounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int64) for name in ("TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")]
    _fields_ += [(name, ctypes.c_uint32) for name in ("TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses")]


WINDOWS_GATE_BOOTSTRAP = """import sys
if sys.stdin.buffer.read(1) != b'G':
    raise SystemExit(125)
sys.argv = sys.argv[1:]
import runpy
runpy.run_path(sys.argv[0], run_name='__main__')
"""


def windows_gated_argv(python, app_path, app_args):
    return [str(python), "-I", "-B", "-S", "-c", WINDOWS_GATE_BOOTSTRAP, str(app_path), *app_args]


def borrowed_process_handle(child):
    """PRIVATE CPython dependency; Popen retains sole ownership of this Handle."""
    handle_type = getattr(subprocess, "Handle", None)
    handle = getattr(child, "_handle", None)
    if (sys.implementation.name != "cpython" or not isinstance(handle_type, type)
            or not issubclass(handle_type, int) or type(handle) is not handle_type
            or getattr(handle, "closed", None) is not False):
        raise LauncherError("Windows launch requires CPython's recognized open subprocess.Handle; application gate remains closed.")
    value = int(handle)
    if not 0 < value < (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1:
        raise LauncherError("Invalid retained CPython process handle; application gate remains closed.")
    return handle


class WindowsJob:
    """Parent-owned kill-on-close job; injected Kernel32 is for inert tests only."""
    def __init__(self, kernel32=None):
        self.handle = None
        self.child = None
        self.borrowed_handle = None
        self.assigned_child = None
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            raise LauncherError("Windows preview requires a 64-bit runtime.")
        if kernel32 is None:
            if os.name != "nt" or sys.implementation.name != "cpython":
                raise LauncherError("Windows jobs require native Windows CPython.")
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api = kernel32
        H, D, B, P = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int32, ctypes.c_void_p
        signatures = {
            "CreateJobObjectW": ([P, ctypes.c_wchar_p], H),
            "SetHandleInformation": ([H, D, D], B), "GetHandleInformation": ([H, ctypes.POINTER(D)], B),
            "SetInformationJobObject": ([H, ctypes.c_int32, P, D], B),
            "QueryInformationJobObject": ([H, ctypes.c_int32, P, D, ctypes.POINTER(D)], B),
            "AssignProcessToJobObject": ([H, H], B), "IsProcessInJob": ([H, H, ctypes.POINTER(B)], B),
            "TerminateJobObject": ([H, D], B), "CloseHandle": ([H], B),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes = arguments
            function.restype = result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            self._failure("CreateJobObjectW")
        try:
            self._ok(self.api.SetHandleInformation(self._handle(), 1, 0), "SetHandleInformation")
            flags = D()
            self._ok(self.api.GetHandleInformation(self._handle(), ctypes.byref(flags)), "GetHandleInformation")
            if flags.value & 1:
                raise LauncherError("Windows job handle is inheritable; application was not started.")
            limits = WindowsExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000
            self._ok(self.api.SetInformationJobObject(self._handle(), 9, ctypes.byref(limits), ctypes.sizeof(limits)), "SetInformationJobObject")
            observed = self._query(9, WindowsExtendedLimits)
            if observed.BasicLimitInformation.LimitFlags != 0x2000:
                raise LauncherError("Windows job kill-on-close limit readback differs; application was not started.")
        except BaseException as error:
            try:
                self.close()
            except Exception as cleanup:
                raise LauncherError(str(error) + "; cleanup: " + str(cleanup)) from error
            raise

    def _handle(self):
        if self.handle is None:
            raise LauncherError("Windows job is closed.")
        return ctypes.c_void_p(self.handle)

    def _failure(self, operation):
        code = getattr(ctypes, "get_last_error", lambda: 0)()
        raise LauncherError(operation + " failed (Windows error " + str(code) + ").")

    def _ok(self, result, operation):
        if not result:
            self._failure(operation)

    def _query(self, kind, structure):
        result, returned = structure(), ctypes.c_uint32()
        self._ok(self.api.QueryInformationJobObject(self._handle(), kind, ctypes.byref(result), ctypes.sizeof(result), ctypes.byref(returned)), "QueryInformationJobObject")
        if returned.value != ctypes.sizeof(result):
            raise LauncherError("Windows job query returned an unexpected structure size.")
        return result

    def assign(self, child):
        self.child = child
        self.borrowed_handle = borrowed_process_handle(child)
        process = ctypes.c_void_p(int(self.borrowed_handle))
        self._ok(self.api.AssignProcessToJobObject(self._handle(), process), "AssignProcessToJobObject")
        self.assigned_child = child
        member = ctypes.c_int32()
        self._ok(self.api.IsProcessInJob(process, self._handle(), ctypes.byref(member)), "IsProcessInJob")
        if member.value != 1:
            raise LauncherError("Exact child membership in the Windows job was not verified; application gate remains closed.")

    def active_processes(self):
        return self._query(1, WindowsAccounting).ActiveProcesses

    def terminate_and_wait(self, timeout=3):
        self._ok(self.api.TerminateJobObject(self._handle(), 1), "TerminateJobObject")
        deadline = time.monotonic() + timeout
        while self.active_processes() != 0:
            if time.monotonic() >= deadline:
                raise LauncherError("Windows job cleanup timed out with active processes.")
            time.sleep(0.02)

    def close(self):
        if self.handle is not None:
            self._ok(self.api.CloseHandle(self._handle()), "CloseHandle(job)")
            self.handle = None


def run_windows_owned(argv, environment, cwd, pending_signal):
    """Run one gated application and finish exact-handle job cleanup on all paths."""
    job = child = None
    result = None
    primary = None
    cleanup = []
    try:
        if sys.implementation.name != "cpython":
            raise LauncherError("Windows launch requires CPython's guarded retained process handle.")
        job = WindowsJob()
        if pending_signal[0] is None:
            child = subprocess.Popen(windows_gated_argv(argv[0], argv[3], argv[4:]), shell=False,
                                     stdin=subprocess.PIPE, bufsize=0, close_fds=True, cwd=cwd,
                                     env=environment, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            if pending_signal[0] is None:
                job.assign(child)
                if child.poll() is not None:
                    raise LauncherError("Windows startup gate process exited before release.")
            if pending_signal[0] is None:
                if child.stdin.write(b"G") != 1:
                    raise LauncherError("Windows startup gate did not accept its exact release byte.")
                child.stdin.flush()
            child.stdin.close()
            while pending_signal[0] is None:
                try:
                    result = child.wait(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    pass
    except BaseException as error:
        primary = error
    finally:
        if child is not None:
            try:
                if child.stdin is not None and not child.stdin.closed:
                    child.stdin.close()
            except Exception as error:
                cleanup.append("gate close: " + str(error))
        if job is not None:
            try:
                job.terminate_and_wait()
            except Exception as error:
                cleanup.append("job termination: " + str(error))
            finally:
                try:
                    job.close()
                except Exception as error:
                    cleanup.append("job close: " + str(error))
        if child is not None:
            try:
                if (job is None or job.assigned_child is not child) and child.poll() is None:
                    child.kill()  # Public Popen exact retained-handle termination; never a PID lookup.
                child.wait(timeout=3)
            except Exception as error:
                cleanup.append("child reap: " + str(error))
    if primary is not None or cleanup:
        messages = ([str(primary)] if primary is not None else []) + cleanup
        raise LauncherError("Windows application lifetime failed: " + "; ".join(messages)) from primary
    if pending_signal[0] is not None:
        return 128 + pending_signal[0]
    return result if result >= 0 else 128 - result


def _exact(value, keys, description):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise LauncherError("Malformed " + description + ": unexpected or missing fields.")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise LauncherError("Duplicate JSON field: " + key)
        result[key] = value
    return result


def _regular_bytes(path, limit, description):
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise LauncherError(description + " must be a bounded regular file: " + str(path))
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit or len(data) != info.st_size:
            raise LauncherError(description + " changed while being read: " + str(path))
        return data
    except OSError as error:
        raise LauncherError("Cannot read " + description + ": " + str(path) + " (" + str(error) + ")") from error


def _json_file(path, limit, description):
    try:
        return json.loads(_regular_bytes(path, limit, description), object_pairs_hook=_pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(LauncherError("Non-finite JSON value: " + value)))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise LauncherError("Malformed " + description + ": " + str(path)) from error


def _inside(path, root):
    return path == root or root in path.parents


def _external(value, root, description):
    try:
        path = Path(value).expanduser().resolve()
    except (OSError, ValueError, RuntimeError) as error:
        raise LauncherError("Invalid " + description + " path: " + str(value)) from error
    if _inside(path, root):
        raise LauncherError(description + " must be outside the immutable package: " + str(path))
    return path


def _host():
    if sys.version_info < (3, 9):
        raise LauncherError("Python 3.9 or later is required.")
    system = platform.system()
    if system not in ("Darwin", "Windows"):
        raise LauncherError("This preview supports macOS and Windows runtime profiles; observed platform: " + system)
    return {"system": system, "machine": platform.machine()}


def _tool(path, root, description):
    resolved = _external(path, root, description)
    try:
        info = resolved.stat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_TOOL_BYTES:
            raise LauncherError(description + " must be an existing bounded executable file: " + str(resolved))
        if not os.access(str(resolved), os.X_OK):
            raise LauncherError(description + " is not executable: " + str(resolved))
        digest = hashlib.sha256()
        with resolved.open("rb") as stream:
            total = 0
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > MAX_TOOL_BYTES:
                    raise LauncherError(description + " exceeds the executable size limit.")
                digest.update(block)
        if total != info.st_size or resolved.stat().st_mtime_ns != info.st_mtime_ns:
            raise LauncherError(description + " changed while its identity was read.")
        return {"path": str(resolved), "bytes": total, "sha256": digest.hexdigest()}
    except OSError as error:
        raise LauncherError("Cannot inspect " + description + ": " + str(resolved) + " (" + str(error) + ")") from error


def _verify_tool(record, root, description):
    _exact(record, ("path", "bytes", "sha256"), description)
    if (not isinstance(record["path"], str) or not Path(record["path"]).is_absolute()
            or type(record["bytes"]) is not int or not isinstance(record["sha256"], str)
            or not HEX.fullmatch(record["sha256"])):
        raise LauncherError("Malformed " + description + " identity.")
    observed = _tool(record["path"], root, description)
    if observed != record:
        raise LauncherError(description + " identity changed; inspect the installation and configure a new external profile.")


def verify_installation(root):
    """Bootstrap helper verification from isolated stdlib, before any helper code."""
    manifest = _json_file(root / "package.json", MAX_MANIFEST_BYTES, "package manifest")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list) or len(manifest["files"]) > 128:
        raise LauncherError("Malformed bounded package manifest.")
    helpers = [entry for entry in manifest["files"] if isinstance(entry, dict) and entry.get("path") == "scripts/workspace_package.py"]
    if len(helpers) != 1:
        raise LauncherError("Package manifest must identify exactly one verification helper.")
    expected = helpers[0]
    _exact(expected, ("path", "bytes", "sha256"), "package helper entry")
    if type(expected["bytes"]) is not int or not isinstance(expected["sha256"], str) or not HEX.fullmatch(expected["sha256"]):
        raise LauncherError("Malformed verification helper identity.")
    helper_path = root / "scripts" / "workspace_package.py"
    if helper_path.parent.is_symlink():
        raise LauncherError("Package scripts directory must not be a symlink.")
    data = _regular_bytes(helper_path, MAX_HELPER_BYTES, "verification helper")
    if len(data) != expected["bytes"] or hashlib.sha256(data).hexdigest() != expected["sha256"]:
        raise LauncherError("Package verification helper bytes changed; restore or rebuild the package.")
    helper = types.ModuleType("heleos_workspace_package")
    helper.__file__ = str(helper_path)
    try:
        exec(compile(data, str(helper_path), "exec"), helper.__dict__)
        verified = helper.verify_package(root)
    except Exception as error:
        raise LauncherError("Package verification failed: " + str(error)) from error
    if verified != manifest or verified.get("entrypoint") != "scripts/drawing-workspace.py":
        raise LauncherError("Package manifest changed during verification or has an unsupported entrypoint.")
    return verified


def _vision(model, digest, port, runtime, root):
    if all(value is None for value in (model, digest, port, runtime)):
        return None
    port = 11434 if port is None else port
    if (not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_./:-]{1,160}", model)
            or ":" not in model or model.endswith(":latest") or "cloud" in model.lower()
            or not isinstance(digest, str) or not HEX.fullmatch(digest)
            or type(port) is not int or not 1 <= port <= 65535 or runtime is None):
        raise LauncherError("Vision settings require an explicit local model tag, SHA-256 digest, port 1-65535 and installed runtime executable.")
    return {"model": model, "digest": digest, "port": port, "runtime": _tool(runtime, root, "Vision runtime")}


def configure(root, args):
    manifest = verify_installation(root)
    host = _host()
    destination = _external(args.profile, root, "Runtime profile")
    if Path(args.profile).expanduser().is_symlink() or destination.exists():
        raise LauncherError("Runtime profile already exists; select a new external profile path.")
    if not destination.parent.is_dir():
        raise LauncherError("Runtime profile parent directory must already exist: " + str(destination.parent))
    profile = {"format": PROFILE_FORMAT, "version": 1, "platform": host,
               "python": _tool(sys.executable, root, "Python interpreter"),
               "python_version": list(sys.version_info[:3]),
               "tools": {name: _tool(getattr(args, name), root, name) for name in ("heleos", "pdftotext", "pdftoppm")},
               "vision": _vision(args.vision_model, args.vision_digest, args.vision_port, args.vision_runtime_executable, root)}
    data = (json.dumps(profile, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    if len(data) > MAX_PROFILE_BYTES:
        raise LauncherError("Runtime profile exceeds its size limit.")
    try:
        descriptor = os.open(str(destination), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        raise LauncherError("Cannot create external runtime profile: " + str(error)) from error
    return {"operation": "configure", "profile": str(destination), "package_id": manifest["package_id"],
            "state": "configured", "native_execution_verified": False}


def check(root, args):
    manifest = verify_installation(root)
    profile_path = _external(args.profile, root, "Runtime profile")
    profile = _json_file(profile_path, MAX_PROFILE_BYTES, "runtime profile")
    _exact(profile, ("format", "version", "platform", "python", "python_version", "tools", "vision"), "runtime profile")
    if profile["format"] != PROFILE_FORMAT or type(profile["version"]) is not int or profile["version"] != 1:
        raise LauncherError("Unsupported runtime profile format/version.")
    if profile["platform"] != _host():
        raise LauncherError("Runtime profile platform differs from this host; configure a new profile on this machine.")
    _verify_tool(profile["python"], root, "Python interpreter")
    if profile["python"] != _tool(sys.executable, root, "Current Python") or profile["python_version"] != list(sys.version_info[:3]):
        raise LauncherError("Use the exact Python interpreter recorded in this runtime profile.")
    _exact(profile["tools"], ("heleos", "pdftotext", "pdftoppm"), "runtime tool set")
    for name, record in profile["tools"].items():
        _verify_tool(record, root, name)
    vision = profile["vision"]
    if vision is not None:
        _exact(vision, ("model", "digest", "port", "runtime"), "vision settings")
        _verify_tool(vision["runtime"], root, "Vision runtime")
        if _vision(vision["model"], vision["digest"], vision["port"], vision["runtime"]["path"], root) != vision:
            raise LauncherError("Vision settings changed.")
    workspace = _external(args.workspace, root, "Project")
    cache = _external(args.cache, root, "Cache")
    if _inside(root, workspace) or _inside(root, cache):
        raise LauncherError("Project and cache directories must not contain the immutable package.")
    if workspace.exists() and not workspace.is_dir():
        raise LauncherError("Project path must be a directory or a new directory path.")
    parent = workspace if workspace.exists() else workspace.parent
    if not parent.is_dir() or not os.access(str(parent), os.W_OK | os.X_OK):
        raise LauncherError("Project directory or its existing parent is not writable: " + str(parent))
    if not cache.is_dir() or not os.access(str(cache), os.W_OK | os.X_OK):
        raise LauncherError("Cache must be an existing writable external directory: " + str(cache))
    try:
        with tempfile.TemporaryFile(dir=str(cache)):
            pass
    except OSError as error:
        raise LauncherError("Cannot write the external cache: " + str(error)) from error
    port = getattr(args, "port", 0)
    if type(port) is not int or not 0 <= port <= 65535:
        raise LauncherError("Workspace port must be between 0 and 65535.")
    return {"operation": "check", "state": "ready", "package_id": manifest["package_id"],
            "profile": str(profile_path), "workspace": str(workspace), "cache": str(cache),
            "native_execution_verified": False}, profile


def _stop_child(child):
    if os.name != "posix":
        raise LauncherError("Windows cleanup requires its owned job.")
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=3)
    if os.name == "posix":
        # A gracefully exited parent may have left a child in its private group.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def run(root, args):
    ready, profile = check(root, args)
    argv = [profile["python"]["path"], "-I", "-B", str(root / "scripts" / "drawing-workspace.py"),
            "--heleos", profile["tools"]["heleos"]["path"], "--workspace", ready["workspace"],
            "--pdftotext", profile["tools"]["pdftotext"]["path"], "--pdftoppm", profile["tools"]["pdftoppm"]["path"],
            "--port", str(args.port)]
    if profile["vision"] is not None:
        vision = profile["vision"]
        argv.extend(["--vision-model", vision["model"], "--vision-digest", vision["digest"], "--vision-port", str(vision["port"]),
                     "--vision-runtime-executable", vision["runtime"]["path"]])
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON") and key not in ("FONTCONFIG_FILE", "FONTCONFIG_PATH")}
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["XDG_CACHE_HOME"] = ready["cache"]
    if profile["platform"]["system"] == "Darwin":
        environment["FONTCONFIG_FILE"] = str(root / "apps" / "drawing-workspace" / "fonts-macos.conf")
    options = {"start_new_session": True} if os.name == "posix" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    previous = {}
    child = None
    pending_signal = [None]
    def interrupted(signum, frame):
        if pending_signal[0] is None:
            pending_signal[0] = signum
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, interrupted)
        if os.name == "nt":
            return run_windows_owned(argv, environment, ready["cache"], pending_signal)
        child = subprocess.Popen(argv, shell=False, cwd=ready["cache"], env=environment, **options)
        # Record signals until the handle is assigned and wait has finished;
        # either boundary can otherwise lose an interrupt or a started child.
        while pending_signal[0] is None:
            try:
                result = child.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                pass
    finally:
        try:
            for sig in previous:
                signal.signal(sig, signal.SIG_IGN)
            if child is not None:
                # The leader can exit during wait while descendants remain.
                _stop_child(child)
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    if pending_signal[0] is not None:
        return 128 + pending_signal[0]
    return result if result >= 0 else 128 - result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    configure_parser = commands.add_parser("configure", help="Create a new external installed-runtime profile; execute no tools")
    configure_parser.add_argument("--profile", required=True, type=Path)
    for name in ("heleos", "pdftotext", "pdftoppm"):
        configure_parser.add_argument("--" + name, required=True, type=Path)
    configure_parser.add_argument("--vision-model")
    configure_parser.add_argument("--vision-digest")
    configure_parser.add_argument("--vision-port", type=int)
    configure_parser.add_argument("--vision-runtime-executable", type=Path)
    for name in ("check", "run"):
        command = commands.add_parser(name)
        for setting in ("profile", "workspace", "cache"):
            command.add_argument("--" + setting, required=True, type=Path)
        if name == "run":
            command.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent
    try:
        if args.operation == "run":
            return run(root, args)
        result = configure(root, args) if args.operation == "configure" else check(root, args)[0]
        print(json.dumps(result, sort_keys=True), flush=True)
        return 0
    except (LauncherError, OSError, ValueError, subprocess.SubprocessError) as error:
        print("launcher: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
