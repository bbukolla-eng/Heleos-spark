"""Retired Kimi entrypoint cannot read a prompt, probe state or launch a provider."""
import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

ADAPTER = Path(__file__).resolve().parents[2] / "scripts/provider-adapters/kimi-stdin.py"
DIAGNOSTIC = "kimi-stdin: provider retired by owner; dispatch disabled\n"


class KimiRetirementTests(unittest.TestCase):
    def test_all_legacy_routes_refuse_before_reading_stdin(self):
        # Leave stdin open and empty: an attempted prompt read would hang.
        for args in ([], ["--kimi-executable", "/unavailable/kimi"],
                     ["--kimi-executable", "/unavailable/kimi", "--probe-state-layout"],
                     ["--kimi-executable", "/unavailable/kimi", "--worker-state-root", "/unavailable/state"]):
            with self.subTest(args=args):
                process = subprocess.Popen([sys.executable, "-B", str(ADAPTER), *args],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    process.wait(timeout=3)
                    self.assertEqual(process.returncode, 78)
                    self.assertEqual(process.stdout.read(), b"")
                    self.assertEqual(process.stderr.read().decode(), DIAGNOSTIC)
                finally:
                    if process.poll() is None:
                        process.kill(); process.wait()
                    for stream in (process.stdin, process.stdout, process.stderr):
                        stream.close()

    def test_refusal_opens_no_files_and_launches_no_subprocess(self):
        spec = importlib.util.spec_from_file_location("retired_kimi", ADAPTER)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        out = io.StringIO()
        with mock.patch("builtins.open", side_effect=AssertionError("file read")), \
             mock.patch("subprocess.Popen", side_effect=AssertionError("launch")), \
             mock.patch.object(sys, "stdin", None), contextlib.redirect_stderr(out):
            self.assertEqual(module.main(), 78)
        self.assertEqual(out.getvalue(), DIAGNOSTIC)


if __name__ == "__main__":
    unittest.main()
