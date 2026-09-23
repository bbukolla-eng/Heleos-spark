"""Package copied native evidence; authenticate neither execution nor authority.

All source descriptors and their lexical directory chains remain pinned until
publication. The restricted receipt schema makes its sorted JSON JCS-equivalent;
canonical_json is not a general-purpose JCS implementation.
"""

import hashlib
import io
import json
import math
import os
import re
import secrets
import selectors
import stat
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace


SUMMARY_MAX_BYTES = 16 * 1024 * 1024
MANIFEST_MAX_BYTES = 1024 * 1024
RECEIPT_MAX_BYTES = 1024 * 1024
TRANSCRIPT_MAX_BYTES = 64 * 1024 * 1024
TOTAL_MAX_BYTES = 512 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 4096
MAX_JSON_DEPTH = 32
MAX_JSON_VALUES = 100000
MAX_INTEGER_TOKEN_LENGTH = 16
MAX_SAFE_INTEGER = 9007199254740991
GIT_TIMEOUT_SECONDS = 10
CHUNK_BYTES = 65536
MATRIX = (
    ("core-backup-restore", ("-p", "heleos-core", "--test", "backup_restore")),
    ("core-store", ("-p", "heleos-core", "--lib", "store::tests")),
    ("core-backup", ("-p", "heleos-core", "--lib", "backup::tests")),
    ("platform-fs", ("-p", "heleos-platform-fs", "--lib")),
    ("cli-unit", ("-p", "heleos-cli", "--bin", "heleos")),
    ("cli-integration", ("-p", "heleos-cli", "--test", "cli")),
    ("workspace-all", ("--workspace", "--all-targets", "--all-features")),
)
TRANSCRIPT_NAMES = tuple(suite + "." + kind + ".txt"
                         for suite, _ in MATRIX for kind in ("list", "run"))
AUTHORITY = {
    "ci_authority": False,
    "independent_native_authentication": False,
    "native_execution_independently_proven": False,
    "owner_approval": False,
    "release_approval": False,
}
ERRORS = {
    "UNSUPPORTED_PLATFORM": "POSIX controller required.",
    "INVALID_ARGUMENT": "Invalid export arguments.",
    "INVALID_PATH": "Unsafe export path.",
    "INVALID_CANDIDATE": "Candidate commit unavailable.",
    "UNSAFE_SOURCE": "Unsafe source file.",
    "INPUT_LIMIT": "Input limit exceeded.",
    "INVALID_JSON": "Invalid input JSON.",
    "CONTRACT_MISMATCH": "Evidence contract mismatch.",
    "HASH_MISMATCH": "Evidence hash mismatch.",
    "SOURCE_CHANGED": "Source changed during export.",
    "OUTPUT_EXISTS": "Output already exists.",
    "WRITE_FAILED": "Archive write failed.",
    "PUBLISH_UNCERTAIN": "Archive publication requires inspection.",
    "INTERNAL_ERROR": "Export failed.",
}
SCHEMA = "heleos.foundation-native-evidence-export/v1"
RESULT_SCHEMA = "heleos.foundation-native-evidence-export-result/v1"
HELP = ("Usage: python3 scripts/export-foundation-native-evidence.py "
        "--repo PATH --candidate FULL_SHA --summary PATH --manifest PATH "
        "--receipt PATH --output PATH [--human]\n\n"
        "Package structurally and hash-consistent evidence on a POSIX controller.\n"
        "All paths must be absolute; the private output parent must exist.\n"
        "No independent native authentication, CI authority, owner approval, "
        "or release approval.\n")


class ExportError(Exception):
    def __init__(self, code):
        self.code = code if code in ERRORS else "INTERNAL_ERROR"
        super().__init__(self.code)


def require(condition, code="CONTRACT_MISMATCH"):
    if not condition:
        raise ExportError(code)


def is_posix_host():
    return os.name == "posix"


def canonical_json(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "INVALID_JSON")
        result[key] = value
    return result


def _integer(token):
    require(len(token) <= MAX_INTEGER_TOKEN_LENGTH, "INPUT_LIMIT")
    value = int(token)
    require(abs(value) <= MAX_SAFE_INTEGER, "INPUT_LIMIT")
    return value


def _constant(_token):
    raise ExportError("INVALID_JSON")


