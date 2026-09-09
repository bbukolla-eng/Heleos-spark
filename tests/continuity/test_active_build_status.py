"""Black-box active-build discovery against real disposable Git worktrees."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "active-build-status.py"


class ActiveBuildStatusTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="heleos-active-build-")
        self.addCleanup(self.scratch.cleanup)
        self.base = Path(self.scratch.name).resolve()
        self.root = self.base / "visible repo"
        self.root.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update({
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "GIT_AUTHOR_DATE": "2026-09-09T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-09-09T00:00:00Z",
        })
        self.git("init", "--initial-branch=main")
        self.git("config", "core.autocrlf", "false")
        (self.root / "tracked.txt").write_text("original\n")
        (self.root / ".gitignore").write_text(".worktrees/\nignored.log\n")
        self.commit("initial")
        self.active = self.root / ".worktrees" / "build with spaces"
        self.git("worktree", "add", "-b", "build/active", str(self.active))
        (self.active / "feature.txt").write_text("feature\n")
        self.commit("feature", cwd=self.active)
        self.active_head = self.git("rev-parse", "HEAD", cwd=self.active).strip()
        self.entries = [{"path": ".worktrees/build with spaces", "branch": "build/active",
                         "checkpoint": self.active_head}]
        self.authority(self.entries)

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or self.root), *args],
                              env=self.env, check=True, capture_output=True, text=True).stdout

    def commit(self, message, cwd=None):
        self.git("add", ".", cwd=cwd)
        self.git("commit", "-m", message, cwd=cwd)

    def authority(self, entries, prefix="", suffix=""):
        self.authority_text = (prefix + "# Current status\n<!-- active-build-authority:v1 -->\n```json\n"
                               + json.dumps({"schema_version": 1, "active_builds": entries})
                               + "\n```\n<!-- /active-build-authority -->\n" + suffix)
        (self.root / "CURRENT_STATUS.md").write_text(self.authority_text)
        self.commit("authority")

    def probe(self, *args, cwd=None, env=None):
        self.assertTrue(SCRIPT.is_file(), "active-build status entry point is missing")
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), *args],
                                cwd=cwd or self.root, env=env or self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.stderr, "")
        return result.returncode, json.loads(result.stdout)

    def hashes(self):
        return {str(p.relative_to(self.base)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.base.rglob("*") if p.is_file()}

    def test_any_checkout_discovers_main_authority_and_exact_live_build_without_writes(self):
        # Catches choosing the caller's stale CURRENT_STATUS copy or main as the build.
        (self.active / "CURRENT_STATUS.md").write_text("obsolete local copy\n")
        (self.active / "tracked.txt").write_text("changed\n")
        (self.active / "ignored.log").write_text("ignored\n")
        before = self.hashes()
        code, report = self.probe()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["authority"]["path"], str(self.root / "CURRENT_STATUS.md"))
        self.assertEqual(report["authority"]["sha256"], hashlib.sha256(self.authority_text.encode()).hexdigest())
        build = report["active_builds"][0]
        self.assertEqual(build["checkout_root"], str(self.active))
        self.assertEqual(build["branch"], "build/active")
        self.assertEqual(build["head"], self.active_head)
        self.assertEqual(build["divergence"], {"ahead_of_main": 1, "behind_main": 1})
        self.assertFalse(build["clean"])
        self.assertEqual(build["tracked_changes"], [{"path": "tracked.txt", "status": " M"}])
        self.assertEqual(build["untracked_paths"], ["CURRENT_STATUS.md"])
        self.assertEqual(self.probe(cwd=self.active)[1], report)
        self.assertEqual(self.probe()[1], report)
        self.assertEqual(before, self.hashes(), "status command mutated checkout or Git metadata")

    def test_multiple_active_builds_are_sorted_and_only_explicit_entries_are_active(self):
        other = self.root / ".worktrees" / "another"
        self.git("worktree", "add", "-b", "build/other", str(other))
        other_head = self.git("rev-parse", "HEAD", cwd=other).strip()
        self.authority(self.entries + [{"path": ".worktrees/another", "branch": "build/other",
                                       "checkpoint": other_head}])
        self.git("worktree", "add", "-b", "build/unlisted", str(self.base / "unlisted"))
        code, report = self.probe(cwd=other)
        self.assertEqual(code, 0, report)
        self.assertEqual([b["checkout_root"] for b in report["active_builds"]],
                         [str(other), str(self.active)])

    def test_empty_authority_explicitly_reports_no_active_builds(self):
        self.authority([])
        code, report = self.probe(cwd=self.active)
        self.assertEqual(code, 0, report)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["active_builds"], [])

        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "--human"], cwd=self.active,
            env=self.env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No active builds declared.", result.stdout)
        self.assertNotIn("Active: ", result.stdout)

    def test_descendant_of_checkpoint_reports_current_head(self):
        (self.active / "new.txt").write_text("new\n")
        self.commit("advanced", cwd=self.active)
        code, report = self.probe()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["active_builds"][0]["head"], self.git("rev-parse", "HEAD", cwd=self.active).strip())
        self.assertEqual(report["active_builds"][0]["checkpoint"], self.active_head)

    def test_stale_checkpoint_is_rejected_with_live_identity_retained(self):
        unrelated = self.root / ".worktrees" / "unrelated checkpoint"
        self.git("worktree", "add", "-b", "build/unrelated", str(unrelated))
        (unrelated / "unrelated.txt").write_text("unrelated\n")
        self.commit("unrelated", cwd=unrelated)
        wrong_checkpoint = self.git("rev-parse", "HEAD", cwd=unrelated).strip()
        self.authority([{**self.entries[0], "checkpoint": wrong_checkpoint}])
        code, report = self.probe()
        self.assertEqual(code, 1)
        self.assertEqual(report["errors"][0]["code"], "stale_authority")
        self.assertEqual(report["active_builds"][0]["head"], self.active_head)

    def test_changed_branch_is_rejected_even_when_head_matches(self):
        self.git("switch", "-c", "wrong", cwd=self.active)
        code, report = self.probe()
        self.assertEqual(code, 1)
        self.assertEqual(report["errors"][0]["code"], "stale_authority")

    def test_missing_or_replaced_registration_is_not_inferred_from_directory_name(self):
        self.git("worktree", "remove", str(self.active))
        self.active.mkdir()
        self.git("init", "--initial-branch=build/active", cwd=self.active)
        code, report = self.probe()
        self.assertEqual(code, 1)
        self.assertEqual(report["errors"][0]["code"], "stale_authority")

    def test_duplicate_entries_or_blocks_are_ambiguous(self):
        self.authority(self.entries * 2)
        code, report = self.probe()
        self.assertEqual(code, 1)
        self.assertEqual(report["errors"][0]["code"], "ambiguous_authority")
        self.authority(self.entries, suffix=self.authority_text)
        code, report = self.probe()
        self.assertEqual(code, 1)
        self.assertEqual(report["errors"][0]["code"], "ambiguous_authority")

    def test_missing_or_uncommitted_authority_fails_closed(self):
        (self.root / "CURRENT_STATUS.md").write_text("changed\n")
        code, report = self.probe()
        self.assertEqual(code, 1)
        self.assertEqual(report["errors"][0]["code"], "uncommitted_authority")
        self.commit("remove machine authority")
        code, report = self.probe()
        self.assertEqual(code, 1)
        self.assertEqual(report["errors"][0]["code"], "missing_authority")

    def test_no_checked_out_main_fails_without_using_caller_status(self):
        self.git("switch", "--detach")
        code, report = self.probe(cwd=self.active)
        self.assertEqual(code, 1)
        self.assertEqual(report["errors"][0]["code"], "main_checkout_unavailable")

    def test_malformed_records_fail_as_json(self):
        for entry in ({**self.entries[0], "checkpoint": "HEAD"},
                      {**self.entries[0], "path": "../elsewhere"},
                      {**self.entries[0], "unexpected": True}):
            with self.subTest(entry=entry):
                self.authority([entry])
                code, report = self.probe()
                self.assertEqual(code, 1)
                self.assertEqual(report["errors"][0]["code"], "invalid_authority")

    def test_human_mode_contains_paths_and_full_identity(self):
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--human"], cwd=self.active,
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for value in (str(self.active), str(self.root / "CURRENT_STATUS.md"), self.active_head,
                      "build/active", "clean", "ahead=1", "behind=1"):
            self.assertIn(value, result.stdout)

    def test_git_routing_override_is_rejected(self):
        code, report = self.probe(env=dict(self.env, GIT_DIR=str(self.root / ".git")))
        self.assertEqual(code, 1)
        self.assertEqual(report["errors"][0]["code"], "git_environment_override")


if __name__ == "__main__":
    unittest.main()
