"""Tests for scripts/cross_review_github.py, the read-only GitHub PR adapter.

Run from the repository root:

    python3 -m unittest discover -s tests/cross-review -p 'test_*.py' -v

The tests never contact GitHub. gh is replaced by an injected runner, by a
patched subprocess.run, or by a local fake gh executable that records the
argument vector it received.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "cross_review_github.py"
MODULE_NAME = "cross_review_github"


def _load_module():
    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load %s" % MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


crg = _load_module()

BASE_SHA = "0123456789abcdef0123456789abcdef01234567"
HEAD_SHA = "89abcdef0123456789abcdef0123456789abcdef"
OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"
REPOSITORY = "octo-org/widget"
NUMBER = 42

EXPECTED_ARGV_TAIL = [
    "api",
    "--hostname",
    "github.com",
    "--method",
    "GET",
    "--header",
    "Accept: application/vnd.github+json",
    "--header",
    "X-GitHub-Api-Version: 2022-11-28",
    "repos/octo-org/widget/pulls/42",
]

EXPECTED_SNAPSHOT = {
    "schema": "heleos.github-pr-snapshot/v1",
    "host": "github.com",
    "repository": "octo-org/widget",
    "number": 42,
    "state": "open",
    "draft": False,
    "base_sha": BASE_SHA,
    "head_sha": HEAD_SHA,
    "head_repository": "octocat/widget",
    "author_login": "octocat",
}

# Token-shaped strings are assembled at runtime so the test source itself
# contains no credential-looking literal.
FAKE_CLASSIC_TOKEN = "gh" + "p_" + "Q7" * 18
FAKE_FINE_GRAINED_TOKEN = "github" + "_pat_" + "Z9" * 30
FAKE_BEARER_VALUE = "abcDEF123456ghiJKL"

_SHEBANG_SAFE = (
    os.name == "posix"
    and os.path.isabs(sys.executable)
    and " " not in sys.executable
    and len(sys.executable) < 120
)

_TMP = None
FAKE_GH = None


def _write_executable(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def setUpModule():
    global _TMP, FAKE_GH
    _TMP = tempfile.TemporaryDirectory()
    stub_dir = Path(_TMP.name) / "stub"
    stub_dir.mkdir()
    # Never executed by injected-runner tests; it only satisfies path checks.
    FAKE_GH = _write_executable(stub_dir / "gh", "#!%s\nimport sys\nsys.exit(97)\n" % sys.executable)


def tearDownModule():
    if _TMP is not None:
        _TMP.cleanup()


def make_payload():
    return {
        "url": "https://api.github.com/repos/octo-org/widget/pulls/42",
        "id": 1001,
        "number": 42,
        "state": "open",
        "locked": False,
        "title": "Add widget support",
        "user": {"login": "octocat", "id": 1, "type": "User"},
        "body": "Example body",
        "draft": False,
        "merged": False,
        "merged_at": None,
        "head": {
            "label": "octocat:feature",
            "ref": "feature",
            "sha": HEAD_SHA,
            "user": {"login": "octocat"},
            "repo": {
                "id": 2,
                "name": "widget",
                "full_name": "octocat/widget",
                "owner": {"login": "octocat"},
            },
        },
        "base": {
            "label": "octo-org:main",
            "ref": "main",
            "sha": BASE_SHA,
            "user": {"login": "octo-org"},
            "repo": {
                "id": 3,
                "name": "widget",
                "full_name": "octo-org/widget",
                "owner": {"login": "octo-org"},
            },
        },
    }


# GitHub.com Enterprise Managed User names: normalized handle, "_", and the
# enterprise's 3-8 character alphanumeric shortcode, 39 characters at most.
MANAGED_OWNER = "developer_acme"
MANAGED_FORK_OWNER = "mona-cat_acme"
MANAGED_REPOSITORY = MANAGED_OWNER + "/widget"
MANAGED_FORK_REPOSITORY = MANAGED_FORK_OWNER + "/widget"
MANAGED_ARGV_TAIL = EXPECTED_ARGV_TAIL[:-1] + ["repos/developer_acme/widget/pulls/42"]
EXPECTED_MANAGED_SNAPSHOT = dict(
    EXPECTED_SNAPSHOT,
    repository=MANAGED_REPOSITORY,
    head_repository=MANAGED_FORK_REPOSITORY,
    author_login=MANAGED_FORK_OWNER,
)

# Underscore-bearing values that are not GitHub account names, plus slash,
# control, shell and URL syntax around an otherwise valid managed-user name.
MALFORMED_MANAGED_NAMES = [
    "developer_",
    "developer_ac",
    "developer_acmecorp1",
    "_acme",
    "_",
    "developer__acme",
    "dev_eloper_acme",
    "developer_ac-me",
    "developer_acme-",
    "developer_ac.me",
    "-developer_acme",
    ("x" * 31) + "_acmecorp",
    ("x" * 36) + "_abc",
    "developer_acme\n",
    "developer_acme\r",
    "\tdeveloper_acme",
    "developer_acme ",
    " developer_acme",
    "developer\x00_acme",
    "developer_acme\x7f",
    "developer_acmé",
    "developer_ａcme",
    "developer_acme​",
    "developer_acme;id",
    "developer_$(id)",
    "developer_`id`",
    "developer_acme|sh",
    "developer_acme&&x",
    "developer_{acme}",
    "developer_acme/x",
    "developer_acme\\x",
    "../developer_acme",
    "https://github.com/developer_acme",
    "developer_acme@github.com",
    "developer_acme:443",
    "developer_acme?x=1",
    "developer_acme#1",
    "developer_acme%2F",
    "developer_acme[bot]",
]


def make_managed_payload():
    """A pull request in a managed user's repository from a managed user's fork."""
    payload = make_payload()
    payload["url"] = "https://api.github.com/repos/developer_acme/widget/pulls/42"
    payload["user"]["login"] = MANAGED_FORK_OWNER
    payload["head"]["label"] = MANAGED_FORK_OWNER + ":feature"
    payload["head"]["user"]["login"] = MANAGED_FORK_OWNER
    payload["head"]["repo"]["full_name"] = MANAGED_FORK_REPOSITORY
    payload["head"]["repo"]["owner"]["login"] = MANAGED_FORK_OWNER
    payload["base"]["label"] = MANAGED_OWNER + ":main"
    payload["base"]["user"]["login"] = MANAGED_OWNER
    payload["base"]["repo"]["full_name"] = MANAGED_REPOSITORY
    payload["base"]["repo"]["owner"]["login"] = MANAGED_OWNER
    return payload


_DELETE = object()


def payload_with(path, value=_DELETE):
    payload = make_payload()
    node = payload
    for key in path[:-1]:
        node = node[key]
    if value is _DELETE:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return payload


def ok_result(payload=None):
    body = make_payload() if payload is None else payload
    raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    return crg.GhResult(returncode=0, stdout=raw, stderr=b"")


def fail_result(returncode, stderr, stdout=b""):
    return crg.GhResult(returncode=returncode, stdout=stdout, stderr=stderr.encode("utf-8"))


class FakeRunner:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def __call__(self, argv, *, env, timeout):
        self.calls.append({"argv": argv, "env": env, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return self.result


def fetch(runner, **overrides):
    repository = overrides.pop("repository", REPOSITORY)
    number = overrides.pop("number", NUMBER)
    kwargs = {
        "expected_base_sha": BASE_SHA,
        "expected_head_sha": HEAD_SHA,
        "gh_path": FAKE_GH,
        "runner": runner,
    }
    kwargs.update(overrides)
    return crg.fetch_pull_request_snapshot(repository, number, **kwargs)


class AdapterTestCase(unittest.TestCase):
    def capture_error(self, runner, **overrides):
        with self.assertRaises(crg.GitHubReadError) as ctx:
            fetch(runner, **overrides)
        return ctx.exception


class TestInputValidation(AdapterTestCase):
    def test_parse_repository_accepts_valid_names(self):
        cases = {
            "octo-org/widget": ("octo-org", "widget"),
            "a/b": ("a", "b"),
            "Octo/.github": ("Octo", ".github"),
            "o/re.po_x-y": ("o", "re.po_x-y"),
            ("o" * 39) + "/repo": ("o" * 39, "repo"),
            "octo/" + ("w" * 100): ("octo", "w" * 100),
            # Enterprise Managed User owner: handle "octo" plus shortcode "org".
            "octo_org/widget": ("octo_org", "widget"),
            "developer_acme/widget": ("developer_acme", "widget"),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(crg.parse_repository(text), expected)

    def test_parse_repository_rejects_malformed_values(self):
        malformed = [
            "",
            "octo-org",
            "octo-org/",
            "/widget",
            "octo-org/widget/extra",
            "octo-org//widget",
            "-octo/widget",
            "octo org/widget",
            "octo-org/wid get",
            "octo-org/.",
            "octo-org/..",
            "../widget",
            "octo-org/widget.git",
            "octo-org/widget;id",
            "octo-org/$(id)",
            "octo-org/widget`id`",
            "octo-org/widget\n",
            " octo-org/widget",
            "octo-org/widget ",
            "ócto/widget",
            ("o" * 40) + "/widget",
            "octo/" + ("w" * 101),
            "octo-org/widget?x=1",
            "octo-org/widget#1",
            "octo_or/widget",
            "octo__org/widget",
            "_org/widget",
            "octo-org\\widget",
            None,
            42,
            b"octo-org/widget",
            ("octo-org", "widget"),
        ]
        for value in malformed:
            with self.subTest(value=value):
                with self.assertRaises(crg.InvalidInputError) as ctx:
                    crg.parse_repository(value)
                self.assertEqual(ctx.exception.category, "invalid_input")
                self.assertEqual(ctx.exception.exit_code, 2)

    def test_target_constructor_validates_parts(self):
        target = crg.PullRequestTarget("octo-org", "widget", 42)
        self.assertEqual(target.full_name, "octo-org/widget")
        self.assertEqual(target.api_path, "repos/octo-org/widget/pulls/42")
        for owner, repo, number in (
            ("octo-org", "..", 1),
            ("octo/org", "widget", 1),
            ("octo-org", "widget", 0),
            ("octo-org", "widget", "1"),
        ):
            with self.subTest(owner=owner, repo=repo, number=number):
                with self.assertRaises(crg.InvalidInputError):
                    crg.PullRequestTarget(owner, repo, number)

    def test_pull_number_validation_is_strict(self):
        self.assertEqual(crg.validate_pull_number(1), 1)
        self.assertEqual(crg.validate_pull_number(crg.MAX_PULL_NUMBER), crg.MAX_PULL_NUMBER)
        for value in (0, -1, True, False, 1.0, "42", None, crg.MAX_PULL_NUMBER + 1):
            with self.subTest(value=value):
                with self.assertRaises(crg.InvalidInputError):
                    crg.validate_pull_number(value)

    def test_cli_pull_number_parsing_is_strict(self):
        self.assertEqual(crg.parse_cli_pull_number("42"), 42)
        self.assertEqual(crg.parse_cli_pull_number("2147483647"), 2147483647)
        for text in (
            "0",
            "01",
            "042",
            "+1",
            "-1",
            " 1",
            "1 ",
            "1\n",
            "1e3",
            "0x10",
            "4.2",
            "",
            "١٢",
            "４２",
            "2147483648",
            "99999999999",
            None,
        ):
            with self.subTest(text=text):
                with self.assertRaises(crg.InvalidInputError):
                    crg.parse_cli_pull_number(text)

    def test_sha_validation_is_strict(self):
        self.assertEqual(crg.validate_sha(HEAD_SHA, "expected_head_sha"), HEAD_SHA)
        for value in (
            HEAD_SHA.upper(),
            HEAD_SHA[:39],
            HEAD_SHA + "0",
            "g" * 40,
            HEAD_SHA + "\n",
            " " + HEAD_SHA[1:],
            "a" * 64,
            HEAD_SHA[:7],
            None,
            HEAD_SHA.encode("ascii"),
        ):
            with self.subTest(value=value):
                with self.assertRaises(crg.InvalidInputError):
                    crg.validate_sha(value, "expected_head_sha")

    def test_hostname_validation(self):
        self.assertEqual(crg.validate_hostname("github.com"), "github.com")
        self.assertEqual(crg.validate_hostname("GitHub.COM"), "github.com")
        for value in ("", "github.com/evil", "-bad.com", "a..b", "host:443", "user@github.com", "gh com", None):
            with self.subTest(value=value):
                with self.assertRaises(crg.InvalidInputError):
                    crg.validate_hostname(value)

    def test_timeout_validation(self):
        self.assertEqual(crg.validate_timeout(30), 30.0)
        self.assertEqual(crg.validate_timeout(0.5), 0.5)
        self.assertEqual(crg.parse_cli_timeout("45.5"), 45.5)
        for value in (0, -1, True, float("nan"), float("inf"), 601, "30", None):
            with self.subTest(value=value):
                with self.assertRaises(crg.InvalidInputError):
                    crg.validate_timeout(value)
        for text in ("0", "-5", "1e3", "601", "", " 5"):
            with self.subTest(text=text):
                with self.assertRaises(crg.InvalidInputError):
                    crg.parse_cli_timeout(text)

    def test_invalid_inputs_never_invoke_gh(self):
        cases = [
            {"repository": "octo-org/widget/extra"},
            {"repository": "octo-org/widget;id"},
            {"number": 0},
            {"number": "42"},
            {"expected_head_sha": HEAD_SHA.upper()},
            {"expected_base_sha": BASE_SHA[:12]},
            {"expected_head_sha": None},
            {"hostname": "github.com/evil"},
            {"timeout": 0},
            {"gh_path": "gh"},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                runner = FakeRunner(ok_result())
                error = self.capture_error(runner, **overrides)
                self.assertIsInstance(error, crg.InvalidInputError)
                self.assertEqual(error.exit_code, 2)
                self.assertEqual(runner.calls, [])


class TestGhInvocation(AdapterTestCase):
    def test_build_argv_is_exact_read_only_get(self):
        target = crg.make_target(REPOSITORY, NUMBER)
        argv = crg.build_gh_api_argv(FAKE_GH, target, "GitHub.com")
        self.assertIsInstance(argv, list)
        self.assertEqual(argv, [FAKE_GH] + EXPECTED_ARGV_TAIL)
        crg.assert_read_only_gh_argv(argv)

    def test_fetch_passes_argument_vector_to_runner(self):
        runner = FakeRunner(ok_result())
        fetch(runner)
        self.assertEqual(len(runner.calls), 1)
        call = runner.calls[0]
        self.assertIsInstance(call["argv"], list)
        self.assertEqual(call["argv"], [FAKE_GH] + EXPECTED_ARGV_TAIL)
        self.assertEqual(call["timeout"], crg.DEFAULT_TIMEOUT_SECONDS)

    def test_read_only_guard_rejects_any_other_shape(self):
        valid = crg.build_gh_api_argv(FAKE_GH, crg.make_target(REPOSITORY, NUMBER))
        post = list(valid)
        post[5] = "POST"
        mutations = {
            "post_method": post,
            "field_added": valid[:-1] + ["-f", "body=x", valid[-1]],
            "input_added": valid[:-1] + ["--input", "-", valid[-1]],
            "paginate_added": valid + ["--paginate"],
            "merge_endpoint": valid[:-1] + ["repos/octo-org/widget/pulls/42/merge"],
            "comment_endpoint": valid[:-1] + ["repos/octo-org/widget/issues/42/comments"],
            "graphql_endpoint": valid[:-1] + ["graphql"],
            "traversal_endpoint": valid[:-1] + ["repos/octo-org/../pulls/42"],
            "placeholder_endpoint": valid[:-1] + ["repos/{owner}/{repo}/pulls/42"],
            "other_executable": ["/bin/sh"] + valid[1:],
            "shell_c": ["/bin/sh", "-c", " ".join(valid)],
            "missing_header": valid[:6] + valid[8:],
            "non_string": valid[:-1] + [42],
            "empty": [],
        }
        for name, argv in mutations.items():
            with self.subTest(mutation=name):
                with self.assertRaises(crg.UnsafeCommandError):
                    crg.assert_read_only_gh_argv(argv)

    def test_guard_runs_before_the_runner(self):
        bad = [FAKE_GH] + EXPECTED_ARGV_TAIL
        bad[5] = "PATCH"
        runner = FakeRunner(ok_result())
        with mock.patch.object(crg, "build_gh_api_argv", return_value=bad):
            error = self.capture_error(runner)
        self.assertIsInstance(error, crg.UnsafeCommandError)
        self.assertEqual(runner.calls, [])

    def test_default_runner_calls_subprocess_with_argv_and_no_shell(self):
        argv = [FAKE_GH] + EXPECTED_ARGV_TAIL
        completed = subprocess.CompletedProcess(args=argv, returncode=0, stdout=b"{}", stderr=b"")
        with mock.patch.object(crg.subprocess, "run", return_value=completed) as run_mock:
            result = crg.run_gh_subprocess(argv, env={"PATH": "/usr/bin"}, timeout=5.0)
        self.assertEqual(result, crg.GhResult(returncode=0, stdout=b"{}", stderr=b""))
        self.assertEqual(run_mock.call_count, 1)
        args, kwargs = run_mock.call_args
        self.assertEqual(len(args), 1)
        self.assertIs(type(args[0]), list)
        self.assertEqual(args[0], argv)
        self.assertIs(kwargs["shell"], False)
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["timeout"], 5.0)
        self.assertEqual(kwargs["env"], {"PATH": "/usr/bin"})
        self.assertNotIn("executable", kwargs)

    def test_fetch_uses_default_subprocess_runner_without_shell(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps(make_payload()).encode("utf-8"), stderr=b""
        )
        with mock.patch.object(crg.subprocess, "run", return_value=completed) as run_mock:
            snapshot = fetch(None)
        self.assertEqual(snapshot.to_dict(), EXPECTED_SNAPSHOT)
        args, kwargs = run_mock.call_args
        self.assertEqual(args[0], [FAKE_GH] + EXPECTED_ARGV_TAIL)
        self.assertIs(kwargs["shell"], False)

    def test_default_runner_timeout_is_network_failure(self):
        side_effect = subprocess.TimeoutExpired(cmd=[FAKE_GH], timeout=5.0)
        with mock.patch.object(crg.subprocess, "run", side_effect=side_effect):
            error = self.capture_error(None)
        self.assertIsInstance(error, crg.NetworkFailureError)
        self.assertEqual(error.details.get("reason"), "timeout")
        self.assertFalse(error.pr_state_observed)

    def test_default_runner_start_failure_is_gh_unavailable(self):
        for exc in (FileNotFoundError(2, "missing"), PermissionError(13, "denied"), OSError(8, "exec format")):
            with self.subTest(exc=type(exc).__name__):
                with mock.patch.object(crg.subprocess, "run", side_effect=exc):
                    error = self.capture_error(None)
                self.assertIsInstance(error, crg.GhUnavailableError)
                self.assertEqual(error.exit_code, crg.EXIT_CODES["gh_unavailable"])

    def test_environment_is_non_interactive_and_strips_debug_and_routing(self):
        injected = {
            "GH_DEBUG": "api",
            "DEBUG": "1",
            "GH_REPO": "evil/elsewhere",
            "GH_HOST": "evil.example",
            "GH_PAGER": "less",
            "KEEP_ME": "yes",
        }
        with mock.patch.dict(os.environ, injected):
            env = crg.build_gh_environment()
            runner = FakeRunner(ok_result())
            fetch(runner)
        for key in ("GH_DEBUG", "DEBUG", "GH_REPO", "GH_HOST", "GH_PAGER"):
            self.assertNotIn(key, env)
            self.assertNotIn(key, runner.calls[0]["env"])
        self.assertEqual(env["KEEP_ME"], "yes")
        self.assertEqual(env["GH_PROMPT_DISABLED"], "1")
        self.assertEqual(env["GH_NO_UPDATE_NOTIFIER"], "1")
        self.assertEqual(runner.calls[0]["env"]["GH_PROMPT_DISABLED"], "1")

    def test_resolve_gh_executable(self):
        with mock.patch.object(crg.shutil, "which", return_value=None):
            with self.assertRaises(crg.GhUnavailableError):
                crg.resolve_gh_executable(None)
        with mock.patch.object(crg.shutil, "which", return_value=FAKE_GH):
            self.assertEqual(crg.resolve_gh_executable(None), os.path.abspath(FAKE_GH))
        self.assertEqual(crg.resolve_gh_executable(FAKE_GH), FAKE_GH)
        with self.assertRaises(crg.InvalidInputError):
            crg.resolve_gh_executable("gh")
        with self.assertRaises(crg.InvalidInputError):
            crg.resolve_gh_executable(sys.executable)
        with self.assertRaises(crg.InvalidInputError):
            crg.resolve_gh_executable("")
        missing = os.path.join(_TMP.name, "missing", "gh")
        with self.assertRaises(crg.GhUnavailableError):
            crg.resolve_gh_executable(missing)
        noexec_dir = Path(_TMP.name) / "noexec"
        noexec_dir.mkdir(exist_ok=True)
        noexec = noexec_dir / "gh"
        noexec.write_text("not executable\n", encoding="utf-8")
        noexec.chmod(0o644)
        if not os.access(str(noexec), os.X_OK):
            with self.assertRaises(crg.GhUnavailableError):
                crg.resolve_gh_executable(str(noexec))

    def test_executable_name_case_rule_follows_platform(self):
        # shutil.which on Windows returns the PATHEXT spelling, normally gh.EXE.
        stub_dir = os.path.dirname(FAKE_GH)
        pathext_gh = os.path.join(stub_dir, "gh.EXE")
        argv = crg.build_gh_api_argv(pathext_gh, crg.make_target(REPOSITORY, NUMBER))
        with mock.patch.object(crg, "_CASE_INSENSITIVE_EXECUTABLE_NAMES", True):
            crg.assert_read_only_gh_argv(argv)
            for other in ("GH.CMD", "gh.bat", "gh-evil.EXE", "sh.EXE"):
                with self.subTest(case_insensitive=True, name=other):
                    with self.assertRaises(crg.UnsafeCommandError):
                        crg.assert_read_only_gh_argv([os.path.join(stub_dir, other)] + argv[1:])
        with mock.patch.object(crg, "_CASE_INSENSITIVE_EXECUTABLE_NAMES", False):
            with self.assertRaises(crg.UnsafeCommandError):
                crg.assert_read_only_gh_argv(argv)

    def test_windows_path_lookup_spelling_reaches_runner(self):
        pathext_gh = os.path.join(os.path.dirname(FAKE_GH), "gh.EXE")
        runner = FakeRunner(ok_result())
        with mock.patch.object(crg, "_CASE_INSENSITIVE_EXECUTABLE_NAMES", True):
            with mock.patch.object(crg.shutil, "which", return_value=pathext_gh):
                snapshot = fetch(runner, gh_path=None)
        self.assertEqual(snapshot.to_dict(), EXPECTED_SNAPSHOT)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(runner.calls[0]["argv"], [os.path.abspath(pathext_gh)] + EXPECTED_ARGV_TAIL)


_RECORDING_GH = """#!@PYTHON@
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
record = {
    "argv": sys.argv[1:],
    "prompt_disabled": os.environ.get("GH_PROMPT_DISABLED"),
    "gh_debug_present": "GH_DEBUG" in os.environ,
    "gh_repo_present": "GH_REPO" in os.environ,
    "stdin_empty": sys.stdin.read() == "",
}
with open(os.path.join(HERE, "record.json"), "w", encoding="utf-8") as handle:
    json.dump(record, handle)