def strict_json(data):
    try:
        text = data.decode("utf-8", errors="strict")
        require(not text.startswith("\ufeff"), "INVALID_JSON")
        depth, quoted, escaped = 0, False, False
        # Count scalar/container starts before json.loads allocates its tree.
        # Object keys are deliberately excluded from the value budget.
        values, token = 0, False
        frames = []
        for char in text:
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                if not frames or frames[-1] != "key":
                    values += 1
                quoted = True
                token = False
            elif char in "[{":
                values += 1
                depth += 1
                require(depth <= MAX_JSON_DEPTH, "INPUT_LIMIT")
                frames.append("key" if char == "{" else "array")
                token = False
            elif char in "]}":
                depth -= 1
                require(depth >= 0, "INVALID_JSON")
                frames.pop()
                token = False
            elif char == ":":
                if frames:
                    frames[-1] = "object"
                token = False
            elif char == ",":
                if frames and frames[-1] == "object":
                    frames[-1] = "key"
                token = False
            elif char in " \t\r\n":
                token = False
            elif not token:
                values += 1
                token = True
            require(values <= MAX_JSON_VALUES, "INPUT_LIMIT")
        value = json.loads(text, object_pairs_hook=reject_duplicate_pairs,
                           parse_int=_integer, parse_constant=_constant)
        pending, count = [value], 0
        while pending:
            item = pending.pop()
            count += 1
            require(count <= MAX_JSON_VALUES, "INPUT_LIMIT")
            if type(item) is dict:
                # Keys are strings too, but the count is of JSON values.
                for key in item:
                    require(not any(0xD800 <= ord(c) <= 0xDFFF for c in key), "INVALID_JSON")
                pending.extend(item.values())
            elif type(item) is list:
                pending.extend(item)
            elif type(item) is str:
                require(not any(0xD800 <= ord(c) <= 0xDFFF for c in item), "INVALID_JSON")
            elif type(item) is float:
                require(math.isfinite(item), "INVALID_JSON")
        return value
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise ExportError("INVALID_JSON") from None


def _parts(path):
    require(type(path) is str and path.startswith("/") and "\x00" not in path,
            "INVALID_PATH")
    parts = path.split("/")
    require(".." not in parts, "INVALID_PATH")
    parts = [part for part in parts if part not in ("", ".")]
    require(bool(parts), "INVALID_PATH")
    return parts


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode


def _snapshot(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class Directory:
    """A lexical absolute path with every ancestor held and rechecked."""
    def __init__(self, parts):
        self.chain = []
        try:
            fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self.chain.append((fd, None, _identity(os.fstat(fd))))
            for part in parts:
                parent_fd = fd
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                             dir_fd=parent_fd)
                self.chain.append((fd, part, _identity(os.fstat(fd))))
                require(_identity(os.stat(part, dir_fd=parent_fd, follow_symlinks=False))
                        == self.chain[-1][2], "INVALID_PATH")
            self.fd = fd
            self.identity = self.chain[-1][2]
        except (OSError, ValueError):
            self.close()
            raise ExportError("INVALID_PATH") from None
        except BaseException:
            self.close()
            raise

    def recheck(self, code="SOURCE_CHANGED"):
        try:
            require(_identity(os.stat("/", follow_symlinks=False)) == self.chain[0][2], code)
            for index, (fd, name, identity) in enumerate(self.chain):
                require(_identity(os.fstat(fd)) == identity, code)
                if index:
                    require(_identity(os.stat(name, dir_fd=self.chain[index - 1][0],
                                              follow_symlinks=False)) == identity, code)
        except OSError:
            raise ExportError(code) from None

    def close(self):
        for fd, _, _ in reversed(self.chain):
            os.close(fd)
        self.chain = []


def _safe_source(info):
    return (stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and not info.st_mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX | 0o022))


def _read_bounded(fd, cap):
    require(os.fstat(fd).st_size <= cap, "INPUT_LIMIT")
    os.lseek(fd, 0, os.SEEK_SET)
    chunks, size = [], 0
    while size <= cap:
        chunk = os.read(fd, min(CHUNK_BYTES, cap + 1 - size))
        if not chunk:
            break
        size += len(chunk)
        require(size <= cap, "INPUT_LIMIT")
        chunks.append(chunk)
    return b"".join(chunks)


