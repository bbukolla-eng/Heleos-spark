"""Bind a returned Foundation summary to locally inspected outbound bytes.

This read-only POSIX observer retains source descriptors and rechecks observed
state. It provides neither a transactional filesystem snapshot nor independent
authentication of native execution. Transcript digests remain reported claims.
"""

import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from types import SimpleNamespace


sys.dont_write_bytecode = True
_spec = importlib.util.spec_from_file_location(
    "foundation_release_status", Path(__file__).with_name("foundation-release-status.py"))
release_status = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_status)

SUMMARY_MAX_BYTES = 16 * 1024 * 1024
METADATA_MAX_BYTES = 64 * 1024
BUNDLE_MAX_BYTES = 512 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_VALUES = 100000
MAX_INTEGER_TOKEN_LENGTH = 16
MAX_SAFE_INTEGER = 9007199254740991
GIT_TIMEOUT_SECONDS = 10
GIT_OUTPUT_MAX_BYTES = 1024 * 1024
IMPORTER = "scripts/import-foundation-native-candidate.ps1"
SUITES = ("core-backup-restore", "core-store", "core-backup", "platform-fs",
          "cli-unit", "cli-integration", "workspace-all")
RESULT_SCHEMA = "heleos.foundation-native-return-binding-result/v1"
IDENTITIES = ("candidate_sha", "outbound_manifest_sha256", "bundle_sha256",
              "trusted_importer_sha256", "transcript_manifest_sha256", "summary_sha256")
AUTHORITY = {name: False for name in (
    "independent_native_authentication", "native_execution_independently_proven",
    "ci_authority", "owner_approval", "release_approval")}
ERRORS = {
    "INVALID_ARGUMENT": "Invalid binding arguments.",
    "UNSUPPORTED_PLATFORM": "POSIX controller required.",
    "GIT_OVERRIDE": "Git environment overrides are not accepted.",
    "INVALID_PATH": "Unsafe binding path.",
    "UNSAFE_SOURCE": "Unsafe source file.",
    "INPUT_LIMIT": "Input limit exceeded.",
    "INVALID_JSON": "Invalid summary JSON.",
    "INVALID_SUMMARY": "Invalid Foundation importer summary.",
    "INVALID_REPOSITORY": "Repository inspection failed.",
    "INVALID_CANDIDATE": "Candidate commit unavailable.",
    "INVALID_HANDOFF": "Invalid Foundation outbound handoff.",
    "CANDIDATE_MISMATCH": "Returned candidate does not match the outbound handoff.",
    "MANIFEST_MISMATCH": "Returned outbound manifest digest does not match.",
    "BUNDLE_MISMATCH": "Returned bundle digest does not match.",
    "SOURCE_CHANGED": "Source changed during inspection.",
    "INTERNAL_ERROR": "Binding verification failed.",
}
AUTHORITY_TEXT = (" No independent native authentication, execution proof, CI authority, "
                  "owner approval, or release approval.\n")
HELP = ("Usage: python3 scripts/verify-foundation-native-return-binding.py "
        "--repo PATH --candidate FULL_SHA --handoff PATH --summary PATH [--human]\n\n"
        "Bind a final Foundation importer summary to actual outbound handoff bytes.\n"
        "All paths must be absolute and canonical; POSIX controller required.\n"
        + AUTHORITY_TEXT.lstrip())


class BindingError(Exception):
    def __init__(self, code):
        self.code = code if code in ERRORS else "INTERNAL_ERROR"
        super().__init__(self.code)


def require(condition, code="INVALID_SUMMARY"):
    if not condition:
        raise BindingError(code)


def is_posix_host():
    return os.name == "posix"