with open(os.path.join(HERE, "control.json"), "r", encoding="utf-8") as handle:
    control = json.load(handle)
sys.stdout.write(control["stdout"])
sys.stderr.write(control["stderr"])
sys.exit(control["exit"])
"""


@unittest.skipUnless(_SHEBANG_SAFE, "fake gh executable needs a POSIX shebang-safe interpreter path")
class TestRealSubprocessWithFakeGh(AdapterTestCase):
    """Runs the real default runner against a local fake gh. No network."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.gh = _write_executable(self.dir / "gh", _RECORDING_GH.replace("@PYTHON@", sys.executable))

    def tearDown(self):
        self._tmp.cleanup()

    def _control(self, stdout="", stderr="", exit_code=0):
        control = {"stdout": stdout, "stderr": stderr, "exit": exit_code}
        (self.dir / "control.json").write_text(json.dumps(control), encoding="utf-8")

    def _record(self):
        return json.loads((self.dir / "record.json").read_text(encoding="utf-8"))

    def test_gh_receives_exact_argv_without_shell_splitting(self):
        self._control(stdout=json.dumps(make_payload()))
        with mock.patch.dict(os.environ, {"GH_DEBUG": "api", "GH_REPO": "evil/elsewhere"}):
            snapshot = crg.fetch_pull_request_snapshot(
                REPOSITORY,
                NUMBER,
                expected_base_sha=BASE_SHA,
                expected_head_sha=HEAD_SHA,
                gh_path=self.gh,
                timeout=60,
            )
        self.assertEqual(snapshot.to_dict(), EXPECTED_SNAPSHOT)
        record = self._record()
        self.assertEqual(record["argv"], EXPECTED_ARGV_TAIL)
        # A header containing a space arrives as one argument: no shell split it.
        self.assertIn("Accept: application/vnd.github+json", record["argv"])
        self.assertEqual(record["prompt_disabled"], "1")
        self.assertFalse(record["gh_debug_present"])
        self.assertFalse(record["gh_repo_present"])
        self.assertTrue(record["stdin_empty"])

    def test_gh_receives_managed_user_endpoint(self):
        self._control(stdout=json.dumps(make_managed_payload()))
        snapshot = crg.fetch_pull_request_snapshot(
            MANAGED_REPOSITORY,
            NUMBER,
            expected_base_sha=BASE_SHA,
            expected_head_sha=HEAD_SHA,
            gh_path=self.gh,
            timeout=60,
        )
        self.assertEqual(snapshot.to_dict(), EXPECTED_MANAGED_SNAPSHOT)
        self.assertEqual(self._record()["argv"], MANAGED_ARGV_TAIL)

    def test_real_gh_auth_exit_is_auth_failure(self):
        self._control(stderr="To get started with GitHub CLI, please run:  gh auth login\n", exit_code=4)
        error = self.capture_error(None, gh_path=self.gh, timeout=60)
        self.assertIsInstance(error, crg.AuthFailureError)
        self.assertFalse(error.pr_state_observed)

    def test_real_gh_closed_pull_request(self):
        self._control(stdout=json.dumps(payload_with(("state",), "closed")))
        error = self.capture_error(None, gh_path=self.gh, timeout=60)
        self.assertIsInstance(error, crg.PullRequestNotOpenError)