def open_source(path, cap, resources):
    parts = _parts(path)
    parent = Directory(parts[:-1])
    resources.append(parent)
    fd = None
    try:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=parent.fd)
        info = os.fstat(fd)
        require(_safe_source(info), "UNSAFE_SOURCE")
        snapshot = _snapshot(info)
        require(_snapshot(os.stat(parts[-1], dir_fd=parent.fd, follow_symlinks=False))
                == snapshot, "SOURCE_CHANGED")
        parent.recheck()
        data = _read_bounded(fd, cap)
        require(_snapshot(os.fstat(fd)) == snapshot, "SOURCE_CHANGED")
        source = SimpleNamespace(fd=fd, parent=parent, name=parts[-1],
                                 snapshot=snapshot, cap=cap, data=data,
                                 sha256=hashlib.sha256(data).hexdigest())
        return source
    except OSError:
        if fd is not None:
            os.close(fd)
        raise ExportError("UNSAFE_SOURCE") from None
    except BaseException:
        if fd is not None:
            os.close(fd)
        raise


def _transcript_inventory(parent, code="CONTRACT_MISMATCH"):
    found = set()
    try:
        with os.scandir(parent.fd) as entries:
            for count, entry in enumerate(entries, 1):
                require(count <= MAX_DIRECTORY_ENTRIES, "INPUT_LIMIT" if code != "SOURCE_CHANGED" else code)
                if entry.name.lower().endswith((".list.txt", ".run.txt")):
                    require(entry.name in TRANSCRIPT_NAMES, code)
                    found.add(entry.name)
        require(found == set(TRANSCRIPT_NAMES), code)
    except OSError:
        raise ExportError(code) from None


def recheck_sources(sources, transcript_parent):
    try:
        for source in sources:
            source.parent.recheck()
            require(_snapshot(os.fstat(source.fd)) == source.snapshot, "SOURCE_CHANGED")
            require(_snapshot(os.stat(source.name, dir_fd=source.parent.fd,
                                      follow_symlinks=False)) == source.snapshot, "SOURCE_CHANGED")
            require(hashlib.sha256(_read_bounded(source.fd, source.cap)).hexdigest()
                    == source.sha256, "SOURCE_CHANGED")
            require(_snapshot(os.fstat(source.fd)) == source.snapshot, "SOURCE_CHANGED")
            require(_snapshot(os.stat(source.name, dir_fd=source.parent.fd,
                                      follow_symlinks=False)) == source.snapshot, "SOURCE_CHANGED")
            source.parent.recheck()
        _transcript_inventory(transcript_parent, "SOURCE_CHANGED")
    except (OSError, ExportError):
        raise ExportError("SOURCE_CHANGED") from None


def _typed_equal(left, right):
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(_typed_equal(left[k], right[k]) for k in left)
    if type(left) is list:
        return len(left) == len(right) and all(_typed_equal(a, b) for a, b in zip(left, right))
    return left == right


def _keys(value, keys):
    require(type(value) is dict and set(value) == set(keys.split()))


