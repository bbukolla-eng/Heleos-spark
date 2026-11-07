"""Black-box Codex adapter contracts, using local executables only."""

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import unittest


ADAPTER = Path(__file__).resolve().parents[2] / "scripts/provider-adapters/codex-stdin.py"
USAGE = b"codex-stdin: usage: --codex-executable ABSOLUTE_REGULAR_EXECUTABLE\n"
OVERSIZED = b"codex-stdin: prompt exceeds 65536 bytes\n"
INVALID_UTF8 = b"codex-stdin: prompt is not valid UTF-8\n"
NUL_INPUT = b"codex-stdin: prompt contains NUL\n"
LAUNCH_FAILED = b"codex-stdin: provider launch failed\n"
SIGNALLED = b"codex-stdin: terminated by signal\n"
READ_FAILED = b"codex-stdin: prompt read failed\n"


@unittest.skipUnless(os.name == "posix", "executable fixtures require POSIX")
class CodexStdinTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="codex-stdin-tests-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()
        self.fake = self.root / "fake codex; $(literal) executable"
        self.write_fake(
            "import json, os, sys\n"
            "print(json.dumps({'argv': sys.argv, 'cwd': os.getcwd(),\n"
            " 'stdin': sys.stdin.buffer.read().decode('utf-8')}, ensure_ascii=False))\n"
        )

    def write_fake(self, body):
        self.fake.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        self.fake.chmod(0o700)

    def command(self, arguments=None):
        if arguments is None:
            arguments = ["--codex-executable", str(self.fake)]
        return [sys.executable, "-B", str(ADAPTER), *arguments]

    def invoke(self, prompt=b"public fixture", arguments=None):
        return subprocess.run(
            self.command(arguments), input=prompt, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=self.root, timeout=10, check=False,
        )

    def assert_rejected(self, result, status, diagnostic):
        self.assertEqual(result.returncode, status, result.stderr)
        self.assertEqual(result.stdout, b"", "provider must not run on rejection")
        self.assertEqual(result.stderr, diagnostic)
        self.assertNotIn(str(self.root).encode(), result.stderr)

    def assert_prompt_passed(self, prompt):
        result = self.invoke(prompt.encode("utf-8"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        report = json.loads(result.stdout)
        self.assertEqual(report, {
            "argv": [str(self.fake), "exec", "--ephemeral", "--ignore-user-config",
                     "--ignore-rules", "--strict-config", "--model", "gpt-6-astra",
                     "--sandbox", "workspace-write", "--json", "--color", "never", "-"],
            "cwd": str(self.root), "stdin": prompt,
        })
        self.assertEqual(set(self.root.iterdir()), {self.fake})

    # Catches shell interpretation, prompt-in-argv, and changed safety/model flags.
    def test_exact_pinned_argv_cwd_and_literal_stdin(self):
        self.assert_prompt_passed(
            "--dangerously-bypass-approvals-and-sandbox; touch INJECTED; "
            "$(touch SUBSTITUTION) `touch BACKTICK` 'single' \"double\" $HOME\n"
            "--config x --profile y --add-dir /tmp | touch PIPE > REDIRECT"
        )

    def test_unicode_bom_whitespace_and_empty_input_preserved(self):
        for prompt in ("", "\ufeff \u00e9 \U0001f642\r\nsecond line\n\t"):
            with self.subTest(length=len(prompt)):
                self.assert_prompt_passed(prompt)

    def test_exact_byte_limit_and_multibyte_limit(self):
        self.assert_prompt_passed("a" * 65536)
        self.assert_prompt_passed("\u00e9" * 32768)
        self.assert_rejected(self.invoke(b"x" * 65537), 65, OVERSIZED)
        self.assert_rejected(self.invoke(("\u00e9" * 32769).encode()), 65, OVERSIZED)

    def test_os_reads_stop_after_single_overflow_byte(self):
        with tempfile.TemporaryFile(dir=self.root) as source:
            source.write(b"x" * 65537 + b"UNREAD_TAIL")
            source.seek(0)
            result = subprocess.run(
                self.command(), stdin=source, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=self.root, timeout=10,
            )
            self.assert_rejected(result, 65, OVERSIZED)
            self.assertEqual(source.tell(), 65537)

    def test_overflow_rejected_without_waiting_for_eof(self):
        self.assertTrue(ADAPTER.is_file(), "adapter implementation is absent")
        with subprocess.Popen(
            self.command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=self.root,
        ) as process:
            try:
                process.stdin.write(b"x" * 65537)
                process.stdin.flush()
                self.assertEqual(process.wait(timeout=5), 65)
                self.assertEqual(process.stdout.read(), b"")
                self.assertEqual(process.stderr.read(), OVERSIZED)
            finally:
                if process.poll() is None:
                    process.kill()

    def test_bad_utf8_and_nul_rejected_without_launch(self):
        for prompt in (b"PRIVATE\xff", b"\xc0\xaf", b"\xed\xa0\x80", b"\xe2\x82"):
            with self.subTest(length=len(prompt)):
                self.assert_rejected(self.invoke(prompt), 65, INVALID_UTF8)
        self.assert_rejected(self.invoke(b"PRIVATE\x00SECRET"), 65, NUL_INPUT)

    def invoke_harness(self, setup):
        # Exercise real OS descriptor failures after imports, so fd 0 is not reused.
        harness = (
            "import os, runpy, sys\n"
            "sys.argv = sys.argv[1:]\n"
            "namespace = runpy.run_path(sys.argv[0])\n" + setup + "\n"
            "raise SystemExit(namespace['main']())\n"
        )
        return subprocess.run(
            [sys.executable, "-B", "-c", harness, *self.command()[2:]],
            input=b"public fixture", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.root, timeout=10, check=False,
        )

    def test_real_stdin_read_failure_is_bounded_and_redacted(self):
        self.assertTrue(ADAPTER.is_file(), "adapter implementation is absent")
        self.assert_rejected(self.invoke_harness("os.close(0)"), 74, READ_FAILED)

    def test_stdin_closed_before_interpreter_start_is_fixed_failure(self):
        result = subprocess.run(
            self.command(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=self.root, timeout=10,
            preexec_fn=lambda: os.close(0),
        )
        self.assert_rejected(result, 74, READ_FAILED)

    def test_stdout_stderr_and_ordinary_exit_are_preserved(self):
        for status in (0, 1, 2, 42, 64, 65, 70, 71, 74, 127, 255):
            with self.subTest(status=status):
                self.write_fake(
                    "import os, sys\n"
                    "os.write(1, b'out\\x00\\xff\\n')\n"
                    "os.write(2, b'err\\xfe\\r\\n')\n"
                    f"sys.exit({status})\n"
                )
                result = self.invoke()
                self.assertEqual(result.returncode, status)
                self.assertEqual(result.stdout, b"out\x00\xff\n")
                self.assertEqual(result.stderr, b"err\xfe\r\n")

    def test_provider_signal_preserves_output_and_maps_failure(self):
        self.write_fake(
            "import os, signal\n"
            "os.write(1, b'before signal\\n')\n"
            "os.write(2, b'provider error\\n')\n"
            "os.kill(os.getpid(), signal.SIGTERM)\n"
        )
        result = self.invoke()
        self.assertEqual(result.returncode, 71)
        self.assertEqual(result.stdout, b"before signal\n")
        self.assertEqual(result.stderr, b"provider error\n" + SIGNALLED)

    def test_adapter_signals_terminate_and_reap_direct_child(self):
        self.assertTrue(ADAPTER.is_file(), "adapter implementation is absent")
        self.write_fake(
            "import os, signal\n"
            "print(os.getpid(), flush=True)\n"
            "while True: signal.pause()\n"
        )
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(signal=signum), subprocess.Popen(
                self.command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=self.root,
            ) as process:
                try:
                    process.stdin.close()
                    process.stdin = None
                    self.assertTrue(select.select([process.stdout], [], [], 5)[0])
                    pid = int(process.stdout.readline())
                    process.send_signal(signum)
                    stdout, stderr = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 71)
                    self.assertEqual(stdout, b"")
                    self.assertEqual(stderr, SIGNALLED)
                    with self.assertRaises(ProcessLookupError):
                        os.kill(pid, 0)
                finally:
                    if process.poll() is None:
                        process.kill()

    def test_only_exact_executable_option_is_accepted(self):
        for arguments in (
            [], [str(self.fake)], ["--codex-executable"],
            ["--codex-executable", "codex"], ["--codex-executable", "./codex"],
            ["--codex-executable", ""], ["--PRIVATE_UNKNOWN", str(self.fake)],
            ["--codex-executable=" + str(self.fake)], ["--help"],
            ["--codex-executable", str(self.fake), "--codex-executable", str(self.fake)],
        ):
            with self.subTest(argument_count=len(arguments)):
                self.assert_rejected(self.invoke(arguments=arguments), 64, USAGE)
        for flag in ("--dangerously-bypass-approvals-and-sandbox", "--add-dir",
                     "--config", "--profile", "--mcp", "--plugin", "--model", "--"):
            with self.subTest(flag=flag):
                self.assert_rejected(self.invoke(arguments=[
                    "--codex-executable", str(self.fake), flag, "PRIVATE"
                ]), 64, USAGE)

    def test_executable_must_be_an_absolute_regular_executable(self):
        fifo = self.root / "PRIVATE_FIFO"
        os.mkfifo(fifo, 0o700)
        for executable in (self.root / "PRIVATE_MISSING", self.root, fifo):
            with self.subTest(kind=executable.name):
                self.assert_rejected(self.invoke(arguments=[
                    "--codex-executable", str(executable)
                ]), 64, USAGE)
        self.fake.chmod(0o600)
        self.assert_rejected(self.invoke(), 64, USAGE)

    def test_invalid_executable_format_has_no_shell_fallback(self):
        self.fake.write_text("touch INJECTED_FORMAT\n", encoding="utf-8")
        self.assert_rejected(self.invoke(), 70, LAUNCH_FAILED)
        self.assertFalse((self.root / "INJECTED_FORMAT").exists())


if __name__ == "__main__":
    unittest.main()