class TestStrictResponseParsing(AdapterTestCase):
    def test_valid_payload_produces_canonical_snapshot(self):
        snapshot = fetch(FakeRunner(ok_result()))
        self.assertEqual(snapshot.to_dict(), EXPECTED_SNAPSHOT)
        canonical = crg.canonical_json(snapshot.to_dict())
        self.assertEqual(canonical, json.dumps(EXPECTED_SNAPSHOT, sort_keys=True, separators=(",", ":")))
        self.assertEqual(crg.snapshot_sha256(snapshot), hashlib.sha256(canonical.encode("ascii")).hexdigest())

    def test_draft_state_is_preserved(self):
        snapshot = fetch(FakeRunner(ok_result(payload_with(("draft",), True))))
        self.assertIs(snapshot.draft, True)
        self.assertIs(snapshot.to_dict()["draft"], True)

    def test_deleted_head_repository_is_null(self):
        snapshot = fetch(FakeRunner(ok_result(payload_with(("head", "repo"), None))))
        self.assertIsNone(snapshot.head_repository)
        self.assertIsNone(snapshot.to_dict()["head_repository"])

    def test_same_repository_head_and_bot_author(self):
        payload = make_payload()
        payload["head"]["repo"]["full_name"] = "octo-org/widget"
        payload["user"]["login"] = "dependabot[bot]"
        snapshot = fetch(FakeRunner(ok_result(payload)))
        self.assertEqual(snapshot.head_repository, "octo-org/widget")
        self.assertEqual(snapshot.author_login, "dependabot[bot]")

    def test_rejects_non_strict_json(self):
        good = json.dumps(make_payload()).encode("utf-8")
        cases = {
            "empty": b"",
            "whitespace": b"   ",
            "not_json": b"not json",
            "trailing_data": good + b"x",
            "bom": b"\xef\xbb\xbf" + good,
            "not_utf8": b"\xff\xfe{}",
            "top_level_list": b"[]",
            "top_level_string": b'"open"',
            "nan": b'{"number": NaN}',
            "infinity": b'{"number": Infinity}',
            "negative_infinity": b'{"number": -Infinity}',
            "duplicate_key": b'{"number": 42, "number": 42}',
            "duplicate_nested_key": good[:-1] + b', "head": {"sha": "x"}}',
        }
        for name, raw in cases.items():
            with self.subTest(case=name):
                error = self.capture_error(FakeRunner(ok_result(raw)))
                self.assertIsInstance(error, crg.MalformedResponseError)
                self.assertEqual(error.exit_code, crg.EXIT_CODES["malformed_response"])
                self.assertFalse(error.pr_state_observed)

    def test_rejects_oversized_response(self):
        with mock.patch.object(crg, "MAX_RESPONSE_BYTES", 16):
            error = self.capture_error(FakeRunner(ok_result()))
        self.assertIsInstance(error, crg.MalformedResponseError)

    def test_rejects_missing_required_fields(self):
        paths = [
            ("number",),
            ("state",),
            ("merged",),
            ("draft",),
            ("user",),
            ("user", "login"),
            ("base",),
            ("base", "sha"),
            ("base", "repo"),
            ("base", "repo", "full_name"),
            ("head",),
            ("head", "sha"),
            ("head", "repo"),
            ("head", "repo", "full_name"),
        ]
        for path in paths:
            with self.subTest(path=".".join(path)):
                error = self.capture_error(FakeRunner(ok_result(payload_with(path))))
                self.assertIsInstance(error, crg.MalformedResponseError)
                self.assertEqual(error.details.get("field"), ".".join(path))

    def test_rejects_wrong_types_and_values(self):
        cases = [
            (("number",), "42"),
            (("number",), True),
            (("number",), 42.0),
            (("number",), 0),
            (("number",), None),
            (("state",), "OPEN"),
            (("state",), "draft"),
            (("state",), None),
            (("merged",), None),
            (("merged",), "false"),
            (("draft",), "false"),
            (("draft",), 0),
            (("user",), None),
            (("user", "login"), 7),
            (("user", "login"), "bad login"),
            (("user", "login"), ""),
            (("base",), [BASE_SHA]),
            (("base", "sha"), BASE_SHA.upper()),
            (("base", "sha"), BASE_SHA[:7]),
            (("base", "sha"), None),
            (("base", "repo"), None),
            (("base", "repo"), "octo-org/widget"),
            (("base", "repo", "full_name"), "octo-org"),
            (("base", "repo", "full_name"), "octo-org/widget/extra"),
            (("head", "sha"), HEAD_SHA + "\n"),
            (("head", "sha"), 12345),
            (("head", "repo"), "octocat/widget"),
            (("head", "repo", "full_name"), "a/b/c"),
            (("head", "repo", "full_name"), None),
        ]
        for path, value in cases:
            with self.subTest(path=".".join(path), value=value):
                error = self.capture_error(FakeRunner(ok_result(payload_with(path, value))))
                self.assertIsInstance(error, crg.MalformedResponseError)

    def test_open_and_merged_is_inconsistent(self):
        error = self.capture_error(FakeRunner(ok_result(payload_with(("merged",), True))))
        self.assertIsInstance(error, crg.MalformedResponseError)

    def test_failed_gh_output_is_never_parsed(self):
        changed = json.dumps(payload_with(("head", "sha"), OTHER_SHA)).encode("utf-8")
        runner = FakeRunner(fail_result(1, "error connecting to api.github.com\n", stdout=changed))
        error = self.capture_error(runner)
        self.assertIsInstance(error, crg.NetworkFailureError)
        self.assertNotIsInstance(error, crg.HeadChangedError)