def _hash(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def validate_contract(candidate, sources):
    summary, manifest, receipt = [strict_json(source.data) for source in sources[:3]]
    _keys(manifest, "schema candidate_sha platform filesystem suites")
    require(manifest["schema"] == "heleos.native-suite-transcripts/v1")
    require(manifest["candidate_sha"] == candidate and manifest["platform"] == "windows-x86_64"
            and manifest["filesystem"] == "NTFS")
    require(type(manifest["suites"]) is list and len(manifest["suites"]) == len(MATRIX))
    for suite, (name, selectors_) in zip(manifest["suites"], MATRIX):
        argv = ["cargo", "+1.96.1", "test", "--frozen"] + list(selectors_)
        expected = {"id": name, "run_argv": argv, "list_argv": argv + ["--", "--list"],
                    "list_exit_code": 0, "run_exit_code": 0,
                    "list_path": name + ".list.txt", "run_path": name + ".run.txt"}
        require(_typed_equal(suite, expected))
    _keys(receipt, "schema manifest_sha256 suites status candidate_sha platform filesystem suite_count total_listed total_passed")
    require(receipt["schema"] == "heleos.native-suite-receipt/v1" and receipt["status"] == "pass")
    require(all(receipt[key] == manifest[key] for key in ("candidate_sha", "platform", "filesystem")))
    require(type(receipt["suite_count"]) is int and receipt["suite_count"] == 7)
    require(type(receipt["suites"]) is list and len(receipt["suites"]) == 7)
    require(_hash(receipt["manifest_sha256"]))
    total = 0
    for index, (suite, (name, _)) in enumerate(zip(receipt["suites"], MATRIX)):
        _keys(suite, "id list_sha256 run_sha256 listed passed")
        require(suite["id"] == name and _hash(suite["list_sha256"]) and _hash(suite["run_sha256"]))
        require(type(suite["listed"]) is int and type(suite["passed"]) is int
                and 1 <= suite["listed"] <= 1000000 and suite["listed"] == suite["passed"])
        total += suite["listed"]
        require(suite["list_sha256"] == sources[3 + 2 * index].sha256
                and suite["run_sha256"] == sources[4 + 2 * index].sha256, "HASH_MISMATCH")
    require(all(type(receipt[key]) is int and receipt[key] == total
                for key in ("total_listed", "total_passed")))
    require(receipt["manifest_sha256"] == sources[1].sha256, "HASH_MISMATCH")
    require(canonical_json(receipt) == sources[2].data)
    _keys(summary, "schema status mode native_host native_evidence commit destination destination_created manifest_sha256 bundle_sha256 gate_summary gate_exit_code gate_log error_code error events")
    require(summary["schema"] == "heleos.foundation-windows-native-import/v1"
            and summary["status"] == "PASS" and summary["mode"] == "native_suites"
            and summary["commit"] == candidate)
    require(all(summary[key] is True for key in ("native_host", "native_evidence", "destination_created")))
    require(type(summary["gate_exit_code"]) is int and summary["gate_exit_code"] == 0)
    require(summary["error"] is None and summary["error_code"] is None)
    require(all(type(summary[key]) is str and bool(summary[key]) for key in ("destination", "gate_log")))
    require(_hash(summary["manifest_sha256"]) and _hash(summary["bundle_sha256"]))
    require(type(summary["events"]) is list and all(type(event) is dict for event in summary["events"]))
    require(_typed_equal(summary["gate_summary"], receipt))


def _candidate(repo, candidate):
    require(type(candidate) is str and re.fullmatch(r"[0-9a-f]{40}", candidate) is not None,
            "INVALID_CANDIDATE")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1",
                "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull})
    # Bound both pipes while draining them, not just the final captured values.
    process = None
    try:
        process = subprocess.Popen(["git", "-C", repo, "cat-file", "-t", candidate],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=env, shell=False)
        result = {"stdout": bytearray(), "stderr": bytearray()}
        deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
        with selectors.DefaultSelector() as poller:
            poller.register(process.stdout, selectors.EVENT_READ, "stdout")
            poller.register(process.stderr, selectors.EVENT_READ, "stderr")
            while poller.get_map():
                remaining = deadline - time.monotonic()
                require(remaining > 0, "INVALID_CANDIDATE")
                for key, _ in poller.select(remaining):
                    cap = 64 if key.data == "stdout" else 4096
                    chunk = os.read(key.fileobj.fileno(), cap + 1 - len(result[key.data]))
                    if not chunk:
                        poller.unregister(key.fileobj)
                    else:
                        result[key.data].extend(chunk)
                        require(len(result[key.data]) <= cap, "INVALID_CANDIDATE")
        require(process.wait(timeout=max(0.001, deadline - time.monotonic())) == 0
                and bytes(result["stdout"]) == b"commit\n", "INVALID_CANDIDATE")
    except (OSError, subprocess.SubprocessError):
        raise ExportError("INVALID_CANDIDATE") from None
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
            process.stderr.close()


def build_archive(fileobj, members, candidate):
    inventory = {"schema": SCHEMA, "candidate_sha": candidate,
                 "platform": "windows-x86_64", "filesystem": "NTFS",
                 "authority": dict(AUTHORITY),
                 "claims": {"inputs_structurally_and_hash_consistent": True},
                 "members": [{"name": name, "sha256": hashlib.sha256(data).hexdigest(),
                              "size": len(data)} for name, data in members]}
    with tarfile.open(fileobj=fileobj, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in members + [("inventory.json", canonical_json(inventory))]:
            item = tarfile.TarInfo(name)
            item.type, item.size, item.mode = tarfile.REGTYPE, len(data), 0o600
            item.mtime = item.uid = item.gid = 0
            item.uname = item.gname = item.linkname = ""
            item.pax_headers = {}
            archive.addfile(item, io.BytesIO(data))


def _output_absent(parent, name):
    try:
        os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError:
        raise ExportError("INVALID_PATH") from None
    raise ExportError("OUTPUT_EXISTS")


def _temp_matches(parent, name, fd, initial, links=1):
    opened = os.fstat(fd)
    named = os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
    require(_identity(opened) == initial and _identity(named) == initial
            and stat.S_ISREG(named.st_mode) and stat.S_IMODE(named.st_mode) == 0o600
            and named.st_nlink == links and opened.st_nlink == links, "WRITE_FAILED")


def _cleanup_temp(parent, name, fd, initial):
    if name is None or fd is None or initial is None:
        return
    try:
        named = os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    require(_identity(named) == initial and _identity(os.fstat(fd)) == initial, "WRITE_FAILED")
    os.unlink(name, dir_fd=parent.fd)


def _archive_digest(fileobj, fd, expected_size=None):
    fileobj.seek(0)
    before = os.fstat(fd)
    size_limit = before.st_size if expected_size is None else expected_size
    require(expected_size is None or before.st_size == expected_size, "WRITE_FAILED")
    archive_hash, size = hashlib.sha256(), 0
    while size <= size_limit:
        chunk = fileobj.read(min(CHUNK_BYTES, size_limit + 1 - size))
        if not chunk:
            break
        archive_hash.update(chunk)
        size += len(chunk)
        require(size <= size_limit, "WRITE_FAILED")
    after = os.fstat(fd)
    require(size == size_limit and before.st_size == after.st_size
            and _identity(before) == _identity(after), "WRITE_FAILED")
    return archive_hash.hexdigest(), size


def publish_archive(parent, output_name, sources, transcript_parent, members, candidate):
    fd, temp_name, initial, linked, fileobj = None, None, None, False, None
    error = None
    result = None
    try:
        parent.recheck("INVALID_PATH")
        for _ in range(16):
            name = ".foundation-native-evidence-" + secrets.token_hex(16) + ".tmp"
            try:
                fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent.fd)
                temp_name = name
                initial = _identity(os.fstat(fd))
                break
            except FileExistsError:
                continue
        require(fd is not None, "WRITE_FAILED")
        os.fchmod(fd, 0o600)
        initial = _identity(os.fstat(fd))
        fileobj = os.fdopen(fd, "w+b", closefd=False)
        build_archive(fileobj, members, candidate)
        fileobj.flush()
        os.fsync(fd)
        archive_snapshot = _snapshot(os.fstat(fd))
        archive_hash, size = _archive_digest(fileobj, fd)
        require(_snapshot(os.fstat(fd)) == archive_snapshot, "WRITE_FAILED")
        _temp_matches(parent, temp_name, fd, initial)
        recheck_sources(sources, transcript_parent)
        parent.recheck("INVALID_PATH")
        require(not os.fstat(parent.fd).st_mode & 0o022, "INVALID_PATH")
        _temp_matches(parent, temp_name, fd, initial)
        require(_snapshot(os.fstat(fd)) == archive_snapshot, "WRITE_FAILED")
        try:
            os.link(temp_name, output_name, src_dir_fd=parent.fd,
                    dst_dir_fd=parent.fd, follow_symlinks=False)
        except FileExistsError:
            raise ExportError("OUTPUT_EXISTS") from None
        linked = True
        _temp_matches(parent, output_name, fd, initial, links=2)
        _cleanup_temp(parent, temp_name, fd, initial)
        temp_name = None
        os.fsync(parent.fd)
        parent.recheck("PUBLISH_UNCERTAIN")
        _temp_matches(parent, output_name, fd, initial)
        final_hash, final_size = _archive_digest(fileobj, fd, size)
        require(final_hash == archive_hash and final_size == size, "WRITE_FAILED")
        _temp_matches(parent, output_name, fd, initial)
        result = {"schema": RESULT_SCHEMA, "status": "PASS", "candidate_sha": candidate,
                  "archive_sha256": archive_hash, "archive_bytes": size,
                  "member_count": 18, "output_created": True, "error_code": None, "error": None}
    except ExportError as exc:
        error = exc
    except (OSError, tarfile.TarError):
        error = ExportError("WRITE_FAILED")
    except Exception:
        error = ExportError("INTERNAL_ERROR")
    finally:
        try:
            _cleanup_temp(parent, temp_name, fd, initial)
        except (OSError, ExportError):
            error = ExportError("WRITE_FAILED")
        try:
            if fileobj is not None:
                fileobj.close()
            if fd is not None:
                os.close(fd)
        except OSError:
            error = ExportError("WRITE_FAILED")
    if error is not None:
        if linked:
            raise ExportError("PUBLISH_UNCERTAIN") from None
        raise error
    return result


def export(args):
    require(is_posix_host(), "UNSUPPORTED_PLATFORM")
    resources, sources = [], []
    try:
        paths = {key: _parts(getattr(args, key)) for key in ("repo", "summary", "manifest", "receipt", "output")}
        repo = Directory(paths["repo"])
        resources.append(repo)
        output = Directory(paths["output"][:-1])
        resources.append(output)
        output_name = paths["output"][-1]
        require(output_name.endswith(".tar") and not output_name.startswith("."), "INVALID_PATH")
        require(not os.fstat(output.fd).st_mode & 0o022, "INVALID_PATH")
        require(all(paths["output"] != paths[key] for key in ("summary", "manifest", "receipt")), "INVALID_PATH")
        _output_absent(output, output_name)
        repo.recheck("INVALID_PATH")
        _candidate(args.repo, args.candidate)
        repo.recheck("INVALID_PATH")
        for key, cap in (("summary", SUMMARY_MAX_BYTES), ("manifest", MANIFEST_MAX_BYTES),
                         ("receipt", RECEIPT_MAX_BYTES)):
            remaining = TOTAL_MAX_BYTES - sum(len(source.data) for source in sources)
            sources.append(open_source(getattr(args, key), min(cap, remaining), resources))
        transcript_parent = sources[1].parent
        require(output.identity[:2] != transcript_parent.identity[:2], "INVALID_PATH")
        _transcript_inventory(transcript_parent)
        for name in TRANSCRIPT_NAMES:
            path = "/" + "/".join(paths["manifest"][:-1] + [name])
            remaining = TOTAL_MAX_BYTES - sum(len(source.data) for source in sources)
            sources.append(open_source(path, min(TRANSCRIPT_MAX_BYTES, remaining), resources))
            require(sum(len(source.data) for source in sources) <= TOTAL_MAX_BYTES, "INPUT_LIMIT")
        require(sum(len(source.data) for source in sources) <= TOTAL_MAX_BYTES, "INPUT_LIMIT")
        require(len({source.snapshot[:2] for source in sources}) == 17, "UNSAFE_SOURCE")
        validate_contract(args.candidate, sources)
        recheck_sources(sources, transcript_parent)
        names = ["importer-summary.json", "manifest.json", "receipt.json"] + ["transcripts/" + name for name in TRANSCRIPT_NAMES]
        members = [(name, source.data) for name, source in zip(names, sources)]
        return publish_archive(output, output_name, sources, transcript_parent, members, args.candidate)
    finally:
        for source in sources:
            os.close(source.fd)
        for resource in reversed(resources):
            resource.close()


def _arguments(argv):
    if argv in (["--help"], ["-h"]):
        return None
    values = {}
    index = 0
    while index < len(argv):
        flag = argv[index]
        require(type(flag) is str, "INVALID_ARGUMENT")
        if flag == "--human":
            require("human" not in values, "INVALID_ARGUMENT")
            values["human"] = True
            index += 1
        else:
            require(flag in ("--repo", "--candidate", "--summary", "--manifest", "--receipt", "--output"), "INVALID_ARGUMENT")
            key = flag[2:]
            require(key not in values and index + 1 < len(argv), "INVALID_ARGUMENT")
            value = argv[index + 1]
            require(type(value) is str and not value.startswith("--"), "INVALID_ARGUMENT")
            values[key] = value
            index += 2
    require(all(key in values for key in ("repo", "candidate", "summary", "manifest", "receipt", "output")), "INVALID_ARGUMENT")
    values.setdefault("human", False)
    return SimpleNamespace(**values)


def _failure(code):
    return {"schema": RESULT_SCHEMA, "status": "FAIL", "candidate_sha": None,
            "archive_sha256": None, "archive_bytes": None, "member_count": None,
            "output_created": code == "PUBLISH_UNCERTAIN", "error_code": code, "error": ERRORS[code]}


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    human = "--human" in arguments
    if arguments in (["--help"], ["-h"]):
        sys.stdout.write(HELP)
        return 0
    try:
        require(is_posix_host(), "UNSUPPORTED_PLATFORM")
        args = _arguments(arguments)
        if args is None:
            sys.stdout.write(HELP)
            return 0
        result = export(args)
    except ExportError as exc:
        result = _failure(exc.code)
    except Exception:
        result = _failure("INTERNAL_ERROR")
    if human:
        text = ("PASS: Archive created. Structural and hash consistency checked."
                if result["status"] == "PASS" else
                "FAIL: " + result["error_code"] + ": " + result["error"])
        sys.stdout.write(text + " No independent native authentication, CI authority, owner approval, or release approval.\n")
    else:
        sys.stdout.write(canonical_json(result).decode("utf-8"))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