def canonical_json(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _parts(path):
    require(type(path) is str and path.startswith("/") and "\x00" not in path
            and "\\" not in path, "INVALID_PATH")
    parts = path.split("/")[1:]
    require(path == "/" or all(part not in ("", ".", "..") for part in parts),
            "INVALID_PATH")
    return [] if path == "/" else parts


def _identity(info):
    return info.st_dev, info.st_ino, info.st_mode


def _snapshot(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _not_link(info):
    return not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400


class Directory:
    """Pin every lexical ancestor, without following links or path aliases."""
    def __init__(self, parts):
        self.chain = []
        try:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
            fd = os.open("/", flags)
            self.chain.append((fd, None, _identity(os.fstat(fd))))
            lexical = Path("/")
            for part in parts:
                parent = fd
                info = os.stat(part, dir_fd=parent, follow_symlinks=False)
                require(_not_link(info) and stat.S_ISDIR(info.st_mode), "INVALID_PATH")
                fd = os.open(part, flags, dir_fd=parent)
                self.chain.append((fd, part, _identity(os.fstat(fd))))
                require(_identity(info) == self.chain[-1][2], "SOURCE_CHANGED")
                lexical = lexical / part
                require(lexical.resolve() == lexical, "INVALID_PATH")
            self.fd = fd
            self.recheck()
        except (OSError, ValueError):
            self.close()
            raise BindingError("INVALID_PATH") from None
        except BaseException:
            self.close()
            raise

    def recheck(self):
        try:
            require(_identity(os.stat("/", follow_symlinks=False)) == self.chain[0][2],
                    "SOURCE_CHANGED")
            for index, (fd, name, identity) in enumerate(self.chain):
                require(_identity(os.fstat(fd)) == identity, "SOURCE_CHANGED")
                if index:
                    info = os.stat(name, dir_fd=self.chain[index - 1][0], follow_symlinks=False)
                    require(_identity(info) == identity and _not_link(info), "SOURCE_CHANGED")
        except OSError:
            raise BindingError("SOURCE_CHANGED") from None

    def close(self):
        for fd, _, _ in reversed(self.chain):
            os.close(fd)
        self.chain = []


class Source:
    def __init__(self, path, cap, resources, retain=True):
        parts = _parts(path)
        require(bool(parts), "UNSAFE_SOURCE")
        self.parent = Directory(parts[:-1])
        resources.append(self.parent)
        self.name, self.cap, self.fd = parts[-1], cap, None
        try:
            info = os.stat(self.name, dir_fd=self.parent.fd, follow_symlinks=False)
            require(_not_link(info), "INVALID_PATH")
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    and not info.st_mode & (0o022 | stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX),
                    "UNSAFE_SOURCE")
            require(info.st_size <= cap, "INPUT_LIMIT")
            require(Path(path).resolve() == Path(path), "INVALID_PATH")
            self.snapshot = _snapshot(info)
            self.fd = os.open(self.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                              dir_fd=self.parent.fd)
            self.check()
            self.data, self.sha256, self.size = self.consume(retain)
        except OSError:
            self.close()
            raise BindingError("UNSAFE_SOURCE") from None
        except BaseException:
            self.close()
            raise

    def check(self):
        try:
            self.parent.recheck()
            require(_snapshot(os.fstat(self.fd)) == self.snapshot, "SOURCE_CHANGED")
            require(_snapshot(os.stat(self.name, dir_fd=self.parent.fd,
                                      follow_symlinks=False)) == self.snapshot, "SOURCE_CHANGED")
        except OSError:
            raise BindingError("SOURCE_CHANGED") from None

    def consume(self, retain=False):
        self.check()
        require(os.fstat(self.fd).st_size <= self.cap, "INPUT_LIMIT")
        os.lseek(self.fd, 0, os.SEEK_SET)
        hasher, size, chunks = hashlib.sha256(), 0, []
        while size <= self.cap:
            chunk = os.read(self.fd, min(CHUNK_BYTES, self.cap + 1 - size))
            if not chunk:
                break
            size += len(chunk)
            require(size <= self.cap, "INPUT_LIMIT")
            hasher.update(chunk)
            if retain:
                chunks.append(chunk)
        self.check()
        require(size == self.snapshot[4], "SOURCE_CHANGED")
        return b"".join(chunks) if retain else None, hasher.hexdigest(), size

    def recheck(self, retain=False):
        try:
            data, digest, size = self.consume(retain)
            require((digest, size) == (self.sha256, self.size), "SOURCE_CHANGED")
            return data
        except (OSError, BindingError):
            raise BindingError("SOURCE_CHANGED") from None

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def open_source(path, cap, resources, retain=True):
    return Source(path, cap, resources, retain)


def recheck_sources(sources):
    for source in sources:
        source.recheck()


def _integer(token):
    require(len(token) <= MAX_INTEGER_TOKEN_LENGTH, "INPUT_LIMIT")
    value = int(token)
    require(abs(value) <= MAX_SAFE_INTEGER, "INPUT_LIMIT")
    return value


def _constant(_token):
    raise BindingError("INVALID_JSON")


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "INVALID_JSON")
        result[key] = value
    return result