class TestStateAndShaChecks(AdapterTestCase):
    def test_closed_pull_request_is_rejected(self):
        error = self.capture_error(FakeRunner(ok_result(payload_with(("state",), "closed"))))
        self.assertIsInstance(error, crg.PullRequestNotOpenError)
        self.assertEqual(error.category, "pr_not_open")
        self.assertEqual(error.exit_code, crg.EXIT_CODES["pr_not_open"])
        self.assertEqual(error.details["state"], "closed")
        self.assertIs(error.details["merged"], False)
        self.assertTrue(error.pr_state_observed)

    def test_merged_pull_request_is_rejected(self):
        payload = make_payload()
        payload["state"] = "closed"
        payload["merged"] = True
        error = self.capture_error(FakeRunner(ok_result(payload)))
        self.assertIsInstance(error, crg.PullRequestNotOpenError)
        self.assertIs(error.details["merged"], True)

    def test_closed_is_reported_even_when_shas_differ(self):
        payload = payload_with(("state",), "closed")
        payload["head"]["sha"] = OTHER_SHA
        error = self.capture_error(FakeRunner(ok_result(payload)))
        self.assertIsInstance(error, crg.PullRequestNotOpenError)

    def test_head_mismatch_is_head_changed(self):
        error = self.capture_error(FakeRunner(ok_result(payload_with(("head", "sha"), OTHER_SHA))))
        self.assertIsInstance(error, crg.HeadChangedError)
        self.assertEqual(error.category, "head_changed")
        self.assertEqual(error.kind, "pr_state")
        self.assertEqual(error.exit_code, crg.EXIT_CODES["head_changed"])
        self.assertEqual(error.details["expected_head_sha"], HEAD_SHA)
        self.assertEqual(error.details["observed_head_sha"], OTHER_SHA)
        self.assertIs(error.details["head_matches"], False)
        self.assertIs(error.details["base_matches"], True)

    def test_base_mismatch_is_base_changed(self):
        error = self.capture_error(FakeRunner(ok_result(payload_with(("base", "sha"), OTHER_SHA))))
        self.assertIsInstance(error, crg.BaseChangedError)
        self.assertEqual(error.exit_code, crg.EXIT_CODES["base_changed"])
        self.assertEqual(error.details["expected_base_sha"], BASE_SHA)
        self.assertEqual(error.details["observed_base_sha"], OTHER_SHA)
        self.assertIs(error.details["head_matches"], True)

    def test_both_mismatched_reports_head_first_with_base_detail(self):
        payload = payload_with(("head", "sha"), OTHER_SHA)
        payload["base"]["sha"] = OTHER_SHA
        error = self.capture_error(FakeRunner(ok_result(payload)))
        self.assertIsInstance(error, crg.HeadChangedError)
        self.assertIs(error.details["base_matches"], False)

    def test_expected_sha_swap_is_rejected(self):
        error = self.capture_error(
            FakeRunner(ok_result()), expected_base_sha=HEAD_SHA, expected_head_sha=BASE_SHA
        )
        self.assertIsInstance(error, crg.HeadChangedError)

    def test_renamed_repository_redirect_is_scope_mismatch(self):
        payload = payload_with(("base", "repo", "full_name"), "octo-org/widget-renamed")
        error = self.capture_error(FakeRunner(ok_result(payload)))
        self.assertIsInstance(error, crg.ScopeMismatchError)
        self.assertEqual(error.details["requested_repository"], "octo-org/widget")
        self.assertEqual(error.details["observed_repository"], "octo-org/widget-renamed")
        self.assertFalse(error.pr_state_observed)

    def test_other_owner_is_scope_mismatch(self):
        payload = payload_with(("base", "repo", "full_name"), "someone-else/widget")
        error = self.capture_error(FakeRunner(ok_result(payload)))
        self.assertIsInstance(error, crg.ScopeMismatchError)

    def test_number_mismatch_is_scope_mismatch(self):
        error = self.capture_error(FakeRunner(ok_result(payload_with(("number",), 43))))
        self.assertIsInstance(error, crg.ScopeMismatchError)
        self.assertEqual(error.details["observed_number"], 43)

    def test_repository_identity_is_case_insensitive_and_canonicalised(self):
        snapshot = fetch(FakeRunner(ok_result()), repository="Octo-Org/Widget")
        self.assertEqual(snapshot.repository, "octo-org/widget")


