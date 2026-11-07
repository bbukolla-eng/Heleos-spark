"""Real subprocess checks for Grok stdin transport; never runs Grok itself."""

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import unittest


ADAPTER = Path(__file__).resolve().parents[2] / "scripts/provider-adapters/grok-stdin.py"
USAGE = b"grok-stdin: usage: --grok-executable ABSOLUTE_PATH\n"
OVERSIZED = b"grok-stdin: prompt exceeds 65536 bytes\n"
INVALID_UTF8 = b"grok-stdin: prompt is not valid UTF-8\n"
NUL_INPUT = b"grok-stdin: prompt contains NUL\n"
LAUNCH_FAILED = b"grok-stdin: provider launch failed\n"
SIGNALLED = b"grok-stdin: terminated by signal\n"
READ_FAILED = b"grok-stdin: prompt read failed\n"
FILE_FAILED = b"grok-stdin: prompt file operation failed\n"
FIXED_FLAGS = [
    "--model", "grok-4.6",
    "--output-format", "streaming-json",
    "--permission-mode", "bypassPermissions", "--tools", "Write",
    "--disable-web-search", "--no-subagents", "--no-memory", "--no-plan",
    "--verbatim", "--no-auto-update", "--max-turns", "8",
]


@unittest.skipUnless(os.name == "posix", "executable fixtures require POSIX")
class GrokStdinTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="grok-stdin-tests-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.tmpdir = self.root / "private temp; $(literal)"
        self.tmpdir.mkdir()
        self.env = dict(os.environ, TMPDIR=str(self.tmpdir))
        self.fake = self.root / "fake grok; literal executable"
        self.write_fake(
            "import json, os, pathlib, stat, sys\n"
            "args = sys.argv[1:]\n"
            "path = pathlib.Path(args[args.index('--prompt-file') + 1])\n"
            "sys.stdout.write(json.dumps({\n"
            " 'args': args, 'path': str(path),\n"
            " 'mode': stat.S_IMODE(path.stat().st_mode),\n"
            " 'prompt': path.read_bytes().decode('utf-8'),\n"
            " 'stdin': list(os.read(0, 1)),\n"
            "}, ensure_ascii=False))\n"
        )

    def write_fake(self, body):
        self.fake.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        self.fake.chmod(0o700)

    def command(self, arguments=None):
        if arguments is None:
            arguments = ["--grok-executable", str(self.fake)]
        return [sys.executable, "-B", str(ADAPTER), *arguments]

    def invoke(self, prompt=b"public fixture", *, arguments=None, env=None):
        return subprocess.run(
            self.command(arguments), input=prompt, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=self.root, env=self.env if env is None else env,
            timeout=10, check=False,
        )

    def assert_clean(self):
        self.assertEqual(list(self.tmpdir.iterdir()), [], "prompt file leaked")

    def assert_rejected(self, result, status, diagnostic):
        self.assertEqual(result.returncode, status, result.stderr)
        self.assertEqual(result.stdout, b"", "provider must not run on rejection")
        self.assertEqual(result.stderr, diagnostic)
        self.assertNotIn(str(self.root).encode(), result.stderr)
        self.assert_clean()

    def assert_prompt_passed(self, prompt):
        result = self.invoke(prompt.encode("utf-8"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        report = json.loads(result.stdout)
        self.assertEqual(report["prompt"], prompt)
        self.assertEqual(report["mode"], 0o600)
        self.assertEqual(report["stdin"], [])
        self.assertEqual(report["args"], ["--prompt-file", report["path"], *FIXED_FLAGS])
        self.assertEqual(Path(report["path"]).parent, self.tmpdir)
        self.assertFalse(Path(report["path"]).exists())
        self.assert_clean()

    def test_prompt_metacharacters_are_literal_file_content(self):
        self.assert_prompt_passed(
            "--tools Bash; touch injected_semicolon; $(touch injected_substitution) "
            "`touch injected_backtick` 'single' \"double\" $HOME && touch injected_and "
            "| touch injected_pipe > injected_redirect\n* ? \\ end"
        )
        self.assertEqual(set(self.root.iterdir()), {self.tmpdir, self.fake})

    def test_executable_metacharacters_are_literal(self):
        self.fake = self.root / "$(touch injected_executable)"
        self.write_fake("import sys\nsys.stdout.write('literal executable')\n")
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"literal executable")
        self.assertFalse((self.root / "injected_executable").exists())
        self.assert_clean()

    def test_unicode_bom_whitespace_and_newlines_are_preserved(self):
        self.assert_prompt_passed("\ufeff  public \u00e9 \U0001f642\r\nsecond line\n\t")

    def test_empty_prompt_is_passed_in_an_empty_file(self):
        self.assert_prompt_passed("")

    def test_exactly_65536_bytes_are_accepted(self):
        self.assert_prompt_passed("a" * 65536)

    def test_multibyte_limit_counts_bytes(self):
        self.assert_prompt_passed("\u00e9" * 32768)
        self.assert_rejected(self.invoke(("\u00e9" * 32769).encode()), 65, OVERSIZED)

    def test_exactly_65537_bytes_are_rejected(self):
        self.assert_rejected(self.invoke(b"PRIVATE" + b"x" * 65530), 65, OVERSIZED)

    def test_os_read_does_not_prefetch_past_65537_bytes(self):
        with tempfile.TemporaryFile(dir=self.root) as source:
            source.write(b"x" * 65537 + b"UNREAD_TAIL")
            source.seek(0)
            result = subprocess.run(
                self.command(), stdin=source, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=self.root, env=self.env, timeout=10,
            )
            self.assert_rejected(result, 65, OVERSIZED)
            self.assertEqual(source.tell(), 65537)

    def test_overflow_does_not_wait_for_eof(self):
        self.assertTrue(ADAPTER.is_file(), "adapter implementation is absent")
        process = subprocess.Popen(
            self.command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=self.root, env=self.env,
        )
        try:
            process.stdin.write(b"x" * 65537)
            process.stdin.flush()
            self.assertEqual(process.wait(timeout=5), 65)
            self.assertEqual(process.stdout.read(), b"")
            self.assertEqual(process.stderr.read(), OVERSIZED)
            self.assert_clean()
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()

    def test_invalid_utf8_is_rejected_and_redacted(self):
        for prompt in (b"PRIVATE\xff", b"\xc0\xaf", b"\xed\xa0\x80", b"\xe2\x82"):
            with self.subTest(prompt_length=len(prompt)):
                self.assert_rejected(self.invoke(prompt), 65, INVALID_UTF8)

    def test_nul_is_rejected_and_redacted(self):
        self.assert_rejected(self.invoke(b"PRIVATE\x00SECRET"), 65, NUL_INPUT)

    def invoke_harness(self, setup, prompt=b"public fixture"):
        # Load imports before changing file descriptors or resource limits;
        # otherwise an import can reuse fd 0 and invalidate the EBADF fixture.
        harness = (
            "import os, runpy, signal, sys\n"
            "sys.argv = sys.argv[1:]\n"
            "namespace = runpy.run_path(sys.argv[0])\n" + setup + "\n"
            "raise SystemExit(namespace['main']())\n"
        )
        return subprocess.run(
            [sys.executable, "-B", "-c", harness, *self.command()[2:]],
            input=prompt, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=self.root, env=self.env, timeout=10, check=False,
        )

    def test_stdin_read_failure_is_fixed_and_redacted(self):
        self.assert_rejected(self.invoke_harness("os.close(0)"), 74, READ_FAILED)

    def test_private_file_mode_even_with_permissive_umask(self):
        result = self.invoke_harness("os.umask(0)")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["mode"], 0o600)
        self.assert_clean()

    def test_real_file_write_failure_cleans_the_created_file(self):
        result = self.invoke_harness(
            "import resource\n"
            "signal.signal(signal.SIGXFSZ, signal.SIG_IGN)\n"
            "resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))"
        )
        self.assert_rejected(result, 74, FILE_FAILED)

    def test_provider_stdout_and_stderr_are_exact_bytes(self):
        self.write_fake(
            "import os\n"
            "os.write(1, b'provider stdout\\x00\\xff\\n')\n"
            "os.write(2, b'provider stderr\\xfe\\r\\n')\n"
        )
        result = self.invoke()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"provider stdout\x00\xff\n")
        self.assertEqual(result.stderr, b"provider stderr\xfe\r\n")
        self.assert_clean()

    def test_ordinary_provider_exit_status_and_cleanup(self):
        for status in (0, 1, 2, 42, 64, 70, 127, 255):
            with self.subTest(status=status):
                self.write_fake(f"import sys\nsys.exit({status})\n")
                result = self.invoke()
                self.assertEqual(result.returncode, status)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, b"")
                self.assert_clean()

    def test_provider_signal_cleans_file_and_preserves_output(self):
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
        self.assert_clean()

    def test_adapter_signals_kill_child_and_clean_prompt_file(self):
        self.assertTrue(ADAPTER.is_file(), "adapter implementation is absent")
        self.write_fake(
            "import json, os, signal, sys\n"
            "print(json.dumps({'pid': os.getpid(), 'path': sys.argv[2]}), flush=True)\n"
            "while True: signal.pause()\n"
        )
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(signum=signum):
                process = subprocess.Popen(
                    self.command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, cwd=self.root, env=self.env,
                )
                try:
                    process.stdin.write(b"public fixture")
                    process.stdin.close()
                    process.stdin = None
                    self.assertTrue(select.select([process.stdout], [], [], 5)[0])
                    report = json.loads(process.stdout.readline())
                    self.assertTrue(Path(report["path"]).is_file())
                    process.send_signal(signum)
                    stdout, stderr = process.communicate(timeout=5)
                    self.assertEqual(process.returncode, 71)
                    self.assertEqual(stdout, b"")
                    self.assertEqual(stderr, SIGNALLED)
                    self.assert_clean()
                    with self.assertRaises(ProcessLookupError):
                        os.kill(report["pid"], 0)
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=5)

    def test_invalid_executables_fail_without_shell_fallback_and_cleanup(self):
        for kind in ("missing", "nonexecutable", "directory", "bad_format"):
            with self.subTest(kind=kind):
                executable = self.fake
                if kind == "missing":
                    executable = self.root / "PRIVATE_MISSING_PATH"
                elif kind == "nonexecutable":
                    self.fake.chmod(0o600)
                elif kind == "directory":
                    executable = self.tmpdir
                else:
                    self.fake.chmod(0o700)
                    self.fake.write_text("touch injected_format\n", encoding="utf-8")
                result = self.invoke(arguments=["--grok-executable", str(executable)])
                self.assert_rejected(result, 70, LAUNCH_FAILED)
                self.assertFalse((self.root / "injected_format").exists())

    def test_cli_only_accepts_the_exact_absolute_executable_option(self):
        for arguments in (
            [], [str(self.fake)], ["--grok-executable"],
            ["--grok-executable", "grok"], ["--grok-executable", "./grok"],
            ["--grok-executable", ""], ["--PRIVATE_UNKNOWN", str(self.fake)],
            ["--grok-executable", str(self.fake), "--tools", "Bash"],
            ["--grok-executable=" + str(self.fake)], ["--help"],
        ):
            with self.subTest(argument_count=len(arguments)):
                self.assert_rejected(self.invoke(arguments=arguments), 64, USAGE)

    def test_hostile_tmpdir_does_not_fall_back_or_reveal_paths(self):
        for directory in ("", "relative", str(self.fake), str(self.root / "PRIVATE_MISSING")):
            with self.subTest(directory_kind=directory == ""):
                result = self.invoke(env=dict(self.env, TMPDIR=directory))
                self.assert_rejected(result, 74, FILE_FAILED)

    def test_provider_removing_prompt_file_is_already_clean(self):
        self.write_fake("import os, sys\nos.unlink(sys.argv[2])\n")
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_clean()

    def test_cleanup_does_not_follow_a_replacement_symlink(self):
        sentinel = self.root / "sentinel"
        sentinel.write_text("preserve", encoding="utf-8")
        self.write_fake(
            "import os, sys\n"
            "os.unlink(sys.argv[2])\n"
            f"os.symlink({str(sentinel)!r}, sys.argv[2])\n"
        )
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sentinel.read_text(), "preserve")
        self.assert_clean()


if __name__ == "__main__":
    unittest.main()
