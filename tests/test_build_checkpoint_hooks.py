"""Real Git boundaries: installation and commit hook cannot trust unstaged code."""
import os
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install-build-hooks.py"
HOOK = ROOT / ".githooks/pre-commit"

class HookTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.repo = Path(self.scratch.name)
        self.env = dict(os.environ)
        for key in list(self.env):
            if key.startswith("GIT_"):
                self.env.pop(key)
        self.env["GIT_CONFIG_NOSYSTEM"] = "1"
        self.env["GIT_CONFIG_GLOBAL"] = os.devnull
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.write("initial.txt", "baseline\n")
        self.git("add", "initial.txt")
        self.git("commit", "-qm", "baseline")

    def command(self, args):
        return subprocess.run(args, cwd=self.repo, env=self.env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def git(self, *args):
        result = self.command(["git", *args])
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def prepare(self, checker="raise SystemExit(0)\n"):
        self.assertTrue(HOOK.is_file(), "Repository pre-commit hook is missing")
        self.assertTrue(INSTALLER.is_file(), "Hook installer is missing")
        path = self.write(".githooks/pre-commit", HOOK.read_text())
        path.chmod(0o755)
        self.write("scripts/verify-build-checkpoint.py", checker)
        self.git("add", ".githooks/pre-commit", "scripts/verify-build-checkpoint.py")

    def install(self):
        return self.command(["python3", str(INSTALLER), "--repo", str(self.repo)])

    def test_installs_only_local_hook_path_and_is_idempotent(self):
        self.prepare()
        first = self.install()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(self.git("config", "--local", "--get", "core.hooksPath"), ".githooks")
        second = self.install()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertFalse((self.repo / "status-created-by-hook").exists())

    def test_refuses_to_replace_existing_custom_hook_configuration(self):
        self.prepare()
        self.git("config", "--local", "core.hooksPath", "other-hooks")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.git("config", "--local", "--get", "core.hooksPath"), "other-hooks")

    def test_refuses_to_hide_active_default_hooks(self):
        self.prepare()
        hook = self.write(".git/hooks/pre-commit", "#!/bin/sh\nexit 0\n")
        hook.chmod(0o755)
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(hook.read_text(), "#!/bin/sh\nexit 0\n")
        self.assertNotEqual(self.command(["git", "config", "--local", "--get", "core.hooksPath"]).returncode, 0)

    def test_requires_executable_hook_without_changing_it(self):
        self.prepare()
        hook = self.repo / ".githooks/pre-commit"
        hook.chmod(0o644)
        self.assertNotEqual(self.install().returncode, 0)
        self.assertFalse(os.access(hook, os.X_OK))

    def test_commit_uses_staged_checker_and_propagates_failure(self):
        self.prepare("print('FIXTURE: staged checker rejected commit')\nraise SystemExit(17)\n")
        self.assertEqual(self.install().returncode, 0)
        self.write("scripts/verify-build-checkpoint.py", "raise SystemExit(0)\n")
        before = self.git("rev-parse", "HEAD")
        result = self.command(["git", "commit", "-qm", "must reject"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("staged checker rejected", result.stdout + result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)

    def test_unstaged_broken_checker_cannot_break_valid_staged_checker(self):
        self.prepare("import sys\nassert sys.argv[1:] == ['--staged']\nraise SystemExit(0)\n")
        self.assertEqual(self.install().returncode, 0)
        self.write("scripts/verify-build-checkpoint.py", "raise SystemExit(29)\n")
        result = self.command(["git", "commit", "-qm", "accepted boundary"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("log", "-1", "--format=%s"), "accepted boundary")

    def test_replacement_object_cannot_substitute_for_staged_checker(self):
        self.prepare("print('FIXTURE: exact staged checker rejects')\nraise SystemExit(17)\n")
        self.assertEqual(self.install().returncode, 0)
        original = self.git("rev-parse", ":scripts/verify-build-checkpoint.py")
        self.write("replacement.py", "raise SystemExit(0)\n")
        replacement = self.git("hash-object", "-w", "replacement.py")
        self.git("replace", original, replacement)
        before = self.git("rev-parse", "HEAD")
        result = self.command(["git", "commit", "-qm", "must use exact blob"])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exact staged checker rejects", result.stdout + result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), before)

    def test_empty_index_allows_git_to_report_nothing_to_commit(self):
        self.prepare()
        self.git("commit", "-qm", "bootstrap")
        self.assertEqual(self.install().returncode, 0)
        self.write("scripts/verify-build-checkpoint.py", "raise SystemExit(29)\n")
        result = self.command(["sh", str(self.repo / ".githooks/pre-commit")])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_actual_hook_blocks_missing_checkpoint_then_commits_honest_progress(self):
        checker = ROOT / "scripts/verify-build-checkpoint.py"
        self.assertTrue(checker.is_file(), "Checkpoint validator is not implemented yet")
        self.prepare(checker.read_text())
        self.git("commit", "-qm", "bootstrap guard fixture")
        self.assertEqual(self.install().returncode, 0)
        base = self.git("rev-parse", "HEAD")
        self.write("apps/sample.py", "answer = 42\n")
        self.git("add", "apps/sample.py")
        rejected = self.command(["git", "commit", "-qm", "missing checkpoint"])
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(self.git("rev-parse", "HEAD"), base)
        block = {"schema_version": 1, "receipt": "docs/operations/build-checkpoint.json",
                 "task_id": "FIXTURE-1", "outcome": "in_progress",
                 "next_task_id": "FIXTURE-1", "next_action": "Continue the fixture implementation."}
        self.write("CURRENT_STATUS.md", "# Current Status\n\n"
                   "**Primary product task: FIXTURE-1.** Continue the fixture.\n\n"
                   "**Next executable action:** Continue the fixture implementation.\n\n"
                   "<!-- build-checkpoint:v1 -->\n```json\n" + json.dumps(block) +
                   "\n```\n<!-- /build-checkpoint -->\n")
        self.git("add", "CURRENT_STATUS.md")
        changes = [{"path": p, "sha256": hashlib.sha256((self.repo / p).read_bytes()).hexdigest()}
                   for p in ["CURRENT_STATUS.md", "apps/sample.py"]]
        receipt = {"schema_version": 1, "task_id": "FIXTURE-1", "outcome": "in_progress",
                   "base_commit": base, "summary": "Fixture implementation in progress.",
                   "remaining_limits": ["Fixture is not yet complete."],
                   "next_task_id": "FIXTURE-1", "next_action": block["next_action"],
                   "changed_files": changes, "checks": [], "review": None,
                   "missing_prerequisite": None, "independent_action": None}
        self.write("docs/operations/build-checkpoint.json", json.dumps(receipt))
        self.git("add", "docs/operations/build-checkpoint.json")
        accepted = self.command(["git", "commit", "-qm", "honest progress"])
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
        self.assertNotEqual(self.git("rev-parse", "HEAD"), base)

if __name__ == "__main__":
    unittest.main()