class TestEnterpriseManagedUserNames(AdapterTestCase):
    """GitHub.com Enterprise Managed User names (HANDLE_SHORTCODE) are valid accounts."""

    VALID_NAMES = [
        "developer_acme",
        "mona-cat_acme",
        "acme_admin",
        "a_b1c",
        "Developer_ACME",
        "4ever_acme1",
        ("x" * 30) + "_acmecorp",
        ("x" * 35) + "_abc",
    ]

    def test_valid_name_lengths_reach_the_limit(self):
        self.assertEqual(crg.MAX_ACCOUNT_NAME_CHARS, 39)
        self.assertEqual(len(("x" * 30) + "_acmecorp"), 39)
        self.assertEqual(len(("x" * 35) + "_abc"), 39)

    def test_accepts_managed_user_owner_and_login(self):
        for name in self.VALID_NAMES:
            with self.subTest(name=name):
                self.assertTrue(crg.is_github_account_name(name))
                self.assertTrue(crg.is_github_login(name))
                self.assertEqual(crg.validate_owner(name), name)
                self.assertEqual(crg.parse_repository(name + "/widget"), (name, "widget"))
                target = crg.PullRequestTarget(name, "widget", 42)
                self.assertEqual(target.full_name, name + "/widget")
                self.assertEqual(target.api_path, "repos/%s/widget/pulls/42" % name)

    def test_rejects_malformed_managed_user_names(self):
        for name in MALFORMED_MANAGED_NAMES:
            with self.subTest(name=name):
                self.assertFalse(crg.is_github_account_name(name))
                self.assertFalse(crg.is_github_login(name))
                with self.assertRaises(crg.InvalidInputError) as ctx:
                    crg.validate_owner(name)
                self.assertEqual(ctx.exception.details["field"], "owner")
                with self.assertRaises(crg.InvalidInputError):
                    crg.parse_repository(name + "/widget")
                with self.assertRaises(crg.InvalidInputError):
                    crg.PullRequestTarget(name, "widget", 42)

    def test_non_string_names_are_rejected(self):
        for value in (None, 42, b"developer_acme", ["developer_acme"]):
            with self.subTest(value=value):
                self.assertFalse(crg.is_github_account_name(value))
                self.assertFalse(crg.is_github_login(value))

    def test_bot_login_rule_is_unchanged(self):
        for login in ("dependabot[bot]", "github-actions[bot]", ("o" * 39) + "[bot]"):
            with self.subTest(login=login):
                self.assertTrue(crg.is_github_login(login))
                self.assertFalse(crg.is_github_account_name(login))
                with self.assertRaises(crg.InvalidInputError):
                    crg.validate_owner(login)
        for login in (("o" * 40) + "[bot]", "[bot]", "dependabot[bot]x", "dependabot[BOT]", "dependabot[bot][bot]"):
            with self.subTest(login=login):
                self.assertFalse(crg.is_github_login(login))

    def test_classic_name_rule_is_unchanged(self):
        for name in ("octocat", "octo-org", "o" * 39, "a", "0"):
            with self.subTest(name=name):
                self.assertTrue(crg.is_github_account_name(name))
        for name in ("o" * 40, "-octo", "octo.org", "octo org", ""):
            with self.subTest(name=name):
                self.assertFalse(crg.is_github_account_name(name))

    def test_build_argv_for_managed_owner_is_exact_read_only_get(self):
        target = crg.make_target(MANAGED_REPOSITORY, NUMBER)
        argv = crg.build_gh_api_argv(FAKE_GH, target)
        self.assertEqual(argv, [FAKE_GH] + MANAGED_ARGV_TAIL)
        crg.assert_read_only_gh_argv(argv)

    def test_read_only_guard_rejects_malformed_managed_endpoints(self):
        valid = crg.build_gh_api_argv(FAKE_GH, crg.make_target(MANAGED_REPOSITORY, NUMBER))
        post = list(valid)
        post[5] = "POST"
        mutations = {
            "post_method": post,
            "merge_endpoint": valid[:-1] + ["repos/developer_acme/widget/pulls/42/merge"],
            "short_shortcode": valid[:-1] + ["repos/developer_ac/widget/pulls/42"],
            "double_underscore": valid[:-1] + ["repos/developer__acme/widget/pulls/42"],
            "empty_handle": valid[:-1] + ["repos/_acme/widget/pulls/42"],
            "shell_owner": valid[:-1] + ["repos/developer_acme;id/widget/pulls/42"],
            "encoded_slash_owner": valid[:-1] + ["repos/developer_acme%2F/widget/pulls/42"],
            "too_long_owner": valid[:-1] + ["repos/%s/widget/pulls/42" % (("x" * 31) + "_acmecorp")],
        }
        for name, argv in mutations.items():
            with self.subTest(mutation=name):
                with self.assertRaises(crg.UnsafeCommandError):
                    crg.assert_read_only_gh_argv(argv)

    def test_malformed_managed_owner_never_invokes_gh(self):
        for name in MALFORMED_MANAGED_NAMES:
            with self.subTest(name=name):
                runner = FakeRunner(ok_result(make_managed_payload()))
                error = self.capture_error(runner, repository=name + "/widget")
                self.assertIsInstance(error, crg.InvalidInputError)
                self.assertEqual(error.exit_code, 2)
                self.assertEqual(runner.calls, [])

    def test_managed_owner_repository_snapshot(self):
        runner = FakeRunner(ok_result(make_managed_payload()))
        snapshot = fetch(runner, repository=MANAGED_REPOSITORY)
        self.assertEqual(snapshot.to_dict(), EXPECTED_MANAGED_SNAPSHOT)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(runner.calls[0]["argv"], [FAKE_GH] + MANAGED_ARGV_TAIL)

    def test_managed_owner_identity_is_case_insensitive(self):
        runner = FakeRunner(ok_result(make_managed_payload()))
        snapshot = fetch(runner, repository="Developer_ACME/widget")
        self.assertEqual(snapshot.repository, MANAGED_REPOSITORY)
        self.assertEqual(runner.calls[0]["argv"][-1], "repos/Developer_ACME/widget/pulls/42")

    def test_managed_user_author_login_in_response(self):
        payload = make_payload()
        payload["user"]["login"] = MANAGED_OWNER
        snapshot = fetch(FakeRunner(ok_result(payload)))
        self.assertEqual(snapshot.author_login, MANAGED_OWNER)
        self.assertEqual(snapshot.to_dict(), dict(EXPECTED_SNAPSHOT, author_login=MANAGED_OWNER))

    def test_managed_user_fork_owner_in_response(self):
        payload = make_payload()
        payload["user"]["login"] = MANAGED_OWNER
        payload["head"]["repo"]["full_name"] = MANAGED_REPOSITORY
        payload["head"]["repo"]["owner"]["login"] = MANAGED_OWNER
        snapshot = fetch(FakeRunner(ok_result(payload)))
        self.assertEqual(snapshot.head_repository, MANAGED_REPOSITORY)
        self.assertEqual(snapshot.repository, REPOSITORY)
        self.assertEqual(
            snapshot.to_dict(),
            dict(EXPECTED_SNAPSHOT, head_repository=MANAGED_REPOSITORY, author_login=MANAGED_OWNER),
        )

    def test_malformed_managed_names_in_response_are_rejected(self):
        cases = []
        for name in MALFORMED_MANAGED_NAMES:
            cases.append((("user", "login"), name))
            if "/" not in name:
                cases.append((("head", "repo", "full_name"), name + "/widget"))
        cases.extend(
            [
                (("head", "repo", "full_name"), "developer_acme/widget/extra"),
                (("head", "repo", "full_name"), "https://github.com/developer_acme/widget"),
                (("base", "repo", "full_name"), "developer_acmecorp1/widget"),
                (("base", "repo", "full_name"), "developer_acme\x00/widget"),
                (("base", "repo", "full_name"), "developer__acme/widget"),
            ]
        )
        for path, value in cases:
            with self.subTest(path=".".join(path), value=value):
                runner = FakeRunner(ok_result(payload_with(path, value)))
                error = self.capture_error(runner)
                self.assertIsInstance(error, crg.MalformedResponseError)
                self.assertEqual(error.details.get("field"), ".".join(path))
                self.assertFalse(error.pr_state_observed)

    def test_underscore_and_hyphen_owners_are_distinct_scopes(self):
        payload = make_managed_payload()
        payload["base"]["repo"]["full_name"] = "developer-acme/widget"
        error = self.capture_error(FakeRunner(ok_result(payload)), repository=MANAGED_REPOSITORY)
        self.assertIsInstance(error, crg.ScopeMismatchError)
        self.assertEqual(error.details["requested_repository"], MANAGED_REPOSITORY)
        self.assertEqual(error.details["observed_repository"], "developer-acme/widget")

    def test_other_managed_owner_is_scope_mismatch(self):
        payload = make_managed_payload()
        payload["base"]["repo"]["full_name"] = "developer_acme2/widget"
        error = self.capture_error(FakeRunner(ok_result(payload)), repository=MANAGED_REPOSITORY)
        self.assertIsInstance(error, crg.ScopeMismatchError)

    def test_managed_owner_head_change_is_head_changed(self):
        payload = make_managed_payload()
        payload["head"]["sha"] = OTHER_SHA
        error = self.capture_error(FakeRunner(ok_result(payload)), repository=MANAGED_REPOSITORY)
        self.assertIsInstance(error, crg.HeadChangedError)
        self.assertEqual(error.details["repository"], MANAGED_REPOSITORY)


