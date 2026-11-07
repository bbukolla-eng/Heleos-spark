"""Exercise checkpoint range selection with real Git graphs and commit trees."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.ci import check_build_checkpoints as ci


class CheckpointCITests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name) / "repo"
        self.root.mkdir()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Checkpoint Test")
        self.git("config", "user.email", "checkpoint@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.write("shared.txt", "baseline\n")
        self.baseline = self.commit("historical baseline", valid=False)
        # This subprocess fixture tests only the adapter boundary. The real
        # validator's staged/committed contracts have their own test suite.
        self.validator = Path(self.scratch.name) / "validator.py"
        self.validator.write_text('''import argparse, json, pathlib, subprocess, sys
p = argparse.ArgumentParser()
p.add_argument("--repo", required=True)
p.add_argument("--commit", required=True)
a = p.parse_args()
git = ["git", "--no-replace-objects", "-C", a.repo]
value = subprocess.run(git + ["show", a.commit + ":valid.txt"], capture_output=True, text=True)
with (pathlib.Path(a.repo) / ".git" / "checkpoint-calls.jsonl").open("a") as out:
    out.write(json.dumps(a.commit) + "\\n")
if value.returncode or value.stdout != "good\\n":
    print("test validator: missing checkpoint for " + a.commit, file=sys.stderr)
    sys.exit(1)
''', encoding="utf-8")

    def git(self, *args, check=True):
        result = subprocess.run(["git", "-C", str(self.root), *args],
                                capture_output=True, text=True)
        if check and result.returncode:
            self.fail(f"git {args}: {result.stderr}")
        return result

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self, message, valid=True):
        self.write("valid.txt", "good\n" if valid else "bad\n")
        self.git("add", "-A")
        self.git("commit", "--allow-empty", "-qm", message)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def run_range(self, base, head):
        return ci.validate_range(self.root, base, head, baseline=self.baseline,
                                 validator_path=self.validator)

    def calls(self):
        path = self.root / ".git" / "checkpoint-calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def real_checkpoint(self, index):
        """Author an honest progress receipt, then let the real guard judge it."""
        base = self.git("rev-parse", "HEAD").stdout.strip()
        task = "CI-INTEGRATION-1"
        action = f"Implement bounded deliverable {index + 1}."
        receipt_path = "docs/operations/build-checkpoint.json"
        marker = {"schema_version": 1, "receipt": receipt_path, "task_id": task,
                  "outcome": "in_progress", "next_task_id": task, "next_action": action}
        self.write("scripts/deliverable.py", f"value = {index}\n")
        self.write("CURRENT_STATUS.md", f"# Current status\n\n**Primary product task: {task}.**\n\n"
                   f"**Next executable action:** {action}\n\n<!-- build-checkpoint:v1 -->\n```json\n"
                   + json.dumps(marker) + "\n```\n<!-- /build-checkpoint -->\n")
        self.git("add", "-A")
        paths = self.git("diff", "--cached", "--name-only", "-z", base).stdout.split("\0")
        manifest = [{"path": path, "sha256": hashlib.sha256(
            subprocess.run(["git", "-C", str(self.root), "show", ":" + path],
                           capture_output=True, check=True).stdout).hexdigest()}
                    for path in paths if path and path != receipt_path]
        receipt = {"schema_version": 1, "task_id": task, "outcome": "in_progress",
                   "base_commit": base, "summary": f"Bounded progress {index}.",
                   "remaining_limits": ["Independent final acceptance remains open."],
                   "next_task_id": task, "next_action": action, "changed_files": manifest,
                   "checks": [], "review": None, "missing_prerequisite": None,
                   "independent_action": None}
        self.write(receipt_path, json.dumps(receipt) + "\n")
        self.git("add", receipt_path)
        self.git("commit", "-qm", f"bounded progress {index}")
        return self.git("rev-parse", "HEAD").stdout.strip()

    def test_real_validator_accepts_each_committed_receipt_despite_dirty_files(self):
        one = self.real_checkpoint(1)
        two = self.real_checkpoint(2)
        self.write("CURRENT_STATUS.md", "uncommitted unrelated text\n")
        result = ci.validate_range(self.root, self.baseline, two, baseline=self.baseline)
        self.assertEqual(result.checked, [one, two])
        self.assertEqual((self.root / "CURRENT_STATUS.md").read_text(), "uncommitted unrelated text\n")

    def test_real_validator_does_not_let_later_receipt_cover_earlier_missing_receipt(self):
        self.write("scripts/deliverable.py", "unrecorded = True\n")
        bad = self.commit("unrecorded protected change")
        good = self.real_checkpoint(2)
        with self.assertRaisesRegex(ci.CheckpointError, bad):
            ci.validate_range(self.root, self.baseline, good, baseline=self.baseline)

    def test_all_new_commits_are_checked_in_parent_order(self):
        one = self.commit("one")
        self.write("later.txt", "two\n")
        two = self.commit("two")
        result = self.run_range(self.baseline, two)
        self.assertEqual(result.checked, [one, two])
        self.assertEqual(self.calls(), [one, two])

    def test_later_good_checkpoint_cannot_hide_earlier_failure(self):
        bad = self.commit("bad new commit", valid=False)
        good = self.commit("later valid commit")
        with self.assertRaisesRegex(ci.CheckpointError, bad):
            self.run_range(self.baseline, good)
        self.assertEqual(self.calls(), [bad])

    def test_pre_adoption_history_is_not_retroactively_rejected(self):
        old = self.git("rev-parse", self.baseline).stdout.strip()
        result = self.run_range(old, self.baseline)
        self.assertEqual(result.checked, [])
        self.assertEqual(self.calls(), [])

    def test_only_ancestors_of_baseline_are_historical(self):
        self.git("checkout", "-qb", "older-branch", self.baseline)
        side = self.commit("post baseline branch", valid=False)
        self.git("checkout", "main")
        new_baseline = self.commit("new baseline")
        with self.assertRaisesRegex(ci.CheckpointError, "adoption baseline"):
            ci.validate_range(self.root, self.baseline, side,
                              baseline=new_baseline, validator_path=self.validator)
        self.assertEqual(self.calls(), [])

    def test_dirty_worktree_cannot_repair_bad_committed_input(self):
        bad = self.commit("invalid", valid=False)
        self.write("valid.txt", "good\n")
        with self.assertRaises(ci.CheckpointError):
            self.run_range(self.baseline, bad)
        self.assertEqual((self.root / "valid.txt").read_text(), "good\n")
        self.assertEqual(self.git("status", "--porcelain").stdout, " M valid.txt\n")

    def test_guard_deletion_does_not_skip_commit(self):
        self.write("scripts/verify-build-checkpoint.py", "guard marker\n")
        one = self.commit("add guard")
        (self.root / "scripts/verify-build-checkpoint.py").unlink()
        two = self.commit("delete guard", valid=False)
        with self.assertRaisesRegex(ci.CheckpointError, two):
            self.run_range(self.baseline, two)
        self.assertEqual(self.calls(), [one, two])

    def test_new_branch_zero_before_uses_baseline(self):
        one = self.commit("new branch commit")
        self.assertEqual(self.run_range("0" * 40, one).checked, [one])

    def test_zero_head_and_missing_or_invalid_objects_fail(self):
        one = self.commit("valid")
        cases = [(self.baseline, "0" * 40), ("a" * 40, one),
                 (self.baseline, "b" * 40), ("HEAD", one),
                 (self.baseline, "$(touch unsafe)"), (None, one)]
        for base, head in cases:
            with self.subTest(base=base, head=head), self.assertRaises(ci.CheckpointError):
                self.run_range(base, head)
        self.assertEqual(self.calls(), [])
        self.assertFalse((self.root / "unsafe").exists())

    def test_missing_baseline_is_a_history_error(self):
        one = self.commit("valid")
        with self.assertRaisesRegex(ci.CheckpointError, "baseline"):
            ci.validate_range(self.root, self.baseline, one, baseline="a" * 40,
                              validator_path=self.validator)

    def test_zero_before_unrelated_root_fails_explicitly(self):
        self.git("checkout", "--orphan", "unrelated")
        self.git("rm", "-rf", ".")
        other = self.commit("unrelated root")
        with self.assertRaisesRegex(ci.CheckpointError, "adoption baseline"):
            self.run_range("0" * 40, other)

    def test_merge_checks_branch_commits_and_reuses_clean_merge(self):
        self.git("checkout", "-qb", "side")
        self.write("side.txt", "side\n")
        side = self.commit("side")
        self.git("checkout", "main")
        self.write("main.txt", "main\n")
        main = self.commit("main")
        self.git("merge", "--no-ff", "side", "-m", "automatic merge")
        merge = self.git("rev-parse", "HEAD").stdout.strip()
        result = self.run_range(self.baseline, merge)
        self.assertEqual(set(result.checked), {side, main})
        self.assertEqual(result.reused_merges, [merge])
        self.assertEqual(set(self.calls()), {side, main})

    def test_bad_branch_commit_is_not_hidden_by_clean_merge(self):
        self.git("checkout", "-qb", "side")
        self.write("side.txt", "side\n")
        bad = self.commit("invalid side", valid=False)
        self.git("checkout", "main")
        self.write("main.txt", "main\n")
        self.commit("main", valid=False)
        self.git("merge", "--no-ff", "side", "-m", "automatic merge")
        merge = self.git("rev-parse", "HEAD").stdout.strip()
        with self.assertRaises(ci.CheckpointError):
            self.run_range(self.baseline, merge)
        self.assertNotIn(merge, self.calls())

    def test_novel_conflict_resolution_requires_its_own_checkpoint(self):
        self.git("checkout", "-qb", "side")
        self.write("shared.txt", "side\n")
        side = self.commit("side")
        self.git("checkout", "main")
        self.write("shared.txt", "main\n")
        main = self.commit("main")
        self.assertNotEqual(self.git("merge", "side", check=False).returncode, 0)
        self.write("shared.txt", "resolved differently\n")
        merge = self.commit("manual merge", valid=False)
        with self.assertRaisesRegex(ci.CheckpointError, merge):
            self.run_range(self.baseline, merge)
        self.assertEqual(set(self.calls()[:-1]), {side, main})
        self.assertEqual(self.calls()[-1], merge)
        self.assertEqual(self.git("status", "--porcelain").stdout, "")

    def test_manual_change_in_otherwise_clean_merge_is_validated(self):
        self.git("checkout", "-qb", "side")
        self.write("side.txt", "side\n")
        side = self.commit("side")
        self.git("checkout", "main")
        self.write("main.txt", "main\n")
        main = self.commit("main")
        self.git("merge", "--no-ff", "--no-commit", "side")
        self.write("novel.txt", "not present in either parent\n")
        merge = self.commit("merge with additional reviewed work")
        result = self.run_range(self.baseline, merge)
        self.assertEqual(set(result.checked), {side, main, merge})
        self.assertEqual(self.calls()[-1], merge)
        self.assertEqual(result.reused_merges, [])

    def test_octopus_merge_fails_instead_of_accepting_empty_remerge_output(self):
        sides = []
        for index in range(2):
            branch = f"side{index}"
            self.git("checkout", "-qb", branch, self.baseline)
            self.write(branch + ".txt", branch + "\n")
            sides.append(self.commit(branch))
        self.git("checkout", "main")
        self.write("main.txt", "main\n")
        main = self.commit("main")
        self.git("merge", "--no-ff", "side0", "side1", "-m", "octopus")
        merge = self.git("rev-parse", "HEAD").stdout.strip()
        with self.assertRaisesRegex(ci.CheckpointError, "Octopus merge"):
            self.run_range(self.baseline, merge)
        self.assertEqual(set(self.calls()), {*sides, main})

    def test_remerge_diff_failure_is_not_treated_as_clean(self):
        self.git("checkout", "-qb", "side")
        self.write("side.txt", "side\n")
        self.commit("side")
        self.git("checkout", "main")
        self.write("main.txt", "main\n")
        self.commit("main")
        self.git("merge", "--no-ff", "side", "-m", "automatic merge")
        merge = self.git("rev-parse", "HEAD").stdout.strip()
        real_git = ci.git

        def fail_remerge(repo, *args):
            if "--remerge-diff" in args:
                raise ci.CheckpointError("remerge unsupported")
            return real_git(repo, *args)

        with patch.object(ci, "git", side_effect=fail_remerge):
            with self.assertRaisesRegex(ci.CheckpointError, "remerge unsupported"):
                self.run_range(self.baseline, merge)


class EventSelectionTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.path = Path(self.scratch.name) / "event.json"

    def event(self, name, payload):
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        return ci.event_range(name, self.path)

    def test_pr_uses_payload_head_and_base_not_synthetic_merge(self):
        with patch.dict(os.environ, {"GITHUB_SHA": "c" * 40}):
            self.assertEqual(self.event("pull_request", {"pull_request": {
                "base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}}),
                ("a" * 40, "b" * 40))

    def test_push_uses_before_and_after(self):
        self.assertEqual(self.event("push", {"before": "a" * 40, "after": "b" * 40}),
                         ("a" * 40, "b" * 40))

    def test_missing_or_wrong_event_fields_do_not_succeed(self):
        for name, payload in [("push", {}), ("pull_request", {}),
                              ("push", []), ("pull_request_target", {})]:
            with self.subTest(name=name), self.assertRaises(ci.CheckpointError):
                self.event(name, payload)
        self.path.write_text("not json", encoding="utf-8")
        with self.assertRaises(ci.CheckpointError):
            ci.event_range("push", self.path)
        with self.assertRaises(ci.CheckpointError):
            ci.event_range("push", self.path.with_name("missing.json"))

    def test_cli_rejects_incomplete_range_without_falling_back_to_head(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools/ci/check_build_checkpoints.py"),
                                 "--base", "a" * 40], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Supply both --base and --head", result.stderr)


if __name__ == "__main__":
    unittest.main()