def strict_json(data):
    """Budget containers and values before the recursive JSON decoder allocates."""
    try:
        require(len(data) <= SUMMARY_MAX_BYTES, "INPUT_LIMIT")
        text = data.decode("utf-8", errors="strict")
        require(not text.startswith("\ufeff"), "INVALID_JSON")
        depth, values, quoted, escaped, token = 0, 0, False, False, False
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
                quoted, token = True, False
            elif char in "[{":
                values += 1
                depth += 1
                require(depth <= MAX_JSON_DEPTH, "INPUT_LIMIT")
                frames.append("key" if char == "{" else "array")
                token = False
            elif char in "]}":
                require(depth > 0, "INVALID_JSON")
                depth -= 1
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
        value = json.loads(text, object_pairs_hook=_unique_keys,
                           parse_int=_integer, parse_constant=_constant)
        pending, count = [value], 0
        while pending:
            item = pending.pop()
            count += 1
            require(count <= MAX_JSON_VALUES, "INPUT_LIMIT")
            if type(item) is dict:
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
        raise BindingError("INVALID_JSON") from None


def _hash(value, length=64):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{%d}" % length, value) is not None


def _keys(value, keys):
    require(type(value) is dict and set(value) == set(keys.split()))


def validate_summary(value):
    _keys(value, "schema status mode native_host native_evidence commit destination "
          "destination_created manifest_sha256 bundle_sha256 gate_summary gate_exit_code "
          "gate_log error_code error events")
    require(value["schema"] == "heleos.foundation-windows-native-import/v1"
            and value["status"] == "PASS" and value["mode"] == "native_suites")
    require(all(value[key] is True for key in ("native_host", "native_evidence", "destination_created")))
    require(type(value["gate_exit_code"]) is int and value["gate_exit_code"] == 0)
    require(value["error"] is None and value["error_code"] is None)
    require(all(type(value[key]) is str and bool(value[key]) for key in ("destination", "gate_log")))
    require(_hash(value["commit"], 40) and _hash(value["manifest_sha256"]) and _hash(value["bundle_sha256"]))
    require(type(value["events"]) is list and all(type(event) is dict for event in value["events"]))
    receipt = value["gate_summary"]
    _keys(receipt, "schema manifest_sha256 suites status candidate_sha platform filesystem "
          "suite_count total_listed total_passed")
    require(receipt["schema"] == "heleos.native-suite-receipt/v1" and receipt["status"] == "pass"
            and receipt["candidate_sha"] == value["commit"] and receipt["platform"] == "windows-x86_64"
            and receipt["filesystem"] == "NTFS" and _hash(receipt["manifest_sha256"]))
    require(type(receipt["suite_count"]) is int and receipt["suite_count"] == 7)
    require(type(receipt["suites"]) is list and len(receipt["suites"]) == 7)
    total = 0
    for suite, name in zip(receipt["suites"], SUITES):
        _keys(suite, "id list_sha256 run_sha256 listed passed")
        require(suite["id"] == name and _hash(suite["list_sha256"]) and _hash(suite["run_sha256"]))
        require(type(suite["listed"]) is int and type(suite["passed"]) is int
                and 1 <= suite["listed"] <= 1000000 and suite["listed"] == suite["passed"])
        total += suite["listed"]
    require(all(type(receipt[key]) is int and receipt[key] == total
                for key in ("total_listed", "total_passed")))
    return value


