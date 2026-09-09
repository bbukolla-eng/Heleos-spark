"""Black-box Cursor transport contracts; every child is a local fixture."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


# Reuse the causal OS-boundary tests, not production helpers or computed argv.
spec = importlib.util.spec_from_file_location(
    "cursor_transport_contracts", Path(__file__).with_name("test_codex_stdin.py")
)
contracts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contracts)
contracts.ADAPTER = Path(__file__).resolve().parents[2] / "scripts/provider-adapters/cursor-stdin.py"
for name in ("USAGE", "OVERSIZED", "INVALID_UTF8", "NUL_INPUT", "LAUNCH_FAILED", "SIGNALLED", "READ_FAILED"):
    setattr(contracts, name, getattr(contracts, name).replace(b"codex", b"cursor"))
contracts.USAGE = b"cursor-stdin: usage: --cursor-node ABSOLUTE_REGULAR_EXECUTABLE --cursor-entrypoint ABSOLUTE_REGULAR_FILE\n"


class CursorStdinTests(contracts.CodexStdinTests):
    def setUp(self):
        super().setUp()
        self.entrypoint = self.root / "index ; $(literal).js"
        self.entrypoint.write_text("// inert entrypoint fixture\n", encoding="utf-8")
        self.runtime_scratch = tempfile.TemporaryDirectory(prefix="cursor-runtime-", dir="/tmp")
        self.addCleanup(self.runtime_scratch.cleanup)
        self.runtime = Path(self.runtime_scratch.name).resolve()
        self.environment = patch.dict(os.environ, {"TMPDIR": str(self.runtime)})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def command(self, arguments=None):
        if arguments is None:
            arguments = ["--cursor-node", str(self.fake), "--cursor-entrypoint", str(self.entrypoint)]
        elif any("--codex-executable" in item for item in arguments):
            arguments = [item.replace("--codex-executable", "--cursor-node") for item in arguments]
            arguments += ["--cursor-entrypoint", str(self.entrypoint)]
        return super().command(arguments)

    def assert_prompt_passed(self, prompt):
        result = self.invoke(prompt.encode("utf-8"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(json.loads(result.stdout), {
            "argv": [str(self.fake), str(self.entrypoint), "--disable-project-configs",
                     "--exclude-workspace-context", "--print", "--force", "--sandbox", "enabled",
                     "--output-format", "stream-json", "--disable-auto-update",
                     "--model", "gpt-5.6-sol-high"],
            "cwd": str(self.root), "stdin": prompt,
        })
        self.assertEqual(set(self.root.iterdir()), {self.fake, self.entrypoint})

    # Catches added user-controlled bypass/root/plugin/model/endpoint arguments.
    def test_cursor_options_cannot_override_the_fixed_contract(self):
        for flag in ("--force", "--yolo", "--sandbox", "--approve-mcps", "--trust",
                     "--workspace", "--add-dir", "--plugin-dir", "--worktree",
                     "--api-key", "--header", "--endpoint", "--resume", "--continue",
                     "--model", "--output-format", "--disable-auto-update",
                     "--disable-project-configs=false", "--exclude-workspace-context=false"):
            with self.subTest(flag=flag):
                self.assert_rejected(self.invoke(arguments=[
                    "--cursor-node", str(self.fake), "--cursor-entrypoint", str(self.entrypoint), flag, "PRIVATE"
                ]), 64, contracts.USAGE)

    def test_node_and_entrypoint_reject_unsafe_paths_without_launch(self):
        link = self.root / "symlink"
        link.symlink_to(self.entrypoint)
        fifo = self.root / "pipe"
        os.mkfifo(fifo)
        for path in ("relative.js", str(self.runtime), str(link), str(fifo), str(self.root / "missing")):
            with self.subTest(path=path):
                self.assert_rejected(self.invoke(arguments=[
                    "--cursor-node", str(self.fake), "--cursor-entrypoint", path
                ]), 64, contracts.USAGE)
        self.entrypoint.chmod(0o666)
        self.assert_rejected(self.invoke(), 64, contracts.USAGE)

    # Catches runtime state writing into the authenticated HOME or an inherited cache.
    def test_runtime_directories_use_tmpdir_preserve_home_and_override_cache(self):
        # Apple Python's launcher itself writes HOME caches before our code.
        # Set HOME after interpreter startup and use a non-Python child, so
        # this test observes adapter writes rather than that upstream effect.
        self.fake.write_text("#!/bin/sh\nexec /usr/bin/env\n", encoding="utf-8")
        result = self.invoke_harness("os.environ.update(" + repr({
            "HOME": str(self.root / "auth-home"),
            "CURSOR_DATA_DIR": "/must-not-use-data",
            "CURSOR_CONFIG_DIR": str(self.root / "auth-home" / ".cursor"),
            "NODE_COMPILE_CACHE": "/must-not-use-cache",
        }) + ")")
        self.assertEqual(result.returncode, 0, result.stderr)
        environment = dict(line.split("=", 1) for line in result.stdout.decode().splitlines())
        self.assertEqual(environment["HOME"], str(self.root / "auth-home"))
        self.assertEqual(environment["CURSOR_DATA_DIR"], str(self.runtime))
        self.assertEqual(environment["CURSOR_CONFIG_DIR"], str(self.runtime / "cursor-config"))
        self.assertEqual(environment["NODE_COMPILE_CACHE"], str(self.runtime / "node-compile-cache"))
        self.assertEqual(set(self.runtime.iterdir()), {
            self.runtime / "node-compile-cache", self.runtime / "cursor-config"
        })
        self.assertFalse((self.root / "auth-home").exists())

    def test_unsafe_existing_config_targets_fail_before_provider_launch(self):
        for kind in ("symlink", "file", "writable_directory"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(prefix="cursor-config-", dir="/tmp") as directory:
                runtime = Path(directory).resolve()
                config = runtime / "cursor-config"
                if kind == "symlink":
                    config.symlink_to(self.root, target_is_directory=True)
                elif kind == "file":
                    config.write_bytes(b"preserve")
                else:
                    config.mkdir()
                    config.chmod(0o777)
                with patch.dict(os.environ, {"TMPDIR": str(runtime)}):
                    self.assert_rejected(self.invoke(), 64, b"cursor-stdin: runtime root unavailable\n")
                if kind == "file":
                    self.assertEqual(config.read_bytes(), b"preserve")
                elif kind == "symlink":
                    self.assertTrue(config.is_symlink())

    def test_child_config_write_stays_under_runtime_not_authenticated_home(self):
        self.fake.write_text(
            '#!/bin/sh\nprintf generated > "$CURSOR_CONFIG_DIR/cli-config.json"\n',
            encoding="utf-8",
        )
        result = self.invoke_harness("os.environ.update(" + repr({
            "HOME": str(self.root / "auth-home"),
            "CURSOR_CONFIG_DIR": str(self.root / "auth-home" / ".cursor"),
        }) + ")")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        self.assertEqual((self.runtime / "cursor-config" / "cli-config.json").read_bytes(), b"generated")
        self.assertFalse((self.root / "auth-home").exists())

    def test_home_fallback_and_invalid_runtime_roots(self):
        self.write_fake("import json, os\nprint(json.dumps(dict(os.environ)))\n")
        with patch.dict(os.environ, {"HOME": str(self.runtime)}):
            os.environ.pop("TMPDIR", None)
            result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["CURSOR_DATA_DIR"], str(self.runtime))
        for root in ("relative", "", str(self.root / "missing"), str(self.entrypoint)):
            with self.subTest(root=root), patch.dict(os.environ, {"TMPDIR": root}):
                self.assert_rejected(self.invoke(), 64, b"cursor-stdin: runtime root unavailable\n")

    def test_symlink_runtime_directory_cannot_redirect_writes(self):
        (self.runtime / "node-compile-cache").symlink_to(self.root, target_is_directory=True)
        self.assert_rejected(self.invoke(), 64, b"cursor-stdin: runtime root unavailable\n")

    # Installed Cursor falls back to /tmp/.cursor when its data root exceeds 84
    # and the joined projects directory exceeds 84; cap the root at 75.
    def test_runtime_root_boundary_prevents_cursor_global_tmp_fallback(self):
        self.write_fake("import json, os\nprint(json.dumps(dict(os.environ)))\n")
        for length in (76, 75):
            path = self.runtime / ("r" * (length - len(str(self.runtime)) - 1))
            path.mkdir()
            self.assertEqual(len(str(path)), length)
            with patch.dict(os.environ, {"TMPDIR": str(path)}):
                result = self.invoke()
            if length == 75:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["CURSOR_DATA_DIR"], str(path))
            else:
                self.assert_rejected(result, 64, b"cursor-stdin: runtime root unavailable\n")
                self.assertEqual(list(path.iterdir()), [])

    def test_runtime_root_rejects_symlink_and_non_ascii(self):
        link = self.runtime / "link"
        link.symlink_to(self.runtime, target_is_directory=True)
        unicode_root = self.runtime / "\u00e9"
        unicode_root.mkdir()
        for root in (link, str(link) + "/", unicode_root):
            with self.subTest(root=root), patch.dict(os.environ, {"TMPDIR": str(root)}):
                self.assert_rejected(self.invoke(), 64, b"cursor-stdin: runtime root unavailable\n")


if __name__ == "__main__":
    unittest.main()
