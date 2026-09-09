"""Black-box checks for the bounded Kimi stdin adapter; no real provider runs.

Local RED/GREEN evidence (2026-09-09 UTC, macOS, Python 3.14.6):
  python3 -B tests/provider-adapters/test_kimi_stdin.py -v
  RED before implementation: exit 1; 18 tests, 35 failed assertions.
  GREEN after implementation: exit 0; 18 tests passed.
  Added exact-read-bound regression: RED exit 1, observed stdin offset 65548
  instead of 65537 with BufferedReader.read(65537). Bounded os.read fixed it.
  Final full suite with that regression: exit 0; all 19 tests passed.
Fixtures exercise real subprocess boundaries; Windows and real Kimi are untested.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import runpy


ADAPTER = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "provider-adapters"
    / "kimi-stdin.py"
)
USAGE = b"kimi-stdin: usage: --kimi-executable ABSOLUTE_PATH\n"
OVERSIZED = b"kimi-stdin: prompt exceeds 65536 bytes\n"
INVALID_UTF8 = b"kimi-stdin: prompt is not valid UTF-8\n"
NUL_INPUT = b"kimi-stdin: prompt contains NUL\n"
LAUNCH_FAILED = b"kimi-stdin: provider launch failed\n"
SIGNALLED = b"kimi-stdin: provider terminated by signal\n"
READ_FAILED = b"kimi-stdin: prompt read failed\n"
STATE_UNAVAILABLE = b"kimi-stdin: read-only authentication/runtime separation unavailable\n"
PROBE_FAILED = b"kimi-stdin: executable capability probe failed\n"


@unittest.skipUnless(os.name == "posix", "executable-script fixtures require POSIX")
class KimiStdinTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="kimi-stdin-tests-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.fake = self.root / "fake kimi; literal executable"
        self.write_fake(
            "import json, sys\n"
            "sys.stdout.write(json.dumps(sys.argv[1:], ensure_ascii=False))\n"
        )

    def write_fake(self, body):
        # Only the test fixture writes files; the adapter has no write authority.
        self.fake.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        self.fake.chmod(0o700)

    def invoke(self, prompt=b"public fixture", *, arguments=None):
        if arguments is None:
            arguments = ["--kimi-executable", str(self.fake)]
        return subprocess.run(
            [sys.executable, "-B", str(ADAPTER), *arguments],
            input=prompt,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.root,
            timeout=10,
            check=False,
        )

    def assert_rejected(self, result, status, diagnostic):
        self.assertEqual(result.returncode, status)
        self.assertEqual(result.stdout, b"", "provider must not run on rejection")
        self.assertEqual(result.stderr, diagnostic)
        self.assertNotIn(str(self.root).encode(), result.stderr)

    def assert_prompt_passed(self, prompt):
        result = self.invoke(prompt.encode("utf-8"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            json.loads(result.stdout),
            ["--output-format", "stream-json", "--prompt", prompt],
        )

    def test_prompt_is_one_literal_argument_and_cannot_inject_shell_commands(self):
        prompt = (
            "--config-file=forbidden; touch injected_semicolon; "
            "$(touch injected_substitution) `touch injected_backtick` "
            "'single' \"double\" $HOME && touch injected_and | "
            "touch injected_pipe > injected_redirect\n* ? \\ end"
        )
        self.assert_prompt_passed(prompt)
        self.assertEqual(list(self.root.iterdir()), [self.fake])

    def test_executable_metacharacters_are_literal(self):
        self.fake = self.root / "$(touch injected_executable)"
        self.write_fake("import sys\nsys.stdout.write('literal executable')\n")
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"literal executable")
        self.assertFalse((self.root / "injected_executable").exists())

    def test_prompt_whitespace_unicode_bom_and_newlines_are_preserved(self):
        self.assert_prompt_passed("\ufeff  public \u00e9 \U0001f642\r\nsecond line\n\t")

    def test_empty_prompt_is_passed_as_an_empty_argument(self):
        self.assert_prompt_passed("")

    def test_denied_devnull_still_launches_child_with_drained_stdin(self):
        # Reproduce Seatbelt's denied /dev/null open inside the adapter process,
        # while retaining a real child launch and the actual EOF input pipe.
        self.write_fake(
            "import json, os, sys\n"
            "sys.stdout.write(json.dumps({\n"
            "    'argv': sys.argv[1:],\n"
            "    'stdin_reads': [list(os.read(0, 1)), list(os.read(0, 1))],\n"
            "}, ensure_ascii=False))\n"
        )
        harness = (
            "import errno, os, runpy, sys\n"
            "original_open = os.open\n"
            "def denied_devnull(path, flags, *args, **kwargs):\n"
            "    if os.fspath(path) in ('/dev/null', b'/dev/null'):\n"
            "        raise PermissionError(errno.EPERM, 'fixture denied')\n"
            "    return original_open(path, flags, *args, **kwargs)\n"
            "os.open = denied_devnull\n"
            "sys.argv = sys.argv[1:]\n"
            "runpy.run_path(sys.argv[0], run_name='__main__')\n"
        )
        prompt = "  public \u00e9\n--flag; $(literal)\n"
        result = subprocess.run(
            [sys.executable, "-B", "-c", harness, str(ADAPTER),
             "--kimi-executable", str(self.fake)],
            input=prompt.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.root,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(json.loads(result.stdout), {
            "argv": ["--output-format", "stream-json", "--prompt", prompt],
            "stdin_reads": [[], []],
        })

    def test_exactly_65536_prompt_bytes_are_accepted(self):
        self.assert_prompt_passed("a" * 65536)

    def test_multibyte_utf8_limit_counts_bytes(self):
        self.assert_prompt_passed("\u00e9" * 32768)
        self.assert_rejected(self.invoke(("\u00e9" * 32769).encode()), 65, OVERSIZED)

    def test_65537_prompt_bytes_are_rejected_without_echoing_input(self):
        prompt = b"PROMPT_MUST_REMAIN_REDACTED" + b"x" * 65536
        self.assert_rejected(self.invoke(prompt[:65537]), 65, OVERSIZED)

    def test_read_bound_does_not_prefetch_more_than_65537_bytes(self):
        # The shared kernel file offset exposes BufferedReader prefetch, which
        # a bounded return value alone does not rule out.
        with tempfile.TemporaryFile(dir=self.root) as prompt_file:
            prompt_file.write(b"x" * 65537 + b"UNREAD_TAIL")
            prompt_file.seek(0)
            result = subprocess.run(
                [sys.executable, "-B", str(ADAPTER), "--kimi-executable", str(self.fake)],
                stdin=prompt_file,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.root,
                timeout=10,
                check=False,
            )
            self.assert_rejected(result, 65, OVERSIZED)
            self.assertEqual(prompt_file.tell(), 65537)

    def test_oversized_prompt_is_rejected_without_waiting_for_eof(self):
        # An unbounded read would wait forever while this pipe remains open.
        self.assertTrue(ADAPTER.is_file(), "adapter implementation is absent")
        process = subprocess.Popen(
            [sys.executable, "-B", str(ADAPTER), "--kimi-executable", str(self.fake)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.root,
        )
        try:
            process.stdin.write(b"x" * 65537)
            process.stdin.flush()
            self.assertEqual(process.wait(timeout=5), 65)
            self.assertEqual(process.stdout.read(), b"")
            self.assertEqual(process.stderr.read(), OVERSIZED)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()

    def test_non_utf8_prompt_is_rejected_without_echoing_input(self):
        for prompt in (b"PRIVATE_PREFIX\xff", b"\xc0\xaf", b"\xed\xa0\x80", b"\xe2\x82"):
            with self.subTest(prompt_length=len(prompt)):
                self.assert_rejected(self.invoke(prompt), 65, INVALID_UTF8)

    def test_nul_prompt_is_rejected_without_echoing_input(self):
        self.assert_rejected(self.invoke(b"PRIVATE_PREFIX\x00PRIVATE_SUFFIX"), 65, NUL_INPUT)

    def test_stdin_read_failure_has_a_fixed_typed_failure(self):
        # Close fd 0 after Python's standard-stream initialization, so the real
        # adapter read receives EBADF instead of failing interpreter startup.
        harness = (
            "import os, runpy, sys; os.close(0); sys.argv = sys.argv[1:]; "
            "runpy.run_path(sys.argv[0], run_name='__main__')"
        )
        result = subprocess.run(
            [sys.executable, "-B", "-c", harness, str(ADAPTER),
             "--kimi-executable", str(self.fake)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.root,
            timeout=10,
            check=False,
        )
        self.assert_rejected(result, 74, READ_FAILED)

    def test_stdout_and_stderr_pass_through_as_exact_bytes(self):
        self.write_fake(
            "import os\n"
            "os.write(1, b'provider stdout\\x00\\xff\\n')\n"
            "os.write(2, b'provider stderr\\xfe\\r\\n')\n"
        )
        result = self.invoke()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"provider stdout\x00\xff\n")
        self.assertEqual(result.stderr, b"provider stderr\xfe\r\n")

    def test_ordinary_provider_exit_codes_are_propagated(self):
        for status in (0, 1, 2, 42, 64, 70, 127, 255):
            with self.subTest(status=status):
                self.write_fake(f"import sys\nsys.exit({status})\n")
                result = self.invoke()
                self.assertEqual(result.returncode, status)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, b"")

    def test_child_signal_has_a_fixed_typed_failure(self):
        self.write_fake(
            "import os, signal\nos.kill(os.getpid(), signal.SIGTERM)\n"
        )
        self.assert_rejected(self.invoke(), 71, SIGNALLED)

    def test_missing_executable_has_a_fixed_redacted_failure(self):
        arguments = ["--kimi-executable", str(self.root / "PRIVATE_MISSING_PATH")]
        self.assert_rejected(self.invoke(arguments=arguments), 70, LAUNCH_FAILED)

    def test_non_executable_has_a_fixed_redacted_failure(self):
        self.fake.chmod(0o600)
        self.assert_rejected(self.invoke(), 70, LAUNCH_FAILED)

    def test_invalid_executable_format_does_not_fall_back_to_a_shell(self):
        self.fake.write_text("touch injected_format\n", encoding="utf-8")
        self.assert_rejected(self.invoke(), 70, LAUNCH_FAILED)
        self.assertFalse((self.root / "injected_format").exists())

    def test_cli_requires_one_explicit_absolute_executable_without_extra_flags(self):
        cases = (
            [],
            [str(self.fake)],
            ["--kimi-executable"],
            ["--kimi-executable", "kimi"],
            ["--kimi-executable", "./kimi"],
            ["--kimi-executable", ""],
            ["--kimi-executable", str(self.fake), "--config-file=PRIVATE_PATH"],
            ["--PRIVATE_UNKNOWN_FLAG", str(self.fake)],
        )
        for arguments in cases:
            with self.subTest(argument_count=len(arguments)):
                self.assert_rejected(self.invoke(arguments=arguments), 64, USAGE)

    def test_state_probe_reads_executable_without_running_it_or_reading_prompt(self):
        # Break caught: launching a version/help command can load auth or write
        # runtime files; an executable-byte probe must never execute the file.
        self.write_fake("raise RuntimeError('MUST_NOT_EXECUTE')\n")
        with tempfile.TemporaryFile(dir=self.root) as prompt:
            prompt.write(b"PRIVATE_PROMPT")
            prompt.seek(0)
            result = subprocess.run(
                [sys.executable, "-B", str(ADAPTER), "--kimi-executable",
                 str(self.fake), "--probe-state-layout"],
                stdin=prompt, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=10, check=False,
            )
            self.assertEqual(prompt.tell(), 0)
        self.assertEqual(result.returncode, 78, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(json.loads(result.stdout), {
            "schema": "heleos.kimi-state-capability/v1",
            "available": False,
            "reason": "unverified_executable",
            "read_only_auth_writable_runtime": False,
        })

    def test_reviewed_build_and_unknown_digest_both_refuse_split_state(self):
        # Exact-byte admission prevents a same-version replacement from
        # inheriting the reviewed build's capability classification.
        adapter = runpy.run_path(str(ADAPTER))
        classify = adapter.get("state_capability")
        self.assertTrue(callable(classify), "state capability classifier is absent")
        known = classify("9f4337e10da47843f6b550474012a53ba8b30dd665f83b176a5cd479c5f7e859")
        self.assertFalse(known["available"])
        self.assertFalse(known["read_only_auth_writable_runtime"])
        self.assertEqual(known["reason"], "combined_auth_runtime_root")
        self.assertEqual(known["reviewed_version"], "0.34.0")
        unknown = classify("0f4337e10da47843f6b550474012a53ba8b30dd665f83b176a5cd479c5f7e859")
        self.assertEqual(unknown["reason"], "unverified_executable")
        self.assertNotIn("reviewed_version", unknown)

    def test_state_probe_refuses_nonregular_symlink_missing_and_oversized_executables(self):
        link = self.root / "linked-executable"
        link.symlink_to(self.fake)
        fifo = self.root / "executable-fifo"
        os.mkfifo(fifo, 0o700)
        oversized = self.root / "oversized-executable"
        with oversized.open("wb") as output:
            output.truncate(268435457)
        oversized.chmod(0o700)
        for target in (link, fifo, self.root, self.root / "PRIVATE_MISSING", oversized):
            with self.subTest(kind=target.name):
                result = self.invoke(arguments=["--kimi-executable", str(target), "--probe-state-layout"])
                self.assert_rejected(result, 78, PROBE_FAILED)
        self.fake.chmod(0o600)
        self.assert_rejected(self.invoke(arguments=[
            "--kimi-executable", str(self.fake), "--probe-state-layout",
        ]), 78, PROBE_FAILED)

    def test_state_root_request_refuses_before_prompt_read_and_without_touching_root(self):
        # Missing and existing roots both refuse: neither directory creation
        # nor credential/config inspection is permitted by this capability.
        for target in (self.root, self.root / "PRIVATE_MISSING_STATE"):
            with self.subTest(existing=target.exists()):
                with tempfile.TemporaryFile(dir=self.root) as prompt:
                    prompt.write(b"PRIVATE_PROMPT")
                    prompt.seek(0)
                    result = subprocess.run(
                        [sys.executable, "-B", str(ADAPTER), "--kimi-executable",
                         str(self.fake), "--worker-state-root", str(target)],
                        stdin=prompt, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        timeout=10, check=False,
                    )
                    self.assert_rejected(result, 78, STATE_UNAVAILABLE)
                    self.assertEqual(prompt.tell(), 0)
        self.assertEqual(list(self.root.iterdir()), [self.fake])

    def test_state_mode_flags_are_strict_and_cannot_smuggle_provider_flags(self):
        for suffix in (
            ["--worker-state-root"],
            ["--worker-state-root", "relative"],
            ["--worker-state-root", str(self.root), "--auto"],
            ["--probe-state-layout", "--worker-state-root", str(self.root)],
            ["--probe-state-layout", "--probe-state-layout"],
        ):
            with self.subTest(suffix=suffix):
                self.assert_rejected(self.invoke(arguments=[
                    "--kimi-executable", str(self.fake), *suffix,
                ]), 64, USAGE)


if __name__ == "__main__":
    unittest.main()
