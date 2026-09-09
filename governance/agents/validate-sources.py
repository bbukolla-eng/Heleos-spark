#!/usr/bin/env python3
"""Offline SourceEntryV1 research metadata checks, never source admission.

Python 3.11+ standard library only. Reads the explicitly named TOML manifests;
does not fetch URLs, read cached source bytes, write files, or promote rules.
Declared rights, classifications, citations and cached hashes remain unverified.
"""

import argparse
from datetime import date, datetime
import hashlib
import ipaddress
import json
import os
import re
import stat
import sys
from urllib.parse import unquote, urlsplit

try:
    import tomllib
except ImportError:
    tomllib = None


MAX_BYTES = 1024 * 1024
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
    require(isinstance(value, dict) and set(value) == {"schema_version", "status", "source"}
            and type(value["schema_version"]) is int and value["schema_version"] == 1
            and value["status"] == "research_only" and isinstance(value["source"], list)
            and 0 < len(value["source"]) <= 1000, "invalid_manifest")
    return value["source"], hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", nargs="+", help="explicit research-candidate TOML paths")
    args = parser.parse_args()
    report = {"schema_version": 1, "production_authority": False}
    try:
        require(tomllib is not None, "python_3_11_required")
        ids, hashes = set(), []
        for path in args.manifests:
            entries, digest = read_manifest(path)
            for entry in entries:
                source_id = SourceEntryV1.validate(entry)
                require(source_id not in ids, "duplicate_source_id")
                ids.add(source_id)
            hashes.append(digest)
        report.update(status="CANDIDATE_METADATA_VALID", source_count=len(ids),
                      manifest_sha256=sorted(hashes), source_bytes_verified=False,
                      citations_verified=False, rights_verified=False)
        code = 0
    except InvalidCandidate as error:
        report.update(status="REJECTED", error=str(error))
        code = 1
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    sys.exit(main())