class TestFailureClassification(AdapterTestCase):
    CASES = [
        (4, "To get started with GitHub CLI, please run:  gh auth login\n", "auth_failure"),
        (1, "gh: Bad credentials (HTTP 401)\n", "auth_failure"),
        (1, "gh: Resource not accessible by integration (HTTP 403)\n", "auth_failure"),
        (1, "HTTP 401: Bad credentials (https://api.github.com/repos/octo-org/widget/pulls/42)\n", "auth_failure"),
        (1, "gh: API rate limit exceeded for user ID 1. (HTTP 403)\n", "rate_limited"),
        (1, "gh: Too Many Requests (HTTP 429)\n", "rate_limited"),
        (1, "gh: Not Found (HTTP 404)\n", "not_found"),
        (1, "gh: Server Error (HTTP 502)\n", "network_failure"),
        (1, "error connecting to api.github.com\ncheck your internet connection\n", "network_failure"),
        (
            1,
            'Get "https://api.github.com/repos/octo-org/widget/pulls/42": '
            "dial tcp: lookup api.github.com: no such host\n",
            "network_failure",
        ),
        (1, "net/http: TLS handshake timeout\n", "network_failure"),
        (1, "gh: Validation Failed (HTTP 422)\n", "gh_failure"),
        (1, "something unexpected happened\n", "gh_failure"),
        (-9, "", "gh_failure"),
    ]

    def test_gh_failures_are_classified(self):
        for returncode, stderr, category in self.CASES:
            with self.subTest(stderr=stderr, returncode=returncode):
                error = self.capture_error(FakeRunner(fail_result(returncode, stderr)))
                self.assertEqual(error.category, category)
                self.assertIsInstance(error, crg.GitHubAccessError)
                self.assertNotIsInstance(error, crg.PullRequestStateError)
                self.assertFalse(error.pr_state_observed)
                self.assertNotEqual(error.exit_code, crg.EXIT_CODES["head_changed"])

    def test_auth_and_network_failures_are_distinct_from_head_change(self):
        auth = self.capture_error(FakeRunner(fail_result(4, "gh auth login\n")))
        network = self.capture_error(FakeRunner(fail_result(1, "dial tcp: lookup api.github.com: no such host\n")))
        head = self.capture_error(FakeRunner(ok_result(payload_with(("head", "sha"), OTHER_SHA))))
        self.assertEqual(
            [auth.category, network.category, head.category],
            ["auth_failure", "network_failure", "head_changed"],
        )
        self.assertEqual([auth.kind, network.kind, head.kind], ["access", "transport", "pr_state"])
        self.assertEqual(len({auth.exit_code, network.exit_code, head.exit_code}), 3)
        self.assertEqual(
            [auth.pr_state_observed, network.pr_state_observed, head.pr_state_observed],
            [False, False, True],
        )
        self.assertIsInstance(auth, crg.GitHubAccessError)
        self.assertIsInstance(network, crg.GitHubAccessError)
        self.assertIsInstance(head, crg.PullRequestStateError)

    def test_exit_codes_are_unique(self):
        codes = list(crg.EXIT_CODES.values())
        self.assertEqual(len(codes), len(set(codes)))
        self.assertEqual(crg.EXIT_CODES["ok"], 0)
        self.assertNotIn(1, codes)

    def test_token_material_is_redacted_from_errors(self):
        stderr = (
            "gh: Bad credentials (HTTP 401)\n"
            "Authorization: token " + FAKE_CLASSIC_TOKEN + "\n"
            "retry with " + FAKE_FINE_GRAINED_TOKEN + "\n"
            "Bearer " + FAKE_BEARER_VALUE + "\n"
            "https://user:" + FAKE_BEARER_VALUE + "@github.com/\n"
        )
        error = self.capture_error(FakeRunner(fail_result(1, stderr)))
        self.assertIsInstance(error, crg.AuthFailureError)
        rendered = json.dumps(error.to_dict()) + str(error)
        for secret in (FAKE_CLASSIC_TOKEN, FAKE_FINE_GRAINED_TOKEN, FAKE_BEARER_VALUE, "Q7Q7Q7Q7", "Z9Z9Z9Z9"):
            self.assertNotIn(secret, rendered)
        excerpt = error.details["gh_stderr_excerpt"]
        self.assertIn("[REDACTED]", excerpt)
        self.assertIn("HTTP 401", excerpt)

    def test_diagnostics_are_bounded_and_control_free(self):
        self.assertEqual(crg.sanitize_diagnostic(b"\x1b[31mred\x1b[0m\x00\x07"), "red")
        long_text = "word " * 1000
        cleaned = crg.sanitize_diagnostic(long_text)
        self.assertLessEqual(len(cleaned), crg.MAX_DIAGNOSTIC_CHARS + len(" [truncated]"))
        self.assertTrue(cleaned.endswith("[truncated]"))
        self.assertEqual(crg.sanitize_diagnostic(None), "")


