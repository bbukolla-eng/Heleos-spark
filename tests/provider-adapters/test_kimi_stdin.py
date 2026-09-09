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


if __name__ == "__main__":
    unittest.main()
