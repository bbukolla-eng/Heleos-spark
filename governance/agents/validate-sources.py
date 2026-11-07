#!/usr/bin/env python3
"""Offline SourceEntryV1 research metadata checks, never source admission.

Python 3.11+ standard library only. Reads the explicitly named TOML manifests;
does not fetch URLs, read cached source bytes, or promote rules. Only --self-test
writes files, inside a private temporary directory removed before returning.
Declared rights, classifications, citations and cached hashes remain unverified.
"""

import argparse
import copy
from datetime import date, datetime
import hashlib
import ipaddress
import json
import os
import re
import selectors
import stat
import subprocess
import sys
import tempfile
import time
from urllib.parse import unquote, urlsplit

try:
    import tomllib
except ImportError:
    tomllib = None


MAX_BYTES = 1024 * 1024
MAX_MANIFESTS = 1000
PRIVATE_PATH = re.compile(
    r"file://|(?:^|[\s\"'(])(?:~[/\\]|/(?:Users|home|private|workspace|tmp|var|etc)/)"
    r"|(?<![A-Za-z])[A-Za-z]:[/\\]|\\\\"
)


class InvalidCandidate(Exception):
    """A fixed diagnostic code; never carries untrusted source text or paths."""


def require(condition, code):
    if not condition:
        raise InvalidCandidate(code)


def text(value):
    require(isinstance(value, str) and 0 < len(value) <= 4096
            and value == value.strip() and not any(ord(c) < 32 for c in value),
            "invalid_value")


def canonical_url(value):
    require(isinstance(value, str), "invalid_url")
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        require(url.scheme in {"http", "https"} and value.startswith(url.scheme + "://")
                and url.netloc == host and host == host.lower() and "." in host
                and not host.endswith((".", ".local", ".localhost", ".internal", ".test"))
                and not url.query and not url.fragment
                and not any(c.isspace() or ord(c) < 32 for c in value)
                and "\\" not in value and url.username is None and url.password is None,
                "invalid_url")
        # Require DNS source names, not literal addresses or local host aliases.
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise InvalidCandidate("invalid_url")
        require(re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) is not None
                and all(label and not label.startswith("-") and not label.endswith("-")
                        for label in host.split(".")), "invalid_url")
        decoded = unquote(url.path)
        require(decoded == url.path and all(part not in {".", ".."} for part in decoded.split("/")),
                "invalid_url")
        require(host not in {"google.com", "www.google.com", "bing.com", "www.bing.com",
                             "duckduckgo.com", "www.duckduckgo.com", "search.yahoo.com"},
                "invalid_url")
    except ValueError:
        raise InvalidCandidate("invalid_url") from None


class SourceEntryV1:
    """The engineering-research plan's source entry, with exact fields/states."""

    REQUIRED = {
        "id", "canonical_url", "publisher", "title", "edition_or_version", "effective_date",
        "retrieved_at", "cache_status", "license_or_rights", "data_class", "locators",
        "claims_supported", "applicability", "supersession_state", "notebooklm_permission",
        "notebooklm_rights_basis", "contradictions", "gaps",
    }
    LISTS = {"locators", "claims_supported", "contradictions", "gaps"}

    @classmethod
    def validate(cls, entry):
        require(isinstance(entry, dict) and cls.REQUIRED <= set(entry)
                and set(entry) <= cls.REQUIRED | {"cache_sha256"}, "invalid_fields")
        canonical_url(entry["canonical_url"])
        for field in sorted(cls.REQUIRED):
            value = entry[field]
            if field in cls.LISTS:
                require(isinstance(value, list) and len(value) <= 1000, "invalid_value")
                if field in {"locators", "claims_supported"}:
                    require(bool(value), "invalid_value")
                for item in value:
                    text(item)
            else:
                text(value)
        for value in entry.values():
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, str):
                    require(PRIVATE_PATH.search(item) is None, "private_path")
        require(re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", entry["id"]) is not None,
                "invalid_value")
        require(entry["data_class"] == "PUBLIC"
                and entry["cache_status"] in {"not_cached", "cached_verified"}
                and entry["supersession_state"] in {"current", "unknown", "superseded", "retired"}
                and entry["notebooklm_permission"] in {"permitted", "reference_only", "denied"},
                "invalid_value")
        if entry["notebooklm_permission"] == "permitted":
            # This is an explicit syntactic declaration, not verified upload rights.
            prefix = "AFFIRMATIVE: "
            require(entry["notebooklm_rights_basis"].startswith(prefix), "invalid_value")
            text(entry["notebooklm_rights_basis"][len(prefix):])
        try:
            require(re.fullmatch(r"\d{4}-\d{2}-\d{2}", entry["effective_date"]) is not None,
                    "invalid_value")
            date.fromisoformat(entry["effective_date"])
            require(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
                                 entry["retrieved_at"]) is not None, "invalid_value")
            datetime.strptime(entry["retrieved_at"], "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            raise InvalidCandidate("invalid_value") from None
        if entry["cache_status"] == "cached_verified":
            digest = entry.get("cache_sha256")
            require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
                    "invalid_cache")
        else:
            require("cache_sha256" not in entry, "invalid_cache")
        return entry["id"]