class TestCli(AdapterTestCase):
    def args(self, route, **overrides):
        values = {
            "--repo": REPOSITORY,
            "--number": str(NUMBER),
            "--expected-base-sha": BASE_SHA,
            "--expected-head-sha": HEAD_SHA,
            "--gh": FAKE_GH,
        }
        values.update(overrides)
        argv = [route]
        for flag, value in values.items():
            argv.extend([flag, value])
        return argv

    def run_cli(self, argv, runner):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = crg.main(argv, runner=runner)
        text = out.getvalue()
        self.assertTrue(text.endswith("\n"))
        self.assertEqual(text.count("\n"), 1)
        envelope = json.loads(text)
        self.assertEqual(text.strip(), crg.canonical_json(envelope))
        return code, envelope, text

    def test_snapshot_route_success(self):
        code, envelope, _ = self.run_cli(self.args("snapshot"), FakeRunner(ok_result()))
        self.assertEqual(code, 0)
        self.assertIs(envelope["ok"], True)
        self.assertEqual(envelope["route"], "snapshot")
        self.assertEqual(envelope["schema"], "heleos.github-pr-read-result/v1")
        self.assertEqual(envelope["snapshot"], EXPECTED_SNAPSHOT)
        expected_digest = hashlib.sha256(crg.canonical_json(EXPECTED_SNAPSHOT).encode("ascii")).hexdigest()
        self.assertEqual(envelope["snapshot_sha256"], expected_digest)

    def test_check_route_success(self):
        code, envelope, _ = self.run_cli(self.args("check"), FakeRunner(ok_result()))
        self.assertEqual(code, 0)
        self.assertEqual(envelope["route"], "check")
        self.assertNotIn("snapshot", envelope)
        self.assertEqual(envelope["repository"], REPOSITORY)
        self.assertEqual(envelope["number"], NUMBER)
        self.assertEqual(envelope["head_sha"], HEAD_SHA)
        self.assertEqual(envelope["base_sha"], BASE_SHA)
        expected_digest = hashlib.sha256(crg.canonical_json(EXPECTED_SNAPSHOT).encode("ascii")).hexdigest()
        self.assertEqual(envelope["snapshot_sha256"], expected_digest)

    def test_check_route_head_changed(self):
        runner = FakeRunner(ok_result(payload_with(("head", "sha"), OTHER_SHA)))
        code, envelope, _ = self.run_cli(self.args("check"), runner)
        self.assertEqual(code, crg.EXIT_CODES["head_changed"])
        self.assertIs(envelope["ok"], False)
        self.assertEqual(envelope["category"], "head_changed")
        self.assertIs(envelope["pr_state_observed"], True)
        self.assertEqual(envelope["details"]["observed_head_sha"], OTHER_SHA)

    def test_check_route_closed(self):
        runner = FakeRunner(ok_result(payload_with(("state",), "closed")))
        code, envelope, _ = self.run_cli(self.args("check"), runner)
        self.assertEqual(code, crg.EXIT_CODES["pr_not_open"])
        self.assertEqual(envelope["category"], "pr_not_open")

    def test_check_route_auth_failure_without_token_material(self):
        stderr = "gh auth login required\nAuthorization: Bearer " + FAKE_CLASSIC_TOKEN + "\n"
        code, envelope, text = self.run_cli(self.args("check"), FakeRunner(fail_result(4, stderr)))
        self.assertEqual(code, crg.EXIT_CODES["auth_failure"])
        self.assertEqual(envelope["category"], "auth_failure")
        self.assertIs(envelope["pr_state_observed"], False)
        self.assertNotIn(FAKE_CLASSIC_TOKEN, text)
        self.assertNotIn("Q7Q7Q7Q7", text)

    def test_snapshot_route_network_failure(self):
        runner = FakeRunner(fail_result(1, "error connecting to api.github.com\n"))
        code, envelope, _ = self.run_cli(self.args("snapshot"), runner)
        self.assertEqual(code, crg.EXIT_CODES["network_failure"])
        self.assertEqual(envelope["kind"], "transport")
        self.assertNotEqual(code, crg.EXIT_CODES["head_changed"])

    def test_malformed_repository_never_runs_gh(self):
        for repo in ("octo-org", "octo-org/widget/extra", "octo-org/widget;id", "../widget", "octo-org/widget.git"):
            with self.subTest(repo=repo):
                runner = FakeRunner(ok_result())
                code, envelope, _ = self.run_cli(self.args("snapshot", **{"--repo": repo}), runner)
                self.assertEqual(code, 2)
                self.assertEqual(envelope["category"], "invalid_input")
                self.assertEqual(runner.calls, [])

    def test_snapshot_route_managed_user_owner_and_fork(self):
        runner = FakeRunner(ok_result(make_managed_payload()))
        code, envelope, _ = self.run_cli(self.args("snapshot", **{"--repo": MANAGED_REPOSITORY}), runner)
        self.assertEqual(code, 0)
        self.assertIs(envelope["ok"], True)
        self.assertEqual(envelope["snapshot"], EXPECTED_MANAGED_SNAPSHOT)
        expected_digest = hashlib.sha256(crg.canonical_json(EXPECTED_MANAGED_SNAPSHOT).encode("ascii")).hexdigest()
        self.assertEqual(envelope["snapshot_sha256"], expected_digest)
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(runner.calls[0]["argv"], [FAKE_GH] + MANAGED_ARGV_TAIL)

    def test_check_route_managed_user_owner(self):
        runner = FakeRunner(ok_result(make_managed_payload()))
        code, envelope, _ = self.run_cli(self.args("check", **{"--repo": MANAGED_REPOSITORY}), runner)
        self.assertEqual(code, 0)
        self.assertNotIn("snapshot", envelope)
        self.assertEqual(envelope["repository"], MANAGED_REPOSITORY)
        self.assertEqual(envelope["number"], NUMBER)
        expected_digest = hashlib.sha256(crg.canonical_json(EXPECTED_MANAGED_SNAPSHOT).encode("ascii")).hexdigest()
        self.assertEqual(envelope["snapshot_sha256"], expected_digest)
        self.assertEqual(runner.calls[0]["argv"], [FAKE_GH] + MANAGED_ARGV_TAIL)

    def test_check_route_managed_user_head_changed(self):
        payload = make_managed_payload()
        payload["head"]["sha"] = OTHER_SHA
        code, envelope, _ = self.run_cli(
            self.args("check", **{"--repo": MANAGED_REPOSITORY}), FakeRunner(ok_result(payload))
        )
        self.assertEqual(code, crg.EXIT_CODES["head_changed"])
        self.assertEqual(envelope["category"], "head_changed")
        self.assertEqual(envelope["details"]["repository"], MANAGED_REPOSITORY)

    def test_snapshot_route_managed_user_author_on_classic_repository(self):
        payload = make_payload()
        payload["user"]["login"] = MANAGED_OWNER
        payload["head"]["repo"]["full_name"] = MANAGED_REPOSITORY
        code, envelope, _ = self.run_cli(self.args("snapshot"), FakeRunner(ok_result(payload)))
        self.assertEqual(code, 0)
        self.assertEqual(envelope["snapshot"]["author_login"], MANAGED_OWNER)
        self.assertEqual(envelope["snapshot"]["head_repository"], MANAGED_REPOSITORY)
        self.assertEqual(envelope["snapshot"]["repository"], REPOSITORY)

    def test_malformed_managed_repository_never_runs_gh(self):
        # Names beginning with "-" are excluded: argparse treats them as options.
        for name in [name for name in MALFORMED_MANAGED_NAMES if not name.startswith("-")]:
            repo = name + "/widget"
            with self.subTest(repo=repo):
                runner = FakeRunner(ok_result(make_managed_payload()))
                code, envelope, _ = self.run_cli(self.args("snapshot", **{"--repo": repo}), runner)
                self.assertEqual(code, 2)
                self.assertEqual(envelope["category"], "invalid_input")
                self.assertEqual(runner.calls, [])

    def test_malformed_managed_login_in_response_is_integrity_failure(self):
        for login in ("developer_ac", "developer__acme", "developer_acme[bot]", "developer_acme;id"):
            with self.subTest(login=login):
                payload = make_managed_payload()
                payload["user"]["login"] = login
                code, envelope, _ = self.run_cli(
                    self.args("check", **{"--repo": MANAGED_REPOSITORY}), FakeRunner(ok_result(payload))
                )
                self.assertEqual(code, crg.EXIT_CODES["malformed_response"])
                self.assertEqual(envelope["details"]["field"], "user.login")
                self.assertIs(envelope["pr_state_observed"], False)

    def test_malformed_number_never_runs_gh(self):
        for number in ("0", "042", "+42", "4.2", "42abc", "４２", ""):
            with self.subTest(number=number):
                runner = FakeRunner(ok_result())
                code, envelope, _ = self.run_cli(self.args("check", **{"--number": number}), runner)
                self.assertEqual(code, 2)
                self.assertEqual(envelope["details"]["field"], "number")
                self.assertEqual(runner.calls, [])

    def test_invalid_sha_hostname_and_timeout_never_run_gh(self):
        for overrides in (
            {"--expected-head-sha": HEAD_SHA.upper()},
            {"--expected-base-sha": "main"},
            {"--hostname": "github.com/evil"},
            {"--timeout": "0"},
        ):
            with self.subTest(overrides=overrides):
                runner = FakeRunner(ok_result())
                code, envelope, _ = self.run_cli(self.args("check", **overrides), runner)
                self.assertEqual(code, 2)
                self.assertEqual(envelope["category"], "invalid_input")
                self.assertEqual(runner.calls, [])

    def test_usage_errors_exit_two(self):
        missing_head = [
            "check",
            "--repo",
            REPOSITORY,
            "--number",
            "42",
            "--expected-base-sha",
            BASE_SHA,
        ]
        for argv in (missing_head, ["merge"] + self.args("check")[1:], []):
            with self.subTest(argv=argv):
                runner = FakeRunner(ok_result())
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as ctx:
                        crg.main(argv, runner=runner)
                self.assertEqual(ctx.exception.code, 2)
                self.assertEqual(runner.calls, [])


