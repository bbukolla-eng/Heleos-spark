#!/usr/bin/env python3
"""Read-only GitHub pull request snapshot adapter for cross-review runs.

The adapter asks the installed GitHub CLI for exactly one pull request,
``repos/OWNER/REPO/pulls/NUMBER``, and returns a canonical snapshot only when
the pull request is open, belongs to the requested repository and number, and
still has the caller's expected base and head commit SHAs.

Boundaries
----------
* The only external call is ``gh api --method GET`` for the single requested
  pull request path. ``gh`` is executed from an argument vector with
  ``shell=False`` and stdin closed; no shell string is ever built.
* The adapter never pushes, creates or edits a pull request, comments,
  reviews, approves or merges. It sends no request body and no fields.
* Authentication is left entirely to ``gh``. The adapter never reads token
  files, credential stores or the CLI's auth state, and never prints
  credential values. Diagnostics copied from ``gh`` stderr are redacted and
  bounded.
* Repository scope is never broadened: no search, listing, pagination or
  follow-up request is made, and a response that names a different
  repository or number (for example after a rename redirect) is rejected.

Failure categories separate "the pull request state was not observed"
(input, environment, access, transport, remote and integrity failures) from
"the pull request was observed and is not reviewable as expected"
(``pr_not_open``, ``head_changed``, ``base_changed``). Every category has its
own process exit code; see ``EXIT_CODES``. An authentication or network
failure is never reported as a changed head.

Usage::

    python3 scripts/cross_review_github.py snapshot --repo OWNER/REPO \\
        --number N --expected-base-sha SHA --expected-head-sha SHA
    python3 scripts/cross_review_github.py check --repo OWNER/REPO \\
        --number N --expected-base-sha SHA --expected-head-sha SHA

Both routes print exactly one canonical JSON object on stdout. Only the
Python standard library is used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

SNAPSHOT_SCHEMA = "heleos.github-pr-snapshot/v1"
RESULT_SCHEMA = "heleos.github-pr-read-result/v1"

DEFAULT_HOSTNAME = "github.com"
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 600.0
MAX_PULL_NUMBER = 2147483647
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_DIAGNOSTIC_CHARS = 600
MAX_DIAGNOSTIC_INPUT_BYTES = 64 * 1024

GITHUB_API_VERSION = "2022-11-28"
ACCEPT_HEADER = "Accept: application/vnd.github+json"
API_VERSION_HEADER = "X-GitHub-Api-Version: " + GITHUB_API_VERSION
GH_EXECUTABLE_NAMES = frozenset({"gh", "gh.exe"})
# Windows file names are case-insensitive and shutil.which returns the
# PATHEXT spelling of the match (normally "gh.EXE"). Elsewhere names are exact.
_CASE_INSENSITIVE_EXECUTABLE_NAMES = os.name == "nt"
GH_AUTH_REQUIRED_EXIT = 4

# category -> (process exit code, failure kind)
_CATEGORY_TABLE: Dict[str, Tuple[int, str]] = {
    "ok": (0, "ok"),
    "invalid_input": (2, "input"),
    "gh_unavailable": (3, "environment"),
    "auth_failure": (4, "access"),
    "network_failure": (5, "transport"),
    "rate_limited": (6, "transport"),
    "not_found": (7, "access"),
    "gh_failure": (8, "remote"),
    "malformed_response": (9, "integrity"),
    "scope_mismatch": (10, "integrity"),
    "pr_not_open": (11, "pr_state"),
    "head_changed": (12, "pr_state"),
    "base_changed": (13, "pr_state"),
}
EXIT_CODES: Dict[str, int] = {name: code for name, (code, _kind) in _CATEGORY_TABLE.items()}
CATEGORY_KINDS: Dict[str, str] = {name: kind for name, (_code, kind) in _CATEGORY_TABLE.items()}

# GitHub account (user or organization) names are at most 39 characters. A
# classic name uses ASCII letters, digits and hyphens and starts with a letter
# or digit. On GitHub.com an Enterprise Managed User name is the normalized
# IdP handle, one underscore and the enterprise's 3-8 character alphanumeric
# shortcode (for example developer_acme); the 39-character limit includes the
# underscore and shortcode. Source:
# https://docs.github.com/enterprise-cloud@latest/admin/managing-iam/iam-configuration-reference/username-considerations-for-external-authentication
# Slash, whitespace, control, shell and URL syntax never match.
MAX_ACCOUNT_NAME_CHARS = 39
_CLASSIC_ACCOUNT_PATTERN = r"[A-Za-z0-9][A-Za-z0-9-]*"
_MANAGED_USER_SUFFIX_PATTERN = r"_[A-Za-z0-9]{3,8}"
_OWNER_RE = re.compile(_CLASSIC_ACCOUNT_PATTERN + r"(?:" + _MANAGED_USER_SUFFIX_PATTERN + r")?")
# GitHub App bot logins: a classic name of at most 39 characters plus "[bot]".
_BOT_LOGIN_SUFFIX = "[bot]"
_BOT_LOGIN_RE = re.compile(_CLASSIC_ACCOUNT_PATTERN + r"\[bot\]")
_REPO_NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_CLI_NUMBER_RE = re.compile(r"[1-9][0-9]{0,9}")
_CLI_TIMEOUT_RE = re.compile(r"[0-9]{1,3}(?:\.[0-9]{1,3})?")
_HOST_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME_RE = re.compile(_HOST_LABEL + r"(?:\." + _HOST_LABEL + r")*")
_ENDPOINT_RE = re.compile(r"repos/([^/]+)/([^/]+)/pulls/([^/]+)")
_HTTP_STATUS_RE = re.compile(r"\(HTTP ([0-9]{3})\)")

# Environment keys removed before running gh: debug output, ambient
# repository/host routing and interactive helpers.
_GH_ENV_REMOVED = frozenset(
    {
        "GH_DEBUG",
        "DEBUG",
        "GH_REPO",
        "GH_HOST",
        "GH_PAGER",
        "PAGER",
        "GH_BROWSER",
        "BROWSER",
        "GH_FORCE_TTY",
    }
)
_GH_ENV_FORCED = {
    "GH_PROMPT_DISABLED": "1",
    "GH_NO_UPDATE_NOTIFIER": "1",
    "GH_NO_EXTENSION_UPDATE_NOTIFIER": "1",
    "GH_SPINNER_DISABLED": "1",
    "NO_COLOR": "1",
    "CLICOLOR": "0",
}

_RATE_LIMIT_MARKERS = ("rate limit", "secondary rate", "abuse detection")
_AUTH_MARKERS = (
    "gh auth login",
    "bad credentials",
    "not logged in",
    "authentication required",
    "requires authentication",
    "authentication failed",
    "http 401",
)
_NETWORK_MARKERS = (
    "error connecting to",
    "dial tcp",
    "no such host",
    "connection refused",
    "connection reset",
    "network is unreachable",
    "i/o timeout",
    "tls handshake",
    "context deadline exceeded",
    "temporary failure in name resolution",
    "could not resolve host",
    "unexpected eof",
    "timeout",
)

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# Applied in order. Token-shaped material is replaced before any diagnostic
# text leaves this module.
_REDACTIONS = (
    (re.compile(r"(?im)^([ \t]*(?:proxy-)?authorization[ \t]*:).*$"), r"\1 [REDACTED]"),
    (re.compile(r"(?i)\b(bearer|token|basic)([ \t]+)[A-Za-z0-9._~+/=-]{8,}"), r"\1\2[REDACTED]"),
    (re.compile(r"(?i)(https?://)[^/\s@]+@"), r"\1[REDACTED]@"),
    (re.compile(r"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]+"), "[REDACTED]"),
    (re.compile(r"[A-Za-z0-9_-]{32,}"), "[REDACTED]"),
)


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class GitHubReadError(Exception):
    """Base adapter failure. Messages and details never carry credentials."""

    category = "gh_failure"

    def __init__(self, message: str, *, details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: Dict[str, Any] = dict(details or {})

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.category]

    @property
    def kind(self) -> str:
        return CATEGORY_KINDS[self.category]

    @property
    def pr_state_observed(self) -> bool:
        return isinstance(self, PullRequestStateError)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category": self.category,
            "kind": self.kind,
            "exit_code": self.exit_code,
            "pr_state_observed": self.pr_state_observed,
            "message": self.message,
            "details": dict(self.details),
        }


class InvalidInputError(GitHubReadError):
    """A caller-supplied value failed strict validation. gh was not run."""

    category = "invalid_input"


class UnsafeCommandError(InvalidInputError):
    """A gh argument vector did not match the single read-only request shape."""


class GitHubAccessError(GitHubReadError):
    """gh or GitHub could not be used; the pull request state was not observed."""


class GhUnavailableError(GitHubAccessError):
    category = "gh_unavailable"


class AuthFailureError(GitHubAccessError):
    category = "auth_failure"


class NetworkFailureError(GitHubAccessError):
    category = "network_failure"


class RateLimitedError(GitHubAccessError):
    category = "rate_limited"


class NotFoundError(GitHubAccessError):
    category = "not_found"


class GhCommandError(GitHubAccessError):
    category = "gh_failure"


class ResponseIntegrityError(GitHubReadError):
    """gh succeeded but the response cannot be trusted for the request."""


class MalformedResponseError(ResponseIntegrityError):
    category = "malformed_response"


class ScopeMismatchError(ResponseIntegrityError):
    category = "scope_mismatch"


class PullRequestStateError(GitHubReadError):
    """The pull request was observed and does not match the expected state."""


class PullRequestNotOpenError(PullRequestStateError):
    category = "pr_not_open"


class HeadChangedError(PullRequestStateError):
    category = "head_changed"


class BaseChangedError(PullRequestStateError):
    category = "base_changed"


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------


def _to_text(data: Union[bytes, bytearray, str, None]) -> str:
    if data is None:
        return ""
    if isinstance(data, (bytes, bytearray)):
        return bytes(data[:MAX_DIAGNOSTIC_INPUT_BYTES]).decode("utf-8", errors="replace")
    return str(data)[:MAX_DIAGNOSTIC_INPUT_BYTES]


def _redact(text: str) -> str:
    text = _ANSI_ESCAPE_RE.sub("", text)
    text = _CONTROL_CHAR_RE.sub("", text)
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) > limit:
        return text[:limit].rstrip() + " [truncated]"
    return text


def sanitize_diagnostic(
    data: Union[bytes, bytearray, str, None], limit: int = MAX_DIAGNOSTIC_CHARS
) -> str:
    """Return bounded, control-free diagnostic text with token material redacted."""
    return _truncate(_redact(_to_text(data)), limit)


def _describe(value: Any) -> str:
    return sanitize_diagnostic(repr(value), limit=120)


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------


def is_github_account_name(value: Any) -> bool:
    """Return True for a GitHub user or organization name.

    Accepts a classic name or an Enterprise Managed User name
    (``HANDLE_SHORTCODE``), in both cases at most 39 characters.
    """
    return (
        isinstance(value, str)
        and len(value) <= MAX_ACCOUNT_NAME_CHARS
        and _OWNER_RE.fullmatch(value) is not None
    )


def is_github_login(value: Any) -> bool:
    """Return True for a pull request author login: an account name or a bot login."""
    if is_github_account_name(value):
        return True
    return (
        isinstance(value, str)
        and len(value) <= MAX_ACCOUNT_NAME_CHARS + len(_BOT_LOGIN_SUFFIX)
        and _BOT_LOGIN_RE.fullmatch(value) is not None
    )


def validate_owner(value: Any) -> str:
    if not is_github_account_name(value):
        raise InvalidInputError(
            "repository owner must be at most 39 characters: ASCII letters, digits or "
            "hyphens, not starting with a hyphen, optionally ending with an Enterprise "
            "Managed User suffix of '_' and a 3-8 character alphanumeric shortcode",
            details={"field": "owner", "value": _describe(value)},
        )
    return value


def validate_repo_name(value: Any) -> str:
    if (
        not isinstance(value, str)
        or _REPO_NAME_RE.fullmatch(value) is None
        or value in (".", "..")
        or value.lower().endswith(".git")
    ):
        raise InvalidInputError(
            "repository name must be 1-100 ASCII letters, digits, '.', '_' or '-', "
            "must not be '.' or '..' and must not end with '.git'",
            details={"field": "repo", "value": _describe(value)},
        )
    return value


def parse_repository(value: Any) -> Tuple[str, str]:
    """Parse a strict ``OWNER/REPO`` string."""
    if not isinstance(value, str) or value.count("/") != 1:
        raise InvalidInputError(
            "repository must have the form OWNER/REPO",
            details={"field": "repository", "value": _describe(value)},
        )
    owner, repo = value.split("/")
    return validate_owner(owner), validate_repo_name(repo)


def validate_pull_number(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_PULL_NUMBER:
        raise InvalidInputError(
            "pull request number must be an integer from 1 to %d" % MAX_PULL_NUMBER,
            details={"field": "number", "value": _describe(value)},
        )
    return value


def parse_cli_pull_number(text: Any) -> int:
    """Parse a decimal pull number with no sign, padding, whitespace or leading zero."""
    if not isinstance(text, str) or _CLI_NUMBER_RE.fullmatch(text) is None:
        raise InvalidInputError(
            "pull request number must be a positive decimal integer without leading zeros",
            details={"field": "number", "value": _describe(text)},
        )
    return validate_pull_number(int(text))


def validate_sha(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        raise InvalidInputError(
            "%s must be a 40-character lowercase hexadecimal commit SHA" % field,
            details={"field": field, "value": _describe(value)},
        )
    return value


def validate_hostname(value: Any) -> str:
    if isinstance(value, str):
        lowered = value.lower()
        if len(lowered) <= 253 and _HOSTNAME_RE.fullmatch(lowered) is not None:
            return lowered
    raise InvalidInputError(
        "hostname must be a plain DNS host name such as github.com",
        details={"field": "hostname", "value": _describe(value)},
    )


def validate_timeout(value: Any) -> float:
    if not isinstance(value, bool) and isinstance(value, (int, float)):
        seconds = float(value)
        if math.isfinite(seconds) and 0.0 < seconds <= MAX_TIMEOUT_SECONDS:
            return seconds
    raise InvalidInputError(
        "timeout must be a finite number of seconds greater than 0 and at most %g"
        % MAX_TIMEOUT_SECONDS,
        details={"field": "timeout", "value": _describe(value)},
    )


def parse_cli_timeout(text: Any) -> float:
    if not isinstance(text, str) or _CLI_TIMEOUT_RE.fullmatch(text) is None:
        raise InvalidInputError(
            "timeout must be a plain decimal number of seconds",
            details={"field": "timeout", "value": _describe(text)},
        )
    return validate_timeout(float(text))


@dataclass(frozen=True)
class PullRequestTarget:
    """One validated pull request identity. Construction validates every part."""

    owner: str
    repo: str
    number: int

    def __post_init__(self) -> None:
        validate_owner(self.owner)
        validate_repo_name(self.repo)
        validate_pull_number(self.number)

    @property
    def full_name(self) -> str:
        return "%s/%s" % (self.owner, self.repo)

    @property
    def api_path(self) -> str:
        return "repos/%s/%s/pulls/%d" % (self.owner, self.repo, self.number)


def make_target(repository: Any, number: Any) -> PullRequestTarget:
    owner, repo = parse_repository(repository)
    return PullRequestTarget(owner, repo, validate_pull_number(number))


# --------------------------------------------------------------------------
# gh invocation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GhResult:
    returncode: int
    stdout: bytes
    stderr: bytes


GhRunner = Callable[..., GhResult]


def _is_gh_executable_name(path: str) -> bool:
    """Return True when the final path component names the gh executable."""
    name = os.path.basename(path)
    if _CASE_INSENSITIVE_EXECUTABLE_NAMES:
        name = name.lower()
    return name in GH_EXECUTABLE_NAMES


def resolve_gh_executable(gh_path: Optional[str] = None) -> str:
    """Return the gh executable path without running it or reading its state."""
    if gh_path is None:
        found = shutil.which("gh")
        if not found:
            raise GhUnavailableError("gh executable was not found on PATH")
        return os.path.abspath(found)
    if not isinstance(gh_path, str) or not gh_path or "\x00" in gh_path:
        raise InvalidInputError(
            "gh path must be a non-empty string",
            details={"field": "gh_path", "value": _describe(gh_path)},
        )
    if not os.path.isabs(gh_path):
        raise InvalidInputError(
            "gh path must be absolute",
            details={"field": "gh_path", "value": _describe(gh_path)},
        )
    if not _is_gh_executable_name(gh_path):
        raise InvalidInputError(
            "gh path must name the gh executable",
            details={"field": "gh_path", "value": _describe(gh_path)},
        )
    if not os.path.isfile(gh_path) or not os.access(gh_path, os.X_OK):
        raise GhUnavailableError(
            "gh path is not an executable file",
            details={"gh_path": _describe(gh_path)},
        )
    return gh_path


def build_gh_api_argv(
    gh_executable: str, target: PullRequestTarget, hostname: str = DEFAULT_HOSTNAME
) -> List[str]:
    """Build the single read-only ``gh api`` argument vector."""
    if not isinstance(gh_executable, str) or not gh_executable:
        raise InvalidInputError("gh executable must be a non-empty string", details={"field": "gh_path"})
    if not isinstance(target, PullRequestTarget):
        raise InvalidInputError("target must be a PullRequestTarget", details={"field": "target"})
    host = validate_hostname(hostname)
    return [
        gh_executable,
        "api",
        "--hostname",
        host,
        "--method",
        "GET",
        "--header",
        ACCEPT_HEADER,
        "--header",
        API_VERSION_HEADER,
        target.api_path,
    ]


def assert_read_only_gh_argv(argv: Sequence[Any]) -> None:
    """Reject any argument vector other than one GET of one pull request.

    The expected shape is written out independently of ``build_gh_api_argv``
    so that a defect in the builder cannot widen what is executed.
    """
    items = list(argv)
    if len(items) != 11 or not all(isinstance(item, str) for item in items):
        raise UnsafeCommandError("gh argv does not match the read-only pull request request shape")
    if not _is_gh_executable_name(items[0]):
        raise UnsafeCommandError("gh argv does not start with the gh executable")
    match = _ENDPOINT_RE.fullmatch(items[10])
    if match is None:
        raise UnsafeCommandError("gh argv endpoint is not a single pull request path")
    try:
        target = PullRequestTarget(match.group(1), match.group(2), parse_cli_pull_number(match.group(3)))
        host = validate_hostname(items[3])
    except InvalidInputError:
        raise UnsafeCommandError("gh argv contains an invalid repository, number or host") from None
    expected = [
        items[0],
        "api",
        "--hostname",
        host,
        "--method",
        "GET",
        "--header",
        ACCEPT_HEADER,
        "--header",
        API_VERSION_HEADER,
        target.api_path,
    ]
    if items != expected:
        raise UnsafeCommandError("gh argv does not match the read-only pull request request shape")


def build_gh_environment(base: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """Return the child environment: inherited, minus debug/routing keys, non-interactive.

    Values are passed through to gh unchanged and are never inspected,
    logged or returned by this module.
    """
    source = os.environ if base is None else base
    env = {key: value for key, value in source.items() if key not in _GH_ENV_REMOVED}
    env.update(_GH_ENV_FORCED)
    return env


def run_gh_subprocess(argv: Sequence[str], *, env: Mapping[str, str], timeout: float) -> GhResult:
    """Execute gh directly from an argument vector. Never uses a shell."""
    argv_list = list(argv)
    if not argv_list or not all(isinstance(item, str) for item in argv_list):
        raise UnsafeCommandError("gh argv must be a non-empty list of strings")
    try:
        completed = subprocess.run(
            argv_list,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(env),
            timeout=timeout,
            check=False,
            shell=False,
            close_fds=True,
        )
    except subprocess.TimeoutExpired:
        raise NetworkFailureError(
            "gh api did not complete before the timeout; pull request state was not observed",
            details={"reason": "timeout", "timeout_seconds": timeout},
        ) from None
    except FileNotFoundError:
        raise GhUnavailableError("gh executable could not be started: not found") from None
    except PermissionError:
        raise GhUnavailableError("gh executable could not be started: permission denied") from None
    except OSError as exc:
        raise GhUnavailableError(
            "gh executable could not be started",
            details={"errno": exc.errno},
        ) from None
    return GhResult(
        returncode=completed.returncode,
        stdout=completed.stdout or b"",
        stderr=completed.stderr or b"",
    )


def classify_gh_failure(returncode: int, stderr: Union[bytes, str, None]) -> GitHubReadError:
    """Map a failed gh invocation to an access/transport category.

    A failed invocation never yields a pull request state category: the
    response body is not parsed, so it cannot report a changed head.
    """
    redacted = _redact(_to_text(stderr))
    lowered = redacted.lower()
    details: Dict[str, Any] = {
        "gh_exit_code": returncode,
        "gh_stderr_excerpt": _truncate(redacted, MAX_DIAGNOSTIC_CHARS),
    }
    match = _HTTP_STATUS_RE.search(redacted)
    status = int(match.group(1)) if match else None
    if status is not None:
        details["http_status"] = status
    unobserved = "; pull request state was not observed"
    if status == 429 or any(marker in lowered for marker in _RATE_LIMIT_MARKERS):
        return RateLimitedError("GitHub rate limited the read" + unobserved, details=details)
    if returncode == GH_AUTH_REQUIRED_EXIT or status in (401, 403):
        return AuthFailureError(
            "gh could not authenticate or was refused access" + unobserved, details=details
        )
    if status == 404:
        return NotFoundError(
            "pull request was not found or is not visible to the gh session" + unobserved,
            details=details,
        )
    if status is not None and 500 <= status <= 599:
        return NetworkFailureError("GitHub service failure" + unobserved, details=details)
    if status is None:
        if any(marker in lowered for marker in _AUTH_MARKERS):
            return AuthFailureError(
                "gh could not authenticate or was refused access" + unobserved, details=details
            )
        if any(marker in lowered for marker in _NETWORK_MARKERS):
            return NetworkFailureError("network failure reaching GitHub" + unobserved, details=details)
    return GhCommandError("gh api failed" + unobserved, details=details)


# --------------------------------------------------------------------------
# Response parsing
# --------------------------------------------------------------------------


def _reject_json_constant(name: str) -> Any:
    raise ValueError("non-finite JSON constant is not allowed")


def _reject_duplicate_keys(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def parse_pull_request_json(raw: Any) -> Dict[str, Any]:
    """Strictly decode gh stdout as one UTF-8 JSON object."""
    if not isinstance(raw, (bytes, bytearray)):
        raise MalformedResponseError("gh output must be bytes", details={"field": "$"})
    if not raw:
        raise MalformedResponseError("gh returned an empty response", details={"field": "$"})
    if len(raw) > MAX_RESPONSE_BYTES:
        raise MalformedResponseError(
            "gh response exceeds the size limit",
            details={"field": "$", "limit_bytes": MAX_RESPONSE_BYTES},
        )
    try:
        text = bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        raise MalformedResponseError("gh response is not valid UTF-8", details={"field": "$"}) from None
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, RecursionError) as exc:
        raise MalformedResponseError(
            "gh response is not strict JSON",
            details={"field": "$", "reason": sanitize_diagnostic(str(exc), limit=160)},
        ) from None
    if not isinstance(payload, dict):
        raise MalformedResponseError("gh response must be a JSON object", details={"field": "$"})
    return payload


def _type_error(path: str, expected: str) -> MalformedResponseError:
    return MalformedResponseError(
        "pull request response field %s must be %s" % (path, expected),
        details={"field": path, "expected": expected},
    )


def _get(container: Dict[str, Any], key: str, path: str) -> Any:
    if key not in container:
        raise MalformedResponseError(
            "pull request response is missing %s" % path, details={"field": path}
        )
    return container[key]


def _require_dict(container: Dict[str, Any], key: str, path: str) -> Dict[str, Any]:
    value = _get(container, key, path)
    if not isinstance(value, dict):
        raise _type_error(path, "an object")
    return value


def _require_bool(container: Dict[str, Any], key: str, path: str) -> bool:
    value = _get(container, key, path)
    if not isinstance(value, bool):
        raise _type_error(path, "a boolean")
    return value


def _require_int(container: Dict[str, Any], key: str, path: str) -> int:
    value = _get(container, key, path)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _type_error(path, "an integer")
    return value


def _require_str(container: Dict[str, Any], key: str, path: str) -> str:
    value = _get(container, key, path)
    if not isinstance(value, str):
        raise _type_error(path, "a string")
    return value


def _require_sha(container: Dict[str, Any], key: str, path: str) -> str:
    value = _require_str(container, key, path)
    if _SHA_RE.fullmatch(value) is None:
        raise _type_error(path, "a 40-character lowercase hexadecimal SHA")
    return value


def _require_login(container: Dict[str, Any], key: str, path: str) -> str:
    value = _require_str(container, key, path)
    if not is_github_login(value):
        raise _type_error(path, "a GitHub login")
    return value


def _require_full_name(container: Dict[str, Any], key: str, path: str) -> str:
    value = _require_str(container, key, path)
    try:
        owner, repo = parse_repository(value)
    except InvalidInputError:
        raise _type_error(path, "an OWNER/REPO name") from None
    return "%s/%s" % (owner, repo)


@dataclass(frozen=True)
class ObservedPullRequest:
    """Strictly validated fields read from one pull request response."""

    number: int
    state: str
    merged: bool
    draft: bool
    base_repository: str
    base_sha: str
    head_sha: str
    head_repository: Optional[str]
    author_login: str


def extract_observed_pull_request(payload: Any) -> ObservedPullRequest:
    if not isinstance(payload, dict):
        raise MalformedResponseError("pull request response must be a JSON object", details={"field": "$"})
    number = _require_int(payload, "number", "number")
    if not 1 <= number <= MAX_PULL_NUMBER:
        raise MalformedResponseError("pull request response number is out of range", details={"field": "number"})
    state = _require_str(payload, "state", "state")
    if state not in ("open", "closed"):
        raise _type_error("state", "'open' or 'closed'")
    merged = _require_bool(payload, "merged", "merged")
    if state == "open" and merged:
        raise MalformedResponseError(
            "pull request response is inconsistent: open and merged", details={"field": "merged"}
        )
    draft = _require_bool(payload, "draft", "draft")
    user = _require_dict(payload, "user", "user")
    author_login = _require_login(user, "login", "user.login")
    base = _require_dict(payload, "base", "base")
    base_sha = _require_sha(base, "sha", "base.sha")
    base_repo = _require_dict(base, "repo", "base.repo")
    base_repository = _require_full_name(base_repo, "full_name", "base.repo.full_name")
    head = _require_dict(payload, "head", "head")
    head_sha = _require_sha(head, "sha", "head.sha")
    head_repo = _get(head, "repo", "head.repo")
    if head_repo is None:
        head_repository: Optional[str] = None
    elif isinstance(head_repo, dict):
        head_repository = _require_full_name(head_repo, "full_name", "head.repo.full_name")
    else:
        raise _type_error("head.repo", "an object or null")
    return ObservedPullRequest(
        number=number,
        state=state,
        merged=merged,
        draft=draft,
        base_repository=base_repository,
        base_sha=base_sha,
        head_sha=head_sha,
        head_repository=head_repository,
        author_login=author_login,
    )


# --------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PullRequestSnapshot:
    """Canonical snapshot of an open pull request at the expected SHAs."""

    host: str
    repository: str
    number: int
    draft: bool
    base_sha: str
    head_sha: str
    head_repository: Optional[str]
    author_login: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": SNAPSHOT_SCHEMA,
            "host": self.host,
            "repository": self.repository,
            "number": self.number,
            "state": "open",
            "draft": self.draft,
            "base_sha": self.base_sha,
            "head_sha": self.head_sha,
            "head_repository": self.head_repository,
            "author_login": self.author_login,
        }


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def snapshot_sha256(snapshot: PullRequestSnapshot) -> str:
    return hashlib.sha256(canonical_json(snapshot.to_dict()).encode("ascii")).hexdigest()


def evaluate_pull_request(
    observed: ObservedPullRequest,
    target: PullRequestTarget,
    *,
    expected_base_sha: str,
    expected_head_sha: str,
    hostname: str = DEFAULT_HOSTNAME,
) -> PullRequestSnapshot:
    """Check identity, open state and both SHAs, then build the snapshot."""
    if not isinstance(observed, ObservedPullRequest) or not isinstance(target, PullRequestTarget):
        raise TypeError("evaluate_pull_request requires ObservedPullRequest and PullRequestTarget")
    expected_base = validate_sha(expected_base_sha, "expected_base_sha")
    expected_head = validate_sha(expected_head_sha, "expected_head_sha")
    host = validate_hostname(hostname)
    # GitHub owner and repository names are case-insensitive identities.
    if observed.base_repository.lower() != target.full_name.lower() or observed.number != target.number:
        raise ScopeMismatchError(
            "response does not describe the requested pull request; repository scope is not broadened",
            details={
                "requested_repository": target.full_name,
                "observed_repository": observed.base_repository,
                "requested_number": target.number,
                "observed_number": observed.number,
            },
        )
    if observed.state != "open" or observed.merged:
        raise PullRequestNotOpenError(
            "pull request is not open",
            details={
                "repository": observed.base_repository,
                "number": observed.number,
                "state": observed.state,
                "merged": observed.merged,
            },
        )
    head_matches = observed.head_sha == expected_head
    base_matches = observed.base_sha == expected_base
    comparison = {
        "repository": observed.base_repository,
        "number": observed.number,
        "expected_head_sha": expected_head,
        "observed_head_sha": observed.head_sha,
        "head_matches": head_matches,
        "expected_base_sha": expected_base,
        "observed_base_sha": observed.base_sha,
        "base_matches": base_matches,
    }
    if not head_matches:
        raise HeadChangedError("pull request head SHA differs from the expected head SHA", details=comparison)
    if not base_matches:
        raise BaseChangedError("pull request base SHA differs from the expected base SHA", details=comparison)
    return PullRequestSnapshot(
        host=host,
        repository=observed.base_repository,
        number=observed.number,
        draft=observed.draft,
        base_sha=observed.base_sha,
        head_sha=observed.head_sha,
        head_repository=observed.head_repository,
        author_login=observed.author_login,
    )


def snapshot_from_response(
    raw: Any,
    target: PullRequestTarget,
    *,
    expected_base_sha: str,
    expected_head_sha: str,
    hostname: str = DEFAULT_HOSTNAME,
) -> PullRequestSnapshot:
    observed = extract_observed_pull_request(parse_pull_request_json(raw))
    return evaluate_pull_request(
        observed,
        target,
        expected_base_sha=expected_base_sha,
        expected_head_sha=expected_head_sha,
        hostname=hostname,
    )


def fetch_pull_request_snapshot(
    repository: Any,
    number: Any,
    *,
    expected_base_sha: Any,
    expected_head_sha: Any,
    gh_path: Optional[str] = None,
    hostname: Any = DEFAULT_HOSTNAME,
    timeout: Any = DEFAULT_TIMEOUT_SECONDS,
    runner: Optional[GhRunner] = None,
) -> PullRequestSnapshot:
    """Fetch one pull request through ``gh api`` and verify it.

    All inputs are validated before gh is run. ``runner`` replaces the
    subprocess call for tests; it receives ``(argv, env=..., timeout=...)``
    and must return a ``GhResult``.
    """
    target = make_target(repository, number)
    expected_base = validate_sha(expected_base_sha, "expected_base_sha")
    expected_head = validate_sha(expected_head_sha, "expected_head_sha")
    host = validate_hostname(hostname)
    timeout_seconds = validate_timeout(timeout)
    gh_executable = resolve_gh_executable(gh_path)
    argv = build_gh_api_argv(gh_executable, target, host)
    assert_read_only_gh_argv(argv)
    env = build_gh_environment()
    run = runner if runner is not None else run_gh_subprocess
    result = run(argv, env=env, timeout=timeout_seconds)
    if not isinstance(result, GhResult):
        raise GhCommandError("gh runner returned an unexpected result type")
    if result.returncode != 0:
        raise classify_gh_failure(result.returncode, result.stderr)
    return snapshot_from_response(
        result.stdout,
        target,
        expected_base_sha=expected_base,
        expected_head_sha=expected_head,
        hostname=host,
    )


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def success_envelope(route: str, snapshot: PullRequestSnapshot) -> Dict[str, Any]:
    envelope: Dict[str, Any] = {
        "schema": RESULT_SCHEMA,
        "route": route,
        "ok": True,
        "category": "ok",
        "kind": "ok",
        "exit_code": EXIT_CODES["ok"],
        "pr_state_observed": True,
        "snapshot_sha256": snapshot_sha256(snapshot),
    }
    if route == "snapshot":
        envelope["snapshot"] = snapshot.to_dict()
    else:
        envelope.update(
            {
                "repository": snapshot.repository,
                "number": snapshot.number,
                "base_sha": snapshot.base_sha,
                "head_sha": snapshot.head_sha,
            }
        )
    return envelope


def failure_envelope(route: str, error: GitHubReadError) -> Dict[str, Any]:
    envelope: Dict[str, Any] = {"schema": RESULT_SCHEMA, "route": route, "ok": False}
    envelope.update(error.to_dict())
    return envelope


def _emit(envelope: Mapping[str, Any]) -> None:
    sys.stdout.write(canonical_json(envelope) + "\n")
    sys.stdout.flush()


def build_parser() -> argparse.ArgumentParser:
    exit_lines = ", ".join("%d=%s" % (code, name) for name, code in EXIT_CODES.items())
    parser = argparse.ArgumentParser(
        prog="cross_review_github.py",
        description=(
            "Read one GitHub pull request through gh api (GET only) and verify it is open "
            "at the expected base and head SHAs."
        ),
        epilog="Exit codes: " + exit_lines + ".",
        allow_abbrev=False,
    )
    routes = parser.add_subparsers(dest="route", metavar="ROUTE", required=True)
    for name, help_text in (
        ("snapshot", "print the verified canonical snapshot"),
        ("check", "verify only; print identity and snapshot digest"),
    ):
        route = routes.add_parser(name, help=help_text, allow_abbrev=False)
        route.add_argument("--repo", required=True, metavar="OWNER/REPO")
        route.add_argument("--number", required=True, metavar="N")
        route.add_argument("--expected-base-sha", required=True, metavar="SHA")
        route.add_argument("--expected-head-sha", required=True, metavar="SHA")
        route.add_argument("--gh", default=None, metavar="PATH", help="absolute path to gh (default: PATH lookup)")
        route.add_argument("--hostname", default=DEFAULT_HOSTNAME, metavar="HOST")
        route.add_argument("--timeout", default=None, metavar="SECONDS")
    return parser


def main(argv: Optional[Sequence[str]] = None, *, runner: Optional[GhRunner] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(None if argv is None else list(argv))
    route = args.route
    try:
        number = parse_cli_pull_number(args.number)
        timeout = DEFAULT_TIMEOUT_SECONDS if args.timeout is None else parse_cli_timeout(args.timeout)
        snapshot = fetch_pull_request_snapshot(
            args.repo,
            number,
            expected_base_sha=args.expected_base_sha,
            expected_head_sha=args.expected_head_sha,
            gh_path=args.gh,
            hostname=args.hostname,
            timeout=timeout,
            runner=runner,
        )
    except GitHubReadError as error:
        _emit(failure_envelope(route, error))
        return error.exit_code
    _emit(success_envelope(route, snapshot))
    return EXIT_CODES["ok"]


if __name__ == "__main__":
    sys.exit(main())