def read_manifest(path):
    # O_NONBLOCK ensures a named pipe cannot block before the regular-file check.
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "input_unavailable")
            raw = stream.read(MAX_BYTES + 1)
    except OSError:
        raise InvalidCandidate("input_unavailable") from None
    require(len(raw) <= MAX_BYTES, "input_too_large")
    try:
        value = tomllib.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        raise InvalidCandidate("invalid_toml") from None
    lane_fields = {"schema_version", "status", "source"}
    aggregate_fields = {"schema_version", "status", "lane_manifest_sha256", "source_ref"}
    require(isinstance(value, dict) and set(value) in (lane_fields, aggregate_fields)
            and type(value["schema_version"]) is int and value["schema_version"] == 1
            and value["status"] == "research_only", "invalid_manifest")
    if "source" in value:
        require(isinstance(value["source"], list) and 0 < len(value["source"]) <= 1000,
                "invalid_manifest")
    else:
        hashes = value["lane_manifest_sha256"]
        require(isinstance(hashes, list) and 0 < len(hashes) <= MAX_MANIFESTS
                and all(isinstance(h, str) and re.fullmatch(r"[0-9a-f]{64}", h)
                        for h in hashes) and hashes == sorted(set(hashes)), "invalid_manifest")
        require(isinstance(value["source_ref"], list)
                and len(value["source_ref"]) <= MAX_MANIFESTS * 1000, "invalid_manifest")
    return value, hashlib.sha256(raw).hexdigest()


def validate_manifests(paths):
    """Validate exact manifest bytes and metadata, with no source admission."""
    require(0 < len(paths) <= MAX_MANIFESTS, "invalid_manifest_count")
    ids, hashes, lane_hashes, aggregates = {}, [], [], []
    for path in paths:
        value, digest = read_manifest(path)
        hashes.append(digest)
        if "source" in value:
            lane_hashes.append(digest)
            for entry in value["source"]:
                source_id = SourceEntryV1.validate(entry)
                require(source_id not in ids, "duplicate_source_id")
                ids[source_id] = digest
        else:
            aggregates.append(value)
    require(len(aggregates) <= 1, "multiple_aggregate_manifests")
    for aggregate in aggregates:
        require(bool(lane_hashes) and aggregate["lane_manifest_sha256"] == sorted(set(lane_hashes)),
                "aggregate_lane_mismatch")
        refs = {}
        for ref in aggregate["source_ref"]:
            require(isinstance(ref, dict) and set(ref) == {"id", "lane_manifest_sha256"},
                    "invalid_fields")
            source_id, digest = ref["id"], ref["lane_manifest_sha256"]
            require(isinstance(source_id, str)
                    and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", source_id) is not None
                    and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
                    "invalid_value")
            require(source_id not in refs, "duplicate_source_id")
            refs[source_id] = digest
        require(refs == ids, "aggregate_source_mismatch")
    return dict(status="CANDIDATE_METADATA_VALID", source_count=len(ids),
                manifest_sha256=sorted(hashes), lane_manifest_count=len(lane_hashes),
                aggregate_manifest_count=len(aggregates), production_authority=False,
                source_bytes_verified=False, citations_verified=False, rights_verified=False)


