"""Black-box Cursor transport contracts; every child is a local fixture."""

import importlib.util
import json
from pathlib import Path
import unittest


# Reuse the causal OS-boundary tests, not production helpers or computed argv.
spec = importlib.util.spec_from_file_location(
    "cursor_transport_contracts", Path(__file__).with_name("test_codex_stdin.py")
)
contracts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contracts)
contracts.ADAPTER = Path(__file__).resolve().parents[2] / "scripts/provider-adapters/cursor-stdin.py"
for name in ("USAGE", "OVERSIZED", "INVALID_UTF8", "NUL_INPUT", "LAUNCH_FAILED", "SIGNALLED", "READ_FAILED"):
    setattr(contracts, name, getattr(contracts, name).replace(b"codex", b"cursor"))


class CursorStdinTests(contracts.CodexStdinTests):
    def command(self, arguments=None):
        if arguments is None:
            arguments = ["--cursor-executable", str(self.fake)]
        else:
            arguments = [item.replace("--codex-executable", "--cursor-executable") for item in arguments]
        return super().command(arguments)

    def assert_prompt_passed(self, prompt):
        result = self.invoke(prompt.encode("utf-8"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(json.loads(result.stdout), {
            "argv": [str(self.fake), "--print", "--force", "--sandbox", "enabled",
                     "--output-format", "stream-json", "--disable-auto-update",
                     "--model", "gpt-5.6-sol-high"],
            "cwd": str(self.root), "stdin": prompt,
        })
        self.assertEqual(set(self.root.iterdir()), {self.fake})

    # Catches added user-controlled bypass/root/plugin/model/endpoint arguments.
    def test_cursor_options_cannot_override_the_fixed_contract(self):
        for flag in ("--force", "--yolo", "--sandbox", "--approve-mcps", "--trust",
                     "--workspace", "--add-dir", "--plugin-dir", "--worktree",
                     "--api-key", "--header", "--endpoint", "--resume", "--continue",
                     "--model", "--output-format", "--disable-auto-update"):
            with self.subTest(flag=flag):
                self.assert_rejected(self.invoke(arguments=[
                    "--cursor-executable", str(self.fake), flag, "PRIVATE"
                ]), 64, contracts.USAGE)


if __name__ == "__main__":
    unittest.main()
