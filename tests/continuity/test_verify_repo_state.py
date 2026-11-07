"""Real-Git checks for the read-only repository continuity guard."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify-repo-state.py"


class RepoStateTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="heleos-repo-state-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve() / "repo with spaces & literal"
        self.root.mkdir()
        self.env = dict(os.environ)
        for name in list(self.env):
            if name.startswith("GIT_"):
                del self.env[name]
        self.env.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Continuity Test",
            "GIT_AUTHOR_EMAIL": "continuity@example.invalid",
            "GIT_COMMITTER_NAME": "Continuity Test",
            "GIT_COMMITTER_EMAIL": "continuity@example.invalid",
            "GIT_AUTHOR_DATE": "2026-09-09T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-09-09T00:00:00Z",
        })
        self.git("init", "--initial-branch=main")
        self.git("config", "core.autocrlf", "false")
        (self.root / "tracked.txt").write_text("original\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
        self.git("add", "tracked.txt", ".gitignore")
        self.git("commit", "-m", "initial fixture")
        self.initial = self.git("rev-parse", "HEAD").strip()

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", "-C", str(cwd or self.root), *args],
            env=self.env, check=True, capture_output=True, text=True,
        ).stdout

    def probe(self, *args, repo=None, env=None):
        self.assertTrue(SCRIPT.is_file(), "repository-state entry point is missing")
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "--repo", str(repo or self.root), *args],
            env=env or self.env, capture_output=True, text=True,
        )
        self.assertEqual(result.stderr, "", result.stderr)
        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.fail(f"Expected one JSON document, got {result.stdout!r}")
        return result.returncode, report

    def file_hashes(self):
        return {
            str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.root.rglob("*") if path.is_file()
        }

    def test_clean_nested_checkout_reports_identity_without_writes(self):
        nested = self.root / "nested"
        nested.mkdir()
        before = self.file_hashes()
        code, report = self.probe(
            "--expect-branch", "main", "--expect-head", self.initial,
            "--require-clean", repo=nested,
        )
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["checkout_root"], str(self.root))
        self.assertEqual(report["git_common_dir"], str(self.root / ".git"))
        self.assertEqual(report["branch"], "main")
        self.assertEqual(report["head"], self.initial)
        self.assertEqual(report["main_head"], self.initial)
        self.assertEqual(report["divergence"], {"ahead_of_main": 0, "behind_main": 0})
        self.assertEqual(report["worktree_paths"], [str(self.root)])
        self.assertFalse(report["tracked_dirty"])
        self.assertFalse(report["untracked_dirty"])
        self.assertTrue(report["clean"])
        self.assertEqual(before, self.file_hashes(), "probe changed source or Git metadata bytes")
        self.assertEqual(self.probe(repo=nested)[1], report, "unchanged state must produce stable JSON")

    def test_linked_worktree_reports_shared_git_dir_and_true_divergence(self):
        self.git("branch", "feature")
        (self.root / "main-only.txt").write_text("main\n", encoding="utf-8")
        self.git("add", "main-only.txt")
        self.git("commit", "-m", "main advances")
        main_head = self.git("rev-parse", "HEAD").strip()
        linked = Path(self.scratch.name).resolve() / "linked worktree"
        self.git("worktree", "add", str(linked), "feature")
        (linked / "feature-only.txt").write_text("feature\n", encoding="utf-8")
        self.git("add", "feature-only.txt", cwd=linked)
        self.git("commit", "-m", "feature advances", cwd=linked)
        feature_head = self.git("rev-parse", "HEAD", cwd=linked).strip()
        code, report = self.probe("--require-clean", repo=linked)
        self.assertEqual(code, 0)
        self.assertEqual(report["checkout_root"], str(linked))
        self.assertEqual(report["git_common_dir"], str(self.root / ".git"))
        self.assertEqual(report["branch"], "feature")
        self.assertEqual(report["head"], feature_head)
        self.assertEqual(report["main_head"], main_head)
        self.assertEqual(report["divergence"], {"ahead_of_main": 1, "behind_main": 1})
        self.assertEqual(sorted(report["worktree_paths"]), sorted([str(self.root), str(linked)]))

    def test_dirtiness_distinguishes_tracked_untracked_and_ignored(self):
        (self.root / "tracked.txt").write_text("changed\n", encoding="utf-8")
        (self.root / "untracked name.txt").write_text("new\n", encoding="utf-8")
        (self.root / "ignored.log").write_text("ignored\n", encoding="utf-8")
        before = self.file_hashes()
        code, report = self.probe()
        self.assertEqual(code, 0, "observation without require-clean should report dirty state")
        self.assertTrue(report["tracked_dirty"])
        self.assertTrue(report["untracked_dirty"])
        self.assertFalse(report["clean"])
        self.assertEqual(report["tracked_changes"], [{"status": " M", "path": "tracked.txt"}])
        self.assertEqual(report["untracked_paths"], ["untracked name.txt"])
        code, rejected = self.probe("--require-clean")
        self.assertEqual(code, 1)
        self.assertEqual(rejected["errors"][0]["code"], "dirty_checkout")
        self.assertEqual(before, self.file_hashes())

    def test_staged_rename_preserves_both_literal_paths(self):
        self.git("mv", "tracked.txt", "renamed name.txt")
        code, report = self.probe()
        self.assertEqual(code, 0)
        self.assertEqual(report["tracked_changes"], [{
            "status": "R ", "path": "renamed name.txt", "original_path": "tracked.txt",
        }])

    def test_wrong_branch_and_head_fail_with_actual_state_retained(self):
        code, report = self.probe("--expect-branch", "feature", "--expect-head", "0" * 40)
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["head"], self.initial)
        self.assertEqual(report["branch"], "main")
        self.assertEqual([error["code"] for error in report["errors"]], ["branch_mismatch", "head_mismatch"])

    def test_abbreviated_or_revision_expression_head_is_not_exact_evidence(self):
        for expected in (self.initial[:12], "HEAD", "HEAD~1"):
            with self.subTest(expected=expected):
                code, report = self.probe("--expect-head", expected)
                self.assertEqual(code, 1)
                self.assertEqual(report["errors"][0]["code"], "invalid_expected_head")

    def test_detached_head_is_visible_and_cannot_satisfy_expected_branch(self):
        self.git("switch", "--detach", self.initial)
        code, report = self.probe()
        self.assertEqual(code, 0)
        self.assertIsNone(report["branch"])
        code, report = self.probe("--expect-branch", "main")
        self.assertEqual(code, 1)
        self.assertEqual(report["errors"][0]["code"], "branch_mismatch")

    def test_missing_local_main_is_incomplete_evidence(self):
        self.git("branch", "-m", "different")
        code, report = self.probe()
        self.assertEqual(code, 1)
        self.assertEqual(report["head"], self.initial)
        self.assertIsNone(report["main_head"])
        self.assertIsNone(report["divergence"])
        self.assertEqual(report["errors"][0]["code"], "main_unavailable")

    def test_nonrepository_and_bad_arguments_fail_as_json(self):
        code, report = self.probe(repo=Path(self.scratch.name))
        self.assertEqual(code, 1)
        self.assertEqual(report["errors"][0]["code"], "git_failed")
        code, report = self.probe("--unexpected")
        self.assertEqual(code, 1)
        self.assertEqual(report["errors"][0]["code"], "invalid_arguments")

    def test_git_routing_override_cannot_silently_change_selected_repository(self):
        override = dict(self.env, GIT_DIR=str(self.root / ".git"))
        code, report = self.probe(env=override)
        self.assertEqual(code, 1)
        self.assertEqual(report["errors"][0]["code"], "git_environment_override")


if __name__ == "__main__":
    unittest.main()