def contract_verdict(filter_path, record_path, scratch):
    """Run the real fixed jq executable with bounded, private fixture I/O."""
    try:
        with open(record_path, "rb") as source:
            process = subprocess.Popen(
                ["/usr/bin/jq", "-e", "-f", filter_path], stdin=source,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                cwd=scratch, env={"LC_ALL": "C"}, close_fds=True,
            )
    except OSError:
        raise InvalidCandidate("contract_validator_unavailable") from None
    output = [bytearray(), bytearray()]
    deadline = time.monotonic() + 1
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, 0)
            selector.register(process.stderr, selectors.EVENT_READ, 1)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                require(remaining > 0, "contract_validator_timeout")
                for key, _ in selector.select(remaining):
                    chunk = os.read(key.fd, 257 - sum(map(len, output)))
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output[key.data].extend(chunk)
                        require(sum(map(len, output)) <= 256, "contract_validator_output_limit")
        code = process.wait(timeout=max(0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        raise InvalidCandidate("contract_validator_timeout") from None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        process.stderr.close()
    require(not output[1] and (code, output[0]) in ((0, b"true\n"), (1, b"false\n")),
            "contract_validator_failed")
    return code == 0


def contract_self_test(scratch):
    """Fixed JSON fixtures use jq as the sole worker/packet validation authority."""
    filter_source = os.path.join(os.path.dirname(os.path.abspath(__file__)), "validate-contracts.jq")
    try:
        fd = os.open(filter_source, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        with os.fdopen(fd, "rb") as stream:
            require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "contract_validator_unavailable")
            filter_bytes = stream.read(MAX_BYTES + 1)
    except OSError:
        raise InvalidCandidate("contract_validator_unavailable") from None
    require(0 < len(filter_bytes) <= MAX_BYTES, "contract_validator_unavailable")

    def write_private(name, raw):
        path = os.path.join(scratch, name)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
        return path

    filter_path = write_private("contract-filter.jq", filter_bytes)
    budget = {"wall_seconds": 60, "actions": 10, "output_bytes": 4096, "cost_usd": 1}
    worker = {
        "schema": "heleos.research-worker-contract/v1", "task_id": "synthetic-task",
        "provider": "codex", "executable_sha256": "a" * 64, "snapshot_sha256": "b" * 64,
        "data_class": "PUBLIC", "allowed_inputs": [{"id": "public-case", "sha256": "c" * 64}],
        "allowed_tools": ["read_public_source", "write_quarantine"],
        "allowed_endpoints": ["https://example.org"],
        "forbidden_paths": ["host", "private", "production", "credentials"],
        "forbidden_actions": ["self_approve", "merge", "push", "publish", "read_credentials", "write_production"],
        "acceptance_commands": [{"tool": "synthetic-check", "argv": ["fixtures/public/case.json"], "expected_exit": 0}],
        "budgets": budget, "result_directory": "550e8400-e29b-41d4-a716-446655440000",
        "return_format": "heleos.research-packet/v1", "authority": "proposal_only",
    }
    packet = {
        "schema": "heleos.research-packet/v1", "provider": "notebook_lm", "status": "completed",
        "purpose": "Compare synthetic public claims.", "data_class": "PUBLIC",
        "source_sha256": ["a" * 64], "input_sha256": "b" * 64, "query_or_case_id": "public-case",
        "retrieved_at": "2026-09-09T12:30:00Z", "output_sha256": "c" * 64,
        "citations": [{"url": "https://example.org/spec", "locator": "Section 3", "source_sha256": "a" * 64}],
        "claims": ["Synthetic test claim."], "contradictions": [], "gaps": [],
        "evaluation": {"boundary_passes": 3, "capability_passes": 0, "reason": "Requires independent verification."},
        "reviewer": "codex", "disposition": "research_only", "budgets": budget,
    }
    results = {}

    def check(name, record, expected):
        raw = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
        require(len(raw) <= 4096, "contract_fixture_too_large")
        path = write_private(name + ".json", raw)
        results[name] = "PASS" if contract_verdict(filter_path, path, scratch) == expected else "FAIL"

    check("valid_worker", worker, True)
    check("valid_packet", packet, True)
    cases = [
        ("worker_missing_input_hash", worker, ["allowed_inputs", 0, "sha256"], None),
        ("worker_non_public", worker, ["data_class"], "INTERNAL"),
        ("worker_absent_budget", worker, ["budgets"], None),
        ("worker_authority_attempt", worker, ["authority"], "self_approve"),
        ("worker_private_path", worker, ["result_directory"], "/Users/synthetic/private"),
        ("worker_credential_text", worker, ["acceptance_commands", 0, "argv"], ["--token=synthetic"]),
        ("packet_missing_input_hash", packet, ["input_sha256"], None),
        ("packet_non_public", packet, ["data_class"], "INTERNAL"),
        ("packet_absent_budget", packet, ["budgets"], None),
        ("packet_self_approval", packet, ["reviewer"], "notebook_lm"),
        ("packet_private_path", packet, ["purpose"], "Read /Users/synthetic/private"),
        ("packet_credential_text", packet, ["purpose"], "token=synthetic"),
    ]
    for name, base, keys, value in cases:
        record = copy.deepcopy(base)
        target = record
        for key in keys[:-1]:
            target = target[key]
        if value is None:
            del target[keys[-1]]
        else:
            target[keys[-1]] = value
        check(name, record, False)
    return results


def self_test():
    """Exercise the real file reader and validator using only fixed synthetic data."""
    entry = {
        "id": "synthetic-source", "canonical_url": "https://standards.example.org/manual",
        "publisher": "Synthetic publisher", "title": "Synthetic source",
        "edition_or_version": "1", "effective_date": "2026-01-01",
        "retrieved_at": "2026-09-09T12:00:00Z", "cache_status": "not_cached",
        "license_or_rights": "Repository-authored synthetic metadata", "data_class": "PUBLIC",
        "locators": ["Section 1"], "claims_supported": ["Synthetic test only"],
        "applicability": "Synthetic advisory fixture", "supersession_state": "unknown",
        "notebooklm_permission": "denied", "notebooklm_rights_basis": "No upload permission",
        "contradictions": [], "gaps": ["No verified source bytes"],
    }
    results = {}
    with tempfile.TemporaryDirectory(prefix="heleos-source-self-test-") as scratch:
        def write_fixture(name, record, lane_digest=None):
            header = 'schema_version = 1\nstatus = "research_only"\n'
            if lane_digest is not None:
                header += 'lane_manifest_sha256 = ["' + lane_digest + '"]\n[[source_ref]]\n'
            else:
                header += '[[source]]\n'
            raw = (header + ''.join(key + ' = ' + json.dumps(value) + '\n'
                                   for key, value in record.items())).encode("utf-8")
            path = os.path.join(scratch, name + ".toml")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
            return path, hashlib.sha256(raw).hexdigest()

        def check(name, paths, expected_error=None):
            try:
                report = validate_manifests(paths)
                passed = expected_error is None and report["status"] == "CANDIDATE_METADATA_VALID"
                passed = passed and all(report[field] is False for field in (
                    "production_authority", "source_bytes_verified", "citations_verified", "rights_verified"))
            except InvalidCandidate as error:
                passed = expected_error is not None and str(error) == expected_error
            results[name] = "PASS" if passed else "FAIL"

        lane, digest = write_fixture("lane", entry)
        aggregate, _ = write_fixture("aggregate", {"id": entry["id"], "lane_manifest_sha256": digest}, digest)
        check("valid_lane", [lane])
        check("valid_aggregate", [aggregate, lane])
        affirmative, _ = write_fixture("affirmative", dict(
            entry, notebooklm_permission="permitted",
            notebooklm_rights_basis="AFFIRMATIVE: Synthetic declared upload permission; requires independent verification"))
        check("valid_affirmative_rights", [affirmative])
        cases = [
            ("missing_locator", "locators", None, "invalid_fields"),
            ("missing_rights", "license_or_rights", None, "invalid_fields"),
            ("non_public", "data_class", "INTERNAL", "invalid_value"),
            ("absent_cached_hash", "cache_status", "cached_verified", "invalid_cache"),
            ("unrecognized_notebooklm_permission", "notebooklm_permission", "approved", "invalid_value"),
            ("unaffirmed_upload_rights", "notebooklm_permission", "permitted", "invalid_value"),
            ("unknown_fields", "unknown", "untrusted synthetic text", "invalid_fields"),
            ("private_path", "title", "Read /Users/synthetic/private.pdf", "private_path"),
        ]
        for name, field, value, error in cases:
            invalid = dict(entry)
            if value is None:
                del invalid[field]
            else:
                invalid[field] = value
            path, _ = write_fixture(name, invalid)
            check(name, [path], error)
        duplicate, _ = write_fixture("duplicate", entry)
        check("duplicate_id_across_manifests", [lane, duplicate], "duplicate_source_id")
        results.update(contract_self_test(scratch))
    return dict(status="SELF_TEST_PASS" if all(v == "PASS" for v in results.values())
                else "SELF_TEST_FAIL", cases=results, production_authority=False)


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise InvalidCandidate("invalid_arguments")


def main():
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("manifests", nargs="*", help="explicit research-candidate TOML paths")
    parser.add_argument("--self-test", action="store_true", help="run private synthetic offline fixtures")
    report = {"schema_version": 1, "production_authority": False}
    try:
        args = parser.parse_args()
        require(args.self_test != bool(args.manifests), "invalid_arguments")
        require(tomllib is not None, "python_3_11_required")
        report.update(self_test() if args.self_test else validate_manifests(args.manifests))
        code = int(report["status"] == "SELF_TEST_FAIL")
    except InvalidCandidate as error:
        report.update(status="REJECTED", error=str(error))
        code = 1
    except OSError:
        report.update(status="REJECTED", error="input_unavailable")
        code = 1
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    sys.exit(main())