def git(repo, *args, allowed=(0,)):
    """Read-only helper adapter: bounded pipes, deadline and process-group cleanup."""
    commands = {"rev-parse", "symbolic-ref", "rev-list", "status", "worktree",
                "check-ignore", "ls-files", "ls-tree", "cat-file", "bundle"}
    require(args and args[0] in commands, "INTERNAL_ERROR")
    require(args[0] != "worktree" or args[1:] == ("list", "--porcelain", "-z"), "INTERNAL_ERROR")
    require(args[0] != "bundle" or (len(args) == 3 and args[1] in ("list-heads", "verify")),
            "INTERNAL_ERROR")
    require(args[0] != "symbolic-ref" or args[1:] == ("--quiet", "HEAD"), "INTERNAL_ERROR")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1",
                "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_SYSTEM": os.devnull, "GIT_ATTR_NOSYSTEM": "1",
                "GIT_PROTOCOL_FROM_USER": "0", "GIT_ALLOW_PROTOCOL": "",
                "LC_ALL": "C"})
    command = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false",
               "-c", "core.untrackedCache=false", "-c", "core.hooksPath=" + os.devnull,
               "-c", "protocol.allow=never", "-c", "gc.auto=0",
               "-c", "maintenance.auto=false", "-C", str(repo), *args]
    process = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env=env, shell=False, start_new_session=True)
        outputs = {"stdout": bytearray(), "stderr": bytearray()}
        deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
        with selectors.DefaultSelector() as poller:
            for name in outputs:
                stream = getattr(process, name)
                os.set_blocking(stream.fileno(), False)
                poller.register(stream, selectors.EVENT_READ, name)
            while poller.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise release_status.StateError("git_failed", "Git inspection failed.")
                for key, _ in poller.select(remaining):
                    try:
                        chunk = os.read(key.fileobj.fileno(), min(65536,
                                        GIT_OUTPUT_MAX_BYTES + 1 - len(outputs[key.data])))
                    except BlockingIOError:
                        continue
                    if not chunk:
                        poller.unregister(key.fileobj)
                    else:
                        outputs[key.data].extend(chunk)
                        require(len(outputs[key.data]) <= GIT_OUTPUT_MAX_BYTES, "INPUT_LIMIT")
        code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        if code not in allowed:
            raise release_status.StateError("git_failed", "Git inspection failed.")
        return code, bytes(outputs["stdout"])
    except (OSError, subprocess.SubprocessError):
        raise release_status.StateError("git_failed", "Git inspection failed.") from None
    finally:
        if process is not None:
            # Also kill descendants that retained pipes after their parent exited.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            process.stdout.close()
            process.stderr.close()


def git_text(repo, *args):
    return os.fsdecode(git(repo, *args)[1].removesuffix(b"\n"))


def _boundary(code, function, *args):
    try:
        return function(*args)
    except release_status.StateError as error:
        translated = {"invalid_path": "INVALID_PATH", "oversized_file": "INPUT_LIMIT",
                      "state_changed": "SOURCE_CHANGED", "invalid_file": "UNSAFE_SOURCE"}
        raise BindingError(translated.get(error.code, code)) from None
    except (OSError, ValueError, TypeError, KeyError, StopIteration, RecursionError):
        raise BindingError(code) from None