class TestSourceSafety(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = MODULE_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_module_has_no_write_shell_or_credential_paths(self):
        forbidden = [
            "shell=True",
            "os.system(",
            "os.popen(",
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "GH_ENTERPRISE_TOKEN",
            "auth token",
            "hosts.yml",
            '"POST"',
            '"PATCH"',
            '"PUT"',
            '"DELETE"',
            "--input",
            "--field",
            "--raw-field",
            "--paginate",
            "pr merge",
            "pr create",
            "pr comment",
            "pr review",
            "pr edit",
            "git push",
        ]
        for needle in forbidden:
            with self.subTest(needle=needle):
                self.assertNotIn(needle, self.source)

    def test_module_imports_only_stdlib(self):
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                imported.add("<relative>" if node.level else node.module.split(".")[0])
        allowed = {
            "__future__",
            "argparse",
            "dataclasses",
            "hashlib",
            "json",
            "math",
            "os",
            "re",
            "shutil",
            "subprocess",
            "sys",
            "typing",
        }
        self.assertTrue(imported <= allowed, sorted(imported - allowed))

    def test_only_subprocess_run_with_shell_false(self):
        subprocess_calls = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if isinstance(owner, ast.Name) and owner.id == "subprocess":
                subprocess_calls.append(node)
            if isinstance(owner, ast.Name) and owner.id == "os":
                self.assertFalse(
                    node.func.attr.startswith(("system", "popen", "exec", "spawn", "fork", "posix_spawn", "startfile")),
                    node.func.attr,
                )
        self.assertEqual([call.func.attr for call in subprocess_calls], ["run"])
        shell_keywords = [kw for kw in subprocess_calls[0].keywords if kw.arg == "shell"]
        self.assertEqual(len(shell_keywords), 1)
        self.assertIsInstance(shell_keywords[0].value, ast.Constant)
        self.assertIs(shell_keywords[0].value.value, False)

    def test_importing_module_has_no_side_effects(self):
        with mock.patch.object(subprocess, "run") as run_mock, mock.patch.object(subprocess, "Popen") as popen_mock:
            spec = importlib.util.spec_from_file_location("cross_review_github_reimport", MODULE_PATH)
            module = importlib.util.module_from_spec(spec)
            sys.modules["cross_review_github_reimport"] = module
            try:
                spec.loader.exec_module(module)
            finally:
                sys.modules.pop("cross_review_github_reimport", None)
        run_mock.assert_not_called()
        popen_mock.assert_not_called()
        self.assertTrue(callable(module.fetch_pull_request_snapshot))
        self.assertTrue(callable(module.main))


if __name__ == "__main__":
    unittest.main()