def _manifest_json(raw):
    try:
        return strict_json(raw)
    except BindingError as error:
        raise BindingError("INVALID_HANDOFF" if error.code == "INVALID_JSON" else error.code) from None


def _repository_identity(repo):
    root, observed = _boundary("INVALID_REPOSITORY", release_status.main_checkout, Path(repo))
    registration = _boundary("INVALID_REPOSITORY", git, root, "worktree", "list", "--porcelain", "-z")[1]
    fields = ("checkout_root", "git_common_dir", "branch", "head", "main_head", "worktree_paths")
    return root, observed, (tuple(observed[key] if key != "worktree_paths" else tuple(observed[key])
                                  for key in fields), registration)


def verify(repo, candidate, handoff, summary):
    require(type(candidate) is str and _hash(candidate, 40), "INVALID_CANDIDATE")
    require(is_posix_host(), "UNSUPPORTED_PLATFORM")
    require(not any(key.startswith("GIT_") for key in os.environ), "GIT_OVERRIDE")
    # Install these before canonicalization, repository discovery or helper inspection.
    release_status.state.git = git
    release_status.state.git_text = git_text
    resources, sources = [], {}
    old_read, old_hash, old_json = release_status.read_file, release_status.hash_file, release_status.parse_json
    try:
        parts = {key: _parts(value) for key, value in (("repo", repo), ("handoff", handoff), ("summary", summary))}
        resources.append(Directory(parts["repo"]))
        root, observed, identity = _repository_identity(repo)
        # Retain the visible root and common Git directory as well as the input chain.
        resources.append(Directory(_parts(str(root))))
        resources.append(Directory(_parts(observed["git_common_dir"])))
        _boundary("INVALID_CANDIDATE", release_status.commit_exists, root, candidate)
        resources.append(Directory(parts["handoff"]))

        def pin(path, cap=METADATA_MAX_BYTES, retain=True):
            key = str(path)
            require(key not in sources, "UNSAFE_SOURCE")
            source = open_source(key, cap, resources, retain)
            sources[key] = source
            require(len({s.snapshot[:2] for s in sources.values()}) == len(sources), "UNSAFE_SOURCE")
            return source

        summary_source = pin(summary, SUMMARY_MAX_BYTES)
        directory = Path(handoff)
        manifest_source = pin(directory / "candidate/candidate.json")
        pin(directory / "candidate/README.md")
        pin(directory / IMPORTER)
        pin(directory / "SHA256SUMS.txt")
        pin(root / IMPORTER)
        manifest = _manifest_json(manifest_source.data)
        # Only derive a filename after validating its closed, safe naming contract.
        _boundary("INVALID_HANDOFF", release_status.shape, manifest,
                  "schema branch commit bundle bundle_sha256 bundle_bytes native_gate required_filesystem native_gate_status")
        require(_hash(manifest["commit"], 40)
                and manifest["bundle"] == "heleos-spark-" + manifest["commit"] + ".bundle",
                "INVALID_HANDOFF")
        require(type(manifest["bundle_bytes"]) is int and manifest["bundle_bytes"] > 0, "INVALID_HANDOFF")
        require(manifest["bundle_bytes"] <= BUNDLE_MAX_BYTES, "INPUT_LIMIT")
        pin(directory / "candidate" / manifest["bundle"], BUNDLE_MAX_BYTES, False)

        def read_file(path, limit=METADATA_MAX_BYTES):
            source = sources.get(str(path))
            require(source is not None and source.data is not None, "INVALID_HANDOFF")
            require(source.size <= min(limit, METADATA_MAX_BYTES), "INPUT_LIMIT")
            return source.recheck(True)

        def hash_file(path):
            source = sources.get(str(path))
            require(source is not None, "INVALID_HANDOFF")
            source.recheck()
            return source.size, source.sha256

        release_status.read_file, release_status.hash_file = read_file, hash_file
        release_status.parse_json = _manifest_json
        transfer = _boundary("INVALID_HANDOFF", release_status.transfer, root, observed["head"], handoff)
        value = validate_summary(strict_json(summary_source.data))
        require(value["commit"] == candidate and transfer["candidate_sha"] == candidate, "CANDIDATE_MISMATCH")
        require(value["manifest_sha256"] == transfer["manifest_sha256"], "MANIFEST_MISMATCH")
        require(value["bundle_sha256"] == transfer["bundle_sha256"], "BUNDLE_MISMATCH")
        # Repeat repository registration/tracking and transfer checks, then all file
        # bytes and directory identities. Any failure now is observed drift.
        try:
            recheck_sources(list(sources.values()))
            for resource in resources:
                resource.recheck()
            require(_repository_identity(repo)[2] == identity, "SOURCE_CHANGED")
            final_transfer = _boundary("SOURCE_CHANGED", release_status.transfer, root, observed["head"], handoff)
            require(final_transfer == transfer, "SOURCE_CHANGED")
            require(_repository_identity(repo)[2] == identity, "SOURCE_CHANGED")
            _boundary("SOURCE_CHANGED", release_status.commit_exists, root, candidate)
            recheck_sources(list(sources.values()))
            for resource in resources:
                resource.recheck()
        except (BindingError, OSError):
            raise BindingError("SOURCE_CHANGED") from None
        return {"schema": RESULT_SCHEMA, "status": "PASS", "candidate_sha": candidate,
                "outbound_manifest_sha256": transfer["manifest_sha256"], "bundle_sha256": transfer["bundle_sha256"],
                "trusted_importer_sha256": transfer["importer_sha256"],
                "transcript_manifest_sha256": value["gate_summary"]["manifest_sha256"],
                "summary_sha256": summary_source.sha256, "authority": dict(AUTHORITY),
                "error_code": None, "error": None}
    finally:
        release_status.read_file, release_status.hash_file, release_status.parse_json = old_read, old_hash, old_json
        for source in sources.values():
            source.close()
        for resource in reversed(resources):
            resource.close()


def _arguments(argv):
    values, index = {}, 0
    while index < len(argv):
        flag = argv[index]
        require(type(flag) is str, "INVALID_ARGUMENT")
        if flag == "--human":
            require("human" not in values, "INVALID_ARGUMENT")
            values["human"] = True
            index += 1
        else:
            require(flag in ("--repo", "--candidate", "--handoff", "--summary"), "INVALID_ARGUMENT")
            key = flag[2:]
            require(key not in values and index + 1 < len(argv), "INVALID_ARGUMENT")
            value = argv[index + 1]
            require(type(value) is str and bool(value) and not value.startswith("-"), "INVALID_ARGUMENT")
            values[key] = value
            index += 2
    require(all(key in values for key in ("repo", "candidate", "handoff", "summary")), "INVALID_ARGUMENT")
    values.setdefault("human", False)
    return SimpleNamespace(**values)


def _failure(code):
    result = {"schema": RESULT_SCHEMA, "status": "FAIL", "authority": dict(AUTHORITY),
              "error_code": code, "error": ERRORS[code]}
    result.update({key: None for key in IDENTITIES})
    return result


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if arguments in (["--help"], ["-h"]):
        sys.stdout.write(HELP)
        return 0
    human = "--human" in arguments
    try:
        args = _arguments(arguments)
        result = verify(args.repo, args.candidate, args.handoff, args.summary)
    except BindingError as error:
        result = _failure(error.code)
    except Exception:
        result = _failure("INTERNAL_ERROR")
    if human:
        text = ("PASS: Foundation native return matches the outbound handoff."
                if result["status"] == "PASS" else
                "FAIL [" + result["error_code"] + "]: " + result["error"])
        sys.stdout.write(text + AUTHORITY_TEXT)
    else:
        sys.stdout.write(canonical_json(result).decode("utf-8"))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
