"""Real Git worktree tests for the local cross-provider review gate."""

import json
import hashlib
import os
import stat
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from cross_review_protocol import (  # noqa: E402
    ProtocolError,
    _raw_commit,
    capture_revision,
    main,
    prepare_pr_packet,
    validate_manifest,
    validate_receipt,
)


def git_env():
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    return env


def git(where, *args):
    return subprocess.check_output(["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "-C", str(where), *args], text=True, env=git_env()).strip()


class CrossReviewProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.name", "Test")
        git(self.repo, "config", "user.email", "test@example.invalid")
        (self.repo / "README.md").write_text("base\n")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-qm", "base")
        self.base = git(self.repo, "rev-parse", "HEAD")
        approved = root / "approved"
        approved.mkdir()
        self.evidence = root / "controller-evidence"
        self.evidence.mkdir()
        self.codex = approved / "codex"
        self.claude = approved / "claude"
        git(self.repo, "worktree", "add", "-qb", "codex/one", str(self.codex), self.base)
        git(self.repo, "worktree", "add", "-qb", "claude/two", str(self.claude), self.base)
        self.manifest = {
            "task_id": "division23-protocol-001",
            "base_commit": self.base,
            "repository_common_dir": str((self.repo / ".git").resolve()),
            "worktree_parent": str(approved),
            "review_evidence_root": str(self.evidence),
            "required_checks": ["python3 -m unittest discover -s tests"],
            "authors": {
                "codex": {"worktree": str(self.codex), "branch": "codex/one", "allowed_paths": ["scripts/codex.py"], "reviewer": "claude"},
                "claude": {"worktree": str(self.claude), "branch": "claude/two", "allowed_paths": ["scripts/claude.py"], "reviewer": "codex"},
            },
        }

    def _candidate(self, team="codex"):
        worktree = self.codex if team == "codex" else self.claude
        path = worktree / "scripts" / f"{team}.py"
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"print('{team}')\n")
        return capture_revision(self.manifest, team)

    def _receipt(self, candidate, team="codex"):
        log = self.evidence / f"{team}-check.log"
        log.write_text("retained check output\n")
        command = self.manifest["required_checks"][0]
        check_record = {"task_id": self.manifest["task_id"], "author_team": team, "base_commit": self.base, "head_commit": candidate["head_commit"], "revision_digest": candidate["revision_digest"], "command": command, "exit_code": 0, "output_path": str(log), "output_sha256": hashlib.sha256(log.read_bytes()).hexdigest()}
        check_path = self.evidence / f"{team}-check.json"
        check_path.write_text(json.dumps(check_record))
        check = {"command": command, "exit_code": 0, "evidence_path": str(check_path), "evidence_sha256": hashlib.sha256(check_path.read_bytes()).hexdigest()}
        report = {"task_id": self.manifest["task_id"], "author_team": team, "reviewer_team": "claude" if team == "codex" else "codex", "reviewer_run_id": f"{team}-review-run", "base_commit": self.base, "head_commit": candidate["head_commit"], "revision_digest": candidate["revision_digest"], "decision": "accepted", "findings": [], "checks": [{"command": check["command"], "exit_code": 0, "evidence_sha256": check["evidence_sha256"]}]}
        report_path = self.evidence / f"{team}-review.json"
        report_path.write_text(json.dumps(report))
        report_hash = hashlib.sha256(report_path.read_bytes()).hexdigest()
        run_record = {"task_id": self.manifest["task_id"], "author_team": team, "reviewer_team": report["reviewer_team"], "reviewer_run_id": report["reviewer_run_id"], "provider": "anthropic" if team == "codex" else "openai", "base_commit": self.base, "head_commit": candidate["head_commit"], "revision_digest": candidate["revision_digest"], "reviewer_report_sha256": report_hash, "check_record_sha256s": [check["evidence_sha256"]], "run_status": "terminal_success"}
        run_path = self.evidence / f"{team}-run.json"
        run_path.write_text(json.dumps(run_record))
        return {
            "task_id": self.manifest["task_id"],
            "author_team": team,
            "reviewer_team": "claude" if team == "codex" else "codex",
            "reviewer_run_id": f"{team}-review-run",
            "base_commit": self.base,
            "head_commit": candidate["head_commit"],
            "revision_digest": candidate["revision_digest"],
            "manifest_digest": candidate["manifest_digest"],
            "decision": "accepted",
            "findings": [],
            "checks": [check],
            "reviewer_report": {"path": str(report_path), "sha256": report_hash},
            "reviewer_run_record": {"path": str(run_path), "sha256": hashlib.sha256(run_path.read_bytes()).hexdigest()},
            "reviewed_at": "2026-10-01T12:00:00Z",
        }

    def _commit_controller_checkpoint(self, parent, version):
        """Create a valid synthetic selected-tree checkpoint for the real validator."""
        status = self.codex / "CURRENT_STATUS.md"
        receipt = self.codex / "docs" / "operations" / "build-checkpoint.json"
        evidence_name = f"codex-check-v{version}.txt"
        evidence_relative = f"docs/operations/loop-controller-mvp-2026-10-01/cross-review-run-001/checkpoint-evidence/{evidence_name}"
        evidence = self.codex / evidence_relative
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(f"independent synthetic check v{version}\n")
        action = "Continue the admitted P1 feasibility preparation."
        marker = {"schema_version": 1, "task_id": "CROSS-REVIEW-TEST", "outcome": "in_progress",
                  "next_task_id": "P1-FEASIBILITY", "next_action": action,
                  "receipt": "docs/operations/build-checkpoint.json"}
        status.write_text(
            "# Current Status\n\n**Primary product task: P1-FEASIBILITY.**\n\n"
            f"Synthetic checkpoint version {version}.\n\n**Next executable action:** {action}\n\n"
            "<!-- build-checkpoint:v1 -->\n```json\n" + json.dumps(marker) +
            "\n```\n<!-- /build-checkpoint -->\n")
        code = self.codex / "scripts" / "codex.py"
        changed = [
            {"path": "CURRENT_STATUS.md", "sha256": hashlib.sha256(status.read_bytes()).hexdigest()},
            {"path": evidence_relative, "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()},
            {"path": "scripts/codex.py", "sha256": hashlib.sha256(code.read_bytes()).hexdigest()},
        ]
        checkpoint = {
            "schema_version": 1, "task_id": "CROSS-REVIEW-TEST", "outcome": "in_progress",
            "base_commit": parent, "summary": f"Synthetic protocol checkpoint v{version}.",
            "remaining_limits": ["Not production evidence"], "next_task_id": "P1-FEASIBILITY",
            "next_action": action, "changed_files": changed,
            "checks": [{"command": "synthetic check", "exit_code": 0,
                        "evidence": {"path": evidence_relative,
                                     "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}}],
            "review": None, "missing_prerequisite": None, "independent_action": None,
        }
        receipt.write_text(json.dumps(checkpoint) + "\n")
        git(self.codex, "add", "scripts/codex.py", "CURRENT_STATUS.md", "docs/operations/build-checkpoint.json", evidence_relative)
        git(self.codex, "commit", "-qm", f"candidate checkpoint v{version}")
        head = git(self.codex, "rev-parse", "HEAD")
        return {
            "commit": head,
            "status_sha256": hashlib.sha256(status.read_bytes()).hexdigest(),
            "receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
            "evidence_files": [{"path": evidence_relative,
                                "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}],
        }

    def test_two_real_worktrees_capture_untracked_bytes_and_packet(self):
        self._candidate("claude")
        candidate = self._candidate()
        self.assertEqual(candidate["files"][0]["path"], "scripts/codex.py")
        self.assertEqual(candidate["files"][0]["status"], "added")
        receipt = self._receipt(candidate)
        self.assertEqual(validate_receipt(self.manifest, "codex", receipt)["revision_digest"], candidate["revision_digest"])
        with self.assertRaisesRegex(ProtocolError, "committed clean HEAD"):
            prepare_pr_packet(self.manifest, "codex", receipt)
        git(self.codex, "add", "scripts/codex.py")
        git(self.codex, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "candidate")
        self.manifest["authors"]["codex"]["head_commit"] = git(self.codex, "rev-parse", "HEAD")
        candidate = capture_revision(self.manifest, "codex")
        receipt = self._receipt(candidate)
        packet = prepare_pr_packet(self.manifest, "codex", receipt)
        self.assertEqual(packet["head_commit"], git(self.codex, "rev-parse", "HEAD"))
        self.assertEqual(packet["reviewer_team"], "claude")
        self.assertEqual(packet["revision_digest"], candidate["revision_digest"])
        self.assertEqual(len(packet["files"]), 1)
        self.assertNotIn("evidence_path", packet["checks"][0])

    def test_controller_checkpoint_pair_is_pinned_outside_author_grant(self):
        self._candidate()
        first_pin = self._commit_controller_checkpoint(self.base, 1)
        first = first_pin["commit"]
        self.manifest["authors"]["codex"]["head_commit"] = first
        self.manifest["checkpoint_validator_sha256"] = hashlib.sha256(
            (Path(__file__).resolve().parents[2] / "scripts" / "verify-build-checkpoint.py").read_bytes()).hexdigest()
        self.manifest["authors"]["codex"]["controller_checkpoint_commits"] = [first_pin]
        candidate = capture_revision(self.manifest, "codex")
        self.assertTrue(candidate["committed_clean"])
        self.assertEqual({item["path"] for item in candidate["files"]}, {
            "scripts/codex.py", "CURRENT_STATUS.md", "docs/operations/build-checkpoint.json",
            first_pin["evidence_files"][0]["path"]})
        packet = prepare_pr_packet(self.manifest, "codex", self._receipt(candidate))
        self.assertEqual(packet["head_commit"], first)

        (self.codex / "scripts" / "codex.py").write_text("print('repair')\n")
        second_pin = self._commit_controller_checkpoint(first, 2)
        second = second_pin["commit"]
        self.manifest["authors"]["codex"]["head_commit"] = second
        with self.assertRaisesRegex(ProtocolError, "controller checkpoint"):
            capture_revision(self.manifest, "codex")
        self.manifest["authors"]["codex"]["controller_checkpoint_commits"].append(second_pin)
        candidate = capture_revision(self.manifest, "codex")
        self.assertEqual(len(candidate["history"]), 2)
        self.assertEqual(prepare_pr_packet(self.manifest, "codex", self._receipt(candidate))["head_commit"], second)

        status = self.codex / "CURRENT_STATUS.md"
        status.write_text("unauthorized working-tree edit\n")
        with self.assertRaisesRegex(ProtocolError, "controller checkpoint final pin mismatch"):
            capture_revision(self.manifest, "codex")
        status.write_bytes(git(self.codex, "show", "HEAD:CURRENT_STATUS.md").encode() + b"\n")

        second_pin["receipt_sha256"] = "0" * 64
        with self.assertRaisesRegex(ProtocolError, "controller checkpoint"):
            capture_revision(self.manifest, "codex")

    def test_controller_checkpoint_pin_does_not_allow_other_protected_paths(self):
        self._candidate()
        status = self.codex / "CURRENT_STATUS.md"
        receipt = self.codex / "docs" / "operations" / "build-checkpoint.json"
        forbidden = self.codex / ".github" / "workflows" / "unsafe.yml"
        receipt.parent.mkdir(parents=True)
        forbidden.parent.mkdir(parents=True)
        status.write_text("controller status\n")
        receipt.write_text("{}\n")
        forbidden.write_text("unsafe\n")
        git(self.codex, "add", "scripts/codex.py", "CURRENT_STATUS.md", "docs/operations/build-checkpoint.json", ".github/workflows/unsafe.yml")
        git(self.codex, "commit", "-qm", "forbidden protected path")
        head = git(self.codex, "rev-parse", "HEAD")
        self.manifest["authors"]["codex"]["head_commit"] = head
        self.manifest["authors"]["codex"]["controller_checkpoint_commits"] = [{
            "commit": head,
            "status_sha256": hashlib.sha256(status.read_bytes()).hexdigest(),
            "receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
            "evidence_files": [],
        }]
        self.manifest["checkpoint_validator_sha256"] = hashlib.sha256(
            (Path(__file__).resolve().parents[2] / "scripts" / "verify-build-checkpoint.py").read_bytes()).hexdigest()
        with self.assertRaisesRegex(ProtocolError, "forbidden candidate path"):
            capture_revision(self.manifest, "codex")

    def test_controller_checkpoint_exception_requires_pair_in_every_commit(self):
        self._candidate()
        status = self.codex / "CURRENT_STATUS.md"
        status.write_text("one metadata file only\n")
        git(self.codex, "add", "scripts/codex.py", "CURRENT_STATUS.md")
        git(self.codex, "commit", "-qm", "missing receipt")
        head = git(self.codex, "rev-parse", "HEAD")
        self.manifest["authors"]["codex"]["head_commit"] = head
        self.manifest["authors"]["codex"]["controller_checkpoint_commits"] = [{
            "commit": head,
            "status_sha256": hashlib.sha256(status.read_bytes()).hexdigest(),
            "receipt_sha256": "0" * 64,
            "evidence_files": [],
        }]
        self.manifest["checkpoint_validator_sha256"] = hashlib.sha256(
            (Path(__file__).resolve().parents[2] / "scripts" / "verify-build-checkpoint.py").read_bytes()).hexdigest()
        with self.assertRaisesRegex(ProtocolError, "requires both controller checkpoint paths"):
            capture_revision(self.manifest, "codex")

    def test_controller_checkpoint_real_validator_rejects_invalid_receipt(self):
        self._candidate()
        first_pin = self._commit_controller_checkpoint(self.base, 1)
        first = first_pin["commit"]
        (self.codex / "scripts" / "codex.py").write_text("print('repair')\n")
        status = self.codex / "CURRENT_STATUS.md"
        status.write_text(status.read_text() + "Invalid receipt test.\n")
        receipt = self.codex / "docs" / "operations" / "build-checkpoint.json"
        receipt.write_text('{"malformed":"checkpoint"}\n')
        git(self.codex, "add", "scripts/codex.py", "CURRENT_STATUS.md", "docs/operations/build-checkpoint.json")
        git(self.codex, "commit", "-qm", "bad checkpoint repair")
        second = git(self.codex, "rev-parse", "HEAD")
        self.manifest["authors"]["codex"]["head_commit"] = second
        self.manifest["checkpoint_validator_sha256"] = hashlib.sha256(
            (Path(__file__).resolve().parents[2] / "scripts" / "verify-build-checkpoint.py").read_bytes()).hexdigest()
        self.manifest["authors"]["codex"]["controller_checkpoint_commits"] = [first_pin, {
            "commit": second,
            "status_sha256": hashlib.sha256(status.read_bytes()).hexdigest(),
            "receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
            "evidence_files": [],
        }]
        with self.assertRaisesRegex(ProtocolError, "checkpoint validator rejected"):
            capture_revision(self.manifest, "codex")

    def test_graft_cannot_bypass_real_checkpoint_validator(self):
        self._candidate()
        first_pin = self._commit_controller_checkpoint(self.base, 1)
        (self.codex / "scripts" / "codex.py").write_text("print('repair')\n")
        status = self.codex / "CURRENT_STATUS.md"
        status.write_text(status.read_text() + "Invalid receipt test.\n")
        receipt = self.codex / "docs" / "operations" / "build-checkpoint.json"
        receipt.write_text('{"malformed":"checkpoint"}\n')
        git(self.codex, "add", "scripts/codex.py", "CURRENT_STATUS.md",
            "docs/operations/build-checkpoint.json")
        git(self.codex, "commit", "-qm", "bad checkpoint repair")
        head = git(self.codex, "rev-parse", "HEAD")
        forged_parent = git(self.codex, "commit-tree",
                            git(self.codex, "rev-parse", "HEAD^{tree}"),
                            "-p", first_pin["commit"])
        common = Path(self.manifest["repository_common_dir"])
        (common / "info" / "grafts").write_text(f"{head} {forged_parent}\n")
        self.manifest["authors"]["codex"]["head_commit"] = head
        self.manifest["checkpoint_validator_sha256"] = hashlib.sha256(
            (Path(__file__).resolve().parents[2] / "scripts" /
             "verify-build-checkpoint.py").read_bytes()).hexdigest()
        self.manifest["authors"]["codex"]["controller_checkpoint_commits"] = [
            first_pin, {
                "commit": head,
                "status_sha256": hashlib.sha256(status.read_bytes()).hexdigest(),
                "receipt_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
                "evidence_files": [],
            }]
        self.assertNotEqual(git(self.codex, "rev-list", "--parents", "-n", "1",
                                head).split()[1], first_pin["commit"])
        with self.assertRaisesRegex(ProtocolError, "graft|checkpoint"):
            capture_revision(self.manifest, "codex")

    def test_controller_checkpoint_evidence_and_validator_pins_cannot_drift(self):
        self._candidate()
        pin = self._commit_controller_checkpoint(self.base, 1)
        self.manifest["authors"]["codex"]["head_commit"] = pin["commit"]
        self.manifest["authors"]["codex"]["controller_checkpoint_commits"] = [pin]
        self.manifest["checkpoint_validator_sha256"] = "0" * 64
        with self.assertRaisesRegex(ProtocolError, "trusted checkpoint validator identity"):
            capture_revision(self.manifest, "codex")
        self.manifest["checkpoint_validator_sha256"] = hashlib.sha256(
            (Path(__file__).resolve().parents[2] / "scripts" / "verify-build-checkpoint.py").read_bytes()).hexdigest()
        pin["evidence_files"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ProtocolError, "controller checkpoint evidence"):
            capture_revision(self.manifest, "codex")

    def test_staged_checkpoint_bytes_cannot_differ_from_pinned_head(self):
        self._candidate()
        pin = self._commit_controller_checkpoint(self.base, 1)
        self.manifest["authors"]["codex"]["head_commit"] = pin["commit"]
        self.manifest["authors"]["codex"]["controller_checkpoint_commits"] = [pin]
        self.manifest["checkpoint_validator_sha256"] = hashlib.sha256(
            (Path(__file__).resolve().parents[2] / "scripts" /
             "verify-build-checkpoint.py").read_bytes()).hexdigest()
        status = self.codex / "CURRENT_STATUS.md"
        original = status.read_bytes()
        status.write_bytes(original + b"staged only drift\n")
        git(self.codex, "add", "CURRENT_STATUS.md")
        status.write_bytes(original)
        with self.assertRaisesRegex(ProtocolError, "staged pin mismatch"):
            capture_revision(self.manifest, "codex")

    def test_both_directions_have_opposite_reviewers(self):
        candidate = self._candidate("claude")
        receipt = self._receipt(candidate, "claude")
        self.assertEqual(validate_receipt(self.manifest, "claude", receipt)["revision_digest"], candidate["revision_digest"])
        self.assertEqual(receipt["reviewer_team"], "codex")

    def test_other_author_head_drift_does_not_invalidate_unchanged_review(self):
        candidate = self._candidate()
        receipt = self._receipt(candidate)
        self._candidate("claude")
        git(self.claude, "add", "scripts/claude.py")
        git(self.claude, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "other")
        self.assertEqual(validate_receipt(self.manifest, "codex", receipt)["revision_digest"], candidate["revision_digest"])
        self.manifest["authors"]["claude"]["head_commit"] = git(self.claude, "rev-parse", "HEAD")
        self.assertEqual(validate_receipt(self.manifest, "codex", receipt)["revision_digest"], candidate["revision_digest"])

    def test_overlapping_writer_path_is_rejected(self):
        self.manifest["authors"]["claude"]["allowed_paths"] = ["scripts/"]
        with self.assertRaisesRegex(ProtocolError, "overlap"):
            validate_manifest(self.manifest)

    def test_directory_grant_requires_descendant_not_directory_named_file(self):
        self.manifest["authors"]["codex"]["allowed_paths"] = ["sandbox/"]
        (self.codex / "sandbox").write_text("file at the directory name\n")
        with self.assertRaisesRegex(ProtocolError, "forbidden"):
            capture_revision(self.manifest, "codex")

    def test_case_colliding_writer_paths_are_rejected(self):
        self.manifest["authors"]["codex"]["allowed_paths"] = ["scripts/Foo.py"]
        self.manifest["authors"]["claude"]["allowed_paths"] = ["scripts/foo.py"]
        with self.assertRaisesRegex(ProtocolError, "overlap"):
            validate_manifest(self.manifest)

    def test_file_grant_and_child_path_grant_overlap(self):
        self.manifest["authors"]["codex"]["allowed_paths"] = ["scripts/a"]
        self.manifest["authors"]["claude"]["allowed_paths"] = ["scripts/a/b"]
        with self.assertRaisesRegex(ProtocolError, "overlap"):
            validate_manifest(self.manifest)

    def test_candidate_path_requires_exact_grant_spelling(self):
        path = self.codex / "Scripts" / "CODEX.py"
        path.parent.mkdir()
        path.write_text("different Git path\n")
        with self.assertRaisesRegex(ProtocolError, "forbidden candidate"):
            capture_revision(self.manifest, "codex")

    def test_nested_registered_author_worktree_is_rejected(self):
        nested = self.codex / "nested"
        git(self.repo, "worktree", "add", "-qb", "claude/nested", str(nested), self.base)
        self.manifest["authors"]["claude"]["worktree"] = str(nested)
        self.manifest["authors"]["claude"]["branch"] = "claude/nested"
        with self.assertRaisesRegex(ProtocolError, "nested"):
            validate_manifest(self.manifest)

    def test_wrong_reviewer_is_rejected(self):
        candidate = self._candidate()
        receipt = self._receipt(candidate)
        receipt["reviewer_team"] = "codex"
        with self.assertRaisesRegex(ProtocolError, "reviewer"):
            validate_receipt(self.manifest, "codex", receipt)

    def test_changed_bytes_invalidate_review(self):
        candidate = self._candidate()
        receipt = self._receipt(candidate)
        (self.codex / "scripts" / "codex.py").write_text("print('changed')\n")
        with self.assertRaisesRegex(ProtocolError, "revision"):
            validate_receipt(self.manifest, "codex", receipt)

    def test_changed_executable_mode_invalidates_review(self):
        candidate = self._candidate()
        receipt = self._receipt(candidate)
        path = self.codex / "scripts" / "codex.py"
        path.chmod(path.stat().st_mode | 0o111)
        with self.assertRaisesRegex(ProtocolError, "revision"):
            validate_receipt(self.manifest, "codex", receipt)

    def test_group_only_execute_bit_does_not_change_git_mode(self):
        candidate = self._candidate()
        path = self.codex / "README.md"
        path.chmod(path.stat().st_mode | stat.S_IXGRP)
        self.assertEqual(capture_revision(self.manifest, "codex")["revision_digest"], candidate["revision_digest"])

    def test_widened_path_grant_invalidates_review(self):
        candidate = self._candidate()
        receipt = self._receipt(candidate)
        self.manifest["authors"]["codex"]["allowed_paths"] = ["scripts/codex.py", "scripts/extra.py"]
        with self.assertRaisesRegex(ProtocolError, "manifest|revision"):
            validate_receipt(self.manifest, "codex", receipt)

    def test_deleted_file_binds_original_full_bytes(self):
        self.manifest["authors"]["codex"]["allowed_paths"] = ["README.md"]
        (self.codex / "README.md").unlink()
        candidate = capture_revision(self.manifest, "codex")
        self.assertEqual(candidate["files"][0]["status"], "deleted")
        self.assertEqual(candidate["files"][0]["sha256"], hashlib.sha256(b"base\n").hexdigest())

    def test_new_commit_invalidates_pinned_head(self):
        self._candidate()
        git(self.codex, "add", "scripts/codex.py")
        git(self.codex, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "change")
        with self.assertRaisesRegex(ProtocolError, "head"):
            capture_revision(self.manifest, "codex")

    def test_forbidden_untracked_path_is_rejected(self):
        (self.codex / "credential.txt").write_text("secret")
        with self.assertRaisesRegex(ProtocolError, "forbidden"):
            capture_revision(self.manifest, "codex")

    def test_ignored_untracked_forbidden_path_is_rejected(self):
        (self.repo / ".git" / "info" / "exclude").write_text("hidden.log\n")
        (self.codex / "hidden.log").write_text("hidden")
        with self.assertRaisesRegex(ProtocolError, "forbidden"):
            capture_revision(self.manifest, "codex")

    def test_unreadable_directory_stops_inventory(self):
        self._candidate()
        hidden = self.codex / "hidden"
        hidden.mkdir()
        (hidden / "credential.txt").write_text("secret")
        hidden.chmod(0)
        self.addCleanup(lambda: hidden.chmod(0o700))
        with self.assertRaisesRegex(ProtocolError, "inventory|directory"):
            capture_revision(self.manifest, "codex")

    def test_index_flags_cannot_hide_tracked_files(self):
        for flag in ("--assume-unchanged", "--skip-worktree"):
            with self.subTest(flag=flag):
                git(self.codex, "update-index", flag, "README.md")
                with self.assertRaisesRegex(ProtocolError, "index flag"):
                    capture_revision(self.manifest, "codex")
                git(self.codex, "update-index", "--no-assume-unchanged", "README.md")
                git(self.codex, "update-index", "--no-skip-worktree", "README.md")

    def test_forbidden_committed_head_change_cannot_be_hidden_by_worktree_revert(self):
        (self.codex / "README.md").write_text("forbidden commit\n")
        git(self.codex, "add", "README.md")
        git(self.codex, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "forbidden")
        self.manifest["authors"]["codex"]["head_commit"] = git(self.codex, "rev-parse", "HEAD")
        (self.codex / "README.md").write_text("base\n")
        (self.codex / "scripts").mkdir()
        (self.codex / "scripts" / "codex.py").write_text("allowed\n")
        with self.assertRaisesRegex(ProtocolError, "forbidden"):
            capture_revision(self.manifest, "codex")

    def test_intermediate_forbidden_commit_cannot_be_hidden_by_later_deletion(self):
        (self.codex / "credential.txt").write_text("secret in history\n")
        git(self.codex, "add", "credential.txt")
        git(self.codex, "commit", "-qm", "forbidden intermediate")
        (self.codex / "credential.txt").unlink()
        (self.codex / "scripts").mkdir()
        (self.codex / "scripts" / "codex.py").write_text("allowed final\n")
        git(self.codex, "add", "-A")
        git(self.codex, "commit", "-qm", "allowed final")
        self.manifest["authors"]["codex"]["head_commit"] = git(self.codex, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ProtocolError, "forbidden|history"):
            capture_revision(self.manifest, "codex")

    def test_graft_cannot_hide_intermediate_forbidden_commit(self):
        forbidden = self.codex / "credential.txt"
        forbidden.write_text("secret in history\n")
        git(self.codex, "add", "credential.txt")
        git(self.codex, "commit", "-qm", "forbidden intermediate")
        forbidden.unlink()
        allowed = self.codex / "scripts" / "codex.py"
        allowed.parent.mkdir()
        allowed.write_text("allowed final\n")
        git(self.codex, "add", "-A")
        git(self.codex, "commit", "-qm", "allowed final")
        head = git(self.codex, "rev-parse", "HEAD")
        self.manifest["authors"]["codex"]["head_commit"] = head
        common = Path(self.manifest["repository_common_dir"])
        (common / "info" / "grafts").write_text(f"{head} {self.base}\n")
        self.assertEqual(git(self.codex, "rev-list", "--reverse", f"{self.base}..{head}"), head)
        with self.assertRaisesRegex(ProtocolError, "forbidden committed history"):
            capture_revision(self.manifest, "codex")

    def test_promisor_missing_blob_cannot_execute_uploadpack(self):
        self._candidate()
        git(self.codex, "add", "scripts/codex.py")
        git(self.codex, "commit", "-qm", "candidate")
        head = git(self.codex, "rev-parse", "HEAD")
        blob = git(self.codex, "rev-parse", "HEAD:scripts/codex.py")
        self.manifest["authors"]["codex"]["head_commit"] = head
        common = Path(self.manifest["repository_common_dir"])
        marker = Path(self.temp.name) / "uploadpack-ran"
        uploadpack = Path(self.temp.name) / "uploadpack"
        uploadpack.write_text(f"#!/bin/sh\nprintf invoked > '{marker}'\nexit 1\n")
        uploadpack.chmod(0o755)
        git(self.repo, "config", "remote.evil.url", str(self.repo))
        git(self.repo, "config", "remote.evil.promisor", "true")
        git(self.repo, "config", "remote.evil.uploadpack", str(uploadpack))
        git(self.repo, "config", "extensions.partialClone", "evil")
        (common / "objects" / blob[:2] / blob[2:]).unlink()
        with self.assertRaisesRegex(ProtocolError, "Git state unavailable"):
            capture_revision(self.manifest, "codex")
        self.assertFalse(marker.exists(), "read-only capture executed configured uploadpack")

    def test_forged_nested_base_tree_cannot_hide_protected_change(self):
        protected = self.codex / ".github" / "protected.txt"
        protected.parent.mkdir()
        protected.write_text("base content\n")
        git(self.codex, "add", ".github/protected.txt")
        git(self.codex, "commit", "-qm", "new base")
        new_base = git(self.codex, "rev-parse", "HEAD")
        self.manifest["base_commit"] = new_base
        protected.write_text("forbidden head content\n")
        allowed = self.codex / "scripts" / "codex.py"
        allowed.parent.mkdir()
        allowed.write_text("allowed head content\n")
        git(self.codex, "add", "-A")
        git(self.codex, "commit", "-qm", "forbidden and allowed")
        head = git(self.codex, "rev-parse", "HEAD")
        self.manifest["authors"]["codex"]["head_commit"] = head
        subtree = git(self.codex, "rev-parse", f"{new_base}:.github")
        head_blob = git(self.codex, "rev-parse", f"{head}:.github/protected.txt")
        raw = b"100644 protected.txt\0" + bytes.fromhex(head_blob)
        loose = Path(self.manifest["repository_common_dir"]) / "objects" / subtree[:2] / subtree[2:]
        loose.chmod(0o644)
        loose.write_bytes(zlib.compress(f"tree {len(raw)}\0".encode() + raw))
        with self.assertRaisesRegex(ProtocolError, "tree object hash mismatch"):
            capture_revision(self.manifest, "codex")

    def test_forged_blob_object_is_rejected(self):
        self._candidate()
        git(self.codex, "add", "scripts/codex.py")
        git(self.codex, "commit", "-qm", "candidate")
        self.manifest["authors"]["codex"]["head_commit"] = git(self.codex, "rev-parse", "HEAD")
        blob = git(self.codex, "rev-parse", "HEAD:scripts/codex.py")
        raw = b"forged blob\n"
        loose = Path(self.manifest["repository_common_dir"]) / "objects" / blob[:2] / blob[2:]
        loose.chmod(0o644)
        loose.write_bytes(zlib.compress(f"blob {len(raw)}\0".encode() + raw))
        with self.assertRaisesRegex(ProtocolError, "blob object hash mismatch"):
            capture_revision(self.manifest, "codex")

    def test_oversized_git_blob_output_is_bounded_and_rejected(self):
        allowed = self.codex / "scripts" / "codex.py"
        allowed.parent.mkdir()
        allowed.write_bytes(b"x" * 4096)
        git(self.codex, "add", "scripts/codex.py")
        git(self.codex, "commit", "-qm", "candidate")
        self.manifest["authors"]["codex"]["head_commit"] = git(self.codex, "rev-parse", "HEAD")
        stderr = io.StringIO()
        with mock.patch.dict("cross_review_protocol._OBJECT_OUTPUT_LIMITS", {"blob": 256}), contextlib.redirect_stderr(stderr):
            with self.assertRaisesRegex(ProtocolError, "Git output limit exceeded"):
                capture_revision(self.manifest, "codex")
        self.assertEqual(stderr.getvalue(), "")

    def test_total_verified_object_budget_is_bounded(self):
        allowed = self.codex / "scripts" / "codex.py"
        allowed.parent.mkdir()
        allowed.write_bytes(b"y" * 128)
        git(self.codex, "add", "scripts/codex.py")
        git(self.codex, "commit", "-qm", "candidate")
        self.manifest["authors"]["codex"]["head_commit"] = git(self.codex, "rev-parse", "HEAD")
        with mock.patch("cross_review_protocol._VERIFIED_OBJECT_BUDGET", 800):
            with self.assertRaisesRegex(ProtocolError, "Git output limit exceeded|budget exhausted"):
                capture_revision(self.manifest, "codex")

    def test_misordered_raw_commit_headers_are_rejected(self):
        tree = git(self.codex, "rev-parse", f"{self.base}^{{tree}}")
        raw = (f"author Test <test@example.invalid> 1 +0000\n"
               f"tree {tree}\nparent {self.base}\n"
               "committer Test <test@example.invalid> 1 +0000\n\nmalformed\n").encode()
        oid = subprocess.check_output(
            ["git", "-C", str(self.codex), "hash-object", "-t", "commit", "-w", "--literally", "--stdin"],
            input=raw, env=git_env(), stderr=subprocess.DEVNULL,
        ).decode().strip()
        with self.assertRaisesRegex(ProtocolError, "misordered raw commit headers"):
            _raw_commit(self.codex, oid, "sha1")

    def test_subject_checkout_from_different_repository_is_rejected(self):
        rogue = Path(self.manifest["worktree_parent"]) / "rogue"
        subprocess.check_call(["git", "clone", "-q", str(self.repo), str(rogue)], env=git_env())
        git(rogue, "switch", "-qc", "codex/rogue", self.base)
        (rogue / "scripts").mkdir()
        (rogue / "scripts" / "codex.py").write_text("rogue\n")
        self.manifest["authors"]["codex"]["worktree"] = str(rogue)
        self.manifest["authors"]["codex"]["branch"] = "codex/rogue"
        with self.assertRaisesRegex(ProtocolError, "common|repository|gitfile"):
            capture_revision(self.manifest, "codex")

    def test_ancestor_evidence_root_is_rejected(self):
        self.manifest["review_evidence_root"] = self.temp.name
        with self.assertRaisesRegex(ProtocolError, "evidence root"):
            validate_manifest(self.manifest)

    def test_evidence_root_ancestor_or_child_of_common_git_dir_is_rejected(self):
        for root in (self.repo, Path(self.manifest["repository_common_dir"])):
            with self.subTest(root=root):
                self.manifest["review_evidence_root"] = str(root)
                with self.assertRaisesRegex(ProtocolError, "evidence root"):
                    validate_manifest(self.manifest)
        nested = Path(self.manifest["repository_common_dir"]) / "review-evidence"
        nested.mkdir()
        self.manifest["review_evidence_root"] = str(nested)
        with self.assertRaisesRegex(ProtocolError, "evidence root"):
            validate_manifest(self.manifest)

    def test_symlinked_evidence_root_under_author_parent_is_rejected(self):
        alias = Path(self.manifest["worktree_parent"]) / "evidence-alias"
        alias.symlink_to(self.evidence, target_is_directory=True)
        self.manifest["review_evidence_root"] = str(alias)
        with self.assertRaisesRegex(ProtocolError, "evidence root"):
            validate_manifest(self.manifest)

    def test_evidence_directory_symlink_is_rejected(self):
        candidate = self._candidate()
        receipt = self._receipt(candidate)
        alias = self.evidence / "alias"
        alias.symlink_to(self.evidence, target_is_directory=True)
        receipt["checks"][0]["evidence_path"] = str(alias / "codex-check.json")
        with self.assertRaisesRegex(ProtocolError, "evidence"):
            validate_receipt(self.manifest, "codex", receipt)

    def test_rm_cached_file_cannot_reuse_base_hash_for_changed_disk_bytes(self):
        self.manifest["authors"]["codex"]["allowed_paths"] = ["README.md"]
        git(self.codex, "rm", "--cached", "README.md")
        (self.codex / "README.md").write_text("changed on disk\n")
        with self.assertRaisesRegex(ProtocolError, "index|divergen"):
            capture_revision(self.manifest, "codex")

    def test_symlink_candidate_is_rejected(self):
        target = self.codex / "scripts" / "codex.py"
        target.parent.mkdir()
        target.symlink_to(self.repo / "README.md")
        with self.assertRaisesRegex(ProtocolError, "symlink"):
            capture_revision(self.manifest, "codex")

    def test_unsafe_allowed_path_is_rejected(self):
        self.manifest["authors"]["codex"]["allowed_paths"] = ["../secrets"]
        with self.assertRaisesRegex(ProtocolError, "unsafe"):
            validate_manifest(self.manifest)
        self.manifest["authors"]["codex"]["allowed_paths"] = ["./"]
        with self.assertRaisesRegex(ProtocolError, "unsafe"):
            validate_manifest(self.manifest)

    def test_case_and_windows_alias_grants_are_rejected(self):
        for path in ("agents.md", ".Github/", "docs/Decisions/", "scripts/.Git/", "scripts/file. ", "scripts/file:meta", "scripts/CON", "scripts/FOO~1.PY", "AGENTS.md/"):
            with self.subTest(path=path):
                self.manifest["authors"]["codex"]["allowed_paths"] = [path]
                with self.assertRaises(ProtocolError):
                    validate_manifest(self.manifest)

    def test_git_environment_cannot_redirect_index(self):
        candidate = self._candidate()
        old = os.environ.get("GIT_INDEX_FILE")
        os.environ["GIT_INDEX_FILE"] = str(Path(self.temp.name) / "wrong-index")
        try:
            self.assertEqual(capture_revision(self.manifest, "codex")["revision_digest"], candidate["revision_digest"])
        finally:
            if old is None:
                os.environ.pop("GIT_INDEX_FILE", None)
            else:
                os.environ["GIT_INDEX_FILE"] = old

    def test_fixture_git_helper_ignores_ambient_git_dir(self):
        old = os.environ.get("GIT_DIR")
        os.environ["GIT_DIR"] = str(Path(self.temp.name) / "invalid-git-dir")
        try:
            self.assertEqual(git(self.codex, "rev-parse", "HEAD"), self.base)
        finally:
            if old is None:
                os.environ.pop("GIT_DIR", None)
            else:
                os.environ["GIT_DIR"] = old

    def test_check_and_reviewer_report_bytes_are_verified(self):
        receipt = self._receipt(self._candidate())
        Path(receipt["checks"][0]["evidence_path"]).write_text("tampered")
        with self.assertRaisesRegex(ProtocolError, "evidence"):
            validate_receipt(self.manifest, "codex", receipt)
        receipt = self._receipt(capture_revision(self.manifest, "codex"))
        Path(receipt["reviewer_report"]["path"]).write_text("tampered")
        with self.assertRaisesRegex(ProtocolError, "reviewer report"):
            validate_receipt(self.manifest, "codex", receipt)

    def test_check_record_for_other_revision_is_rejected(self):
        receipt = self._receipt(self._candidate())
        path = Path(receipt["checks"][0]["evidence_path"])
        record = json.loads(path.read_text())
        record["revision_digest"] = "0" * 64
        path.write_text(json.dumps(record))
        receipt["checks"][0]["evidence_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ProtocolError, "check record"):
            validate_receipt(self.manifest, "codex", receipt)

    def test_boolean_check_exit_in_record_is_rejected(self):
        receipt = self._receipt(self._candidate())
        path = Path(receipt["checks"][0]["evidence_path"])
        record = json.loads(path.read_text())
        record["exit_code"] = False
        path.write_text(json.dumps(record))
        receipt["checks"][0]["evidence_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ProtocolError, "check record"):
            validate_receipt(self.manifest, "codex", receipt)

    def test_evidence_outside_controller_root_is_rejected(self):
        receipt = self._receipt(self._candidate())
        outside = Path(self.temp.name) / "outside-check.json"
        outside.write_bytes(Path(receipt["checks"][0]["evidence_path"]).read_bytes())
        receipt["checks"][0]["evidence_path"] = str(outside)
        with self.assertRaisesRegex(ProtocolError, "controller evidence root"):
            validate_receipt(self.manifest, "codex", receipt)

    def test_missing_reviewer_run_record_is_rejected(self):
        receipt = self._receipt(self._candidate())
        receipt.pop("reviewer_run_record")
        with self.assertRaisesRegex(ProtocolError, "reviewer run"):
            validate_receipt(self.manifest, "codex", receipt)

    def test_report_with_valid_hash_but_wrong_revision_is_rejected(self):
        receipt = self._receipt(self._candidate())
        path = Path(receipt["reviewer_report"]["path"])
        report = json.loads(path.read_text())
        report["revision_digest"] = "0" * 64
        path.write_text(json.dumps(report))
        receipt["reviewer_report"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ProtocolError, "reviewer report"):
            validate_receipt(self.manifest, "codex", receipt)

    def test_review_evidence_inside_author_checkout_is_rejected(self):
        receipt = self._receipt(self._candidate())
        inside = self.codex / "scripts" / "codex.py"
        receipt["checks"][0]["evidence_path"] = str(inside)
        receipt["checks"][0]["evidence_sha256"] = hashlib.sha256(inside.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ProtocolError, "controller evidence root|author worktree"):
            validate_receipt(self.manifest, "codex", receipt)

    def test_ignored_granted_file_blocks_clean_committed_packet(self):
        self._candidate()
        git(self.codex, "add", "scripts/codex.py")
        git(self.codex, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "candidate")
        self.manifest["authors"]["codex"]["head_commit"] = git(self.codex, "rev-parse", "HEAD")
        self.manifest["authors"]["codex"]["allowed_paths"].append("scratch.log")
        (self.repo / ".git" / "info" / "exclude").write_text("scratch.log\n")
        (self.codex / "scratch.log").write_text("ignored but present\n")
        candidate = capture_revision(self.manifest, "codex")
        self.assertFalse(candidate["committed_clean"])
        with self.assertRaisesRegex(ProtocolError, "committed clean HEAD"):
            prepare_pr_packet(self.manifest, "codex", self._receipt(candidate))

    def test_cli_rejected_review_is_nonzero(self):
        receipt = self._receipt(self._candidate())
        receipt["decision"] = "rejected"
        receipt["findings"] = [{"reason": "a concrete defect"}]
        report_path = Path(receipt["reviewer_report"]["path"])
        report = json.loads(report_path.read_text())
        report["decision"] = "rejected"
        report["findings"] = receipt["findings"]
        report_path.write_text(json.dumps(report))
        receipt["reviewer_report"]["sha256"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
        run_path = Path(receipt["reviewer_run_record"]["path"])
        run_record = json.loads(run_path.read_text())
        run_record["reviewer_report_sha256"] = receipt["reviewer_report"]["sha256"]
        run_path.write_text(json.dumps(run_record))
        receipt["reviewer_run_record"]["sha256"] = hashlib.sha256(run_path.read_bytes()).hexdigest()
        manifest_path = Path(self.temp.name) / "manifest.json"
        receipt_path = Path(self.temp.name) / "receipt.json"
        manifest_path.write_text(json.dumps(self.manifest))
        receipt_path.write_text(json.dumps(receipt))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["validate-review", str(manifest_path), "--author", "codex", "--receipt", str(receipt_path)]), 3)

    def test_cli_nonobject_receipt_is_controlled_rejection(self):
        self._candidate()
        manifest_path = Path(self.temp.name) / "manifest.json"
        receipt_path = Path(self.temp.name) / "receipt.json"
        manifest_path.write_text(json.dumps(self.manifest))
        receipt_path.write_text("[]")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(["validate-review", str(manifest_path), "--author", "codex", "--receipt", str(receipt_path)]), 2)

    def test_protected_authority_grants_are_rejected(self):
        for grant in (
            ".git/", ".github/", ".loop-trial/", "CURRENT_STATUS.md",
            "docs/decisions/", "docs/operations/build-checkpoint.json",
            "docs/operations/checkpoint-evidence-policy.json",
            "scripts/verify-build-checkpoint.py", "scripts/active-build-status.py",
            "scripts/verify-csi-division23.py",
            "tools/ci/check_build_checkpoints.py", "scripts/", "docs/",
            "scripts/cross_review_protocol.py", "tests/cross-review/test_cross_review_protocol.py",
            "docs/operations/acceptance.md", "docs/architecture/decisions/",
            "docs/operations/guarded-worker-runner.md", "docs/operations/claude-opus-5-5-ultracode.json",
            "docs/plans/division-23-section-register.json",
            "docs/plans/division-23-task-contracts.json", ".cursor/", ".codex/",
            "docs/operations/claude-opus-5-5-ultracode.md",
            "crates/heleos-worker-runner/", "scripts/run-windows-native-candidate.ps1",
            "docs/runs/", "docs/research/notebooklm/", ".worktrees/",
            "docs/operations/loop-controller-mvp-2026-10-01/",
            "scripts/cross_review_protocol/", "scripts/build_run_guard/",
            "scripts/__pycache__/", "tests/cross-review/__pycache__/",
        ):
            with self.subTest(grant=grant):
                self.manifest["authors"]["codex"]["allowed_paths"] = [grant]
                with self.assertRaises(ProtocolError):
                    validate_manifest(self.manifest)

    def test_csi_register_and_import_shadow_grants_are_rejected(self):
        self.manifest["authors"]["codex"]["allowed_paths"] = [
            "docs/plans/division-23-section-register.json", "scripts/json.py"]
        with self.assertRaisesRegex(ProtocolError, "protected authority path"):
            validate_manifest(self.manifest)

    def test_missing_or_failed_check_blocks_acceptance(self):
        candidate = self._candidate()
        receipt = self._receipt(candidate)
        receipt["checks"] = []
        with self.assertRaisesRegex(ProtocolError, "checks"):
            validate_receipt(self.manifest, "codex", receipt)
        receipt = self._receipt(candidate)
        receipt["checks"][0]["exit_code"] = 1
        with self.assertRaisesRegex(ProtocolError, "check"):
            validate_receipt(self.manifest, "codex", receipt)

    def test_base_drift_and_different_repository_are_rejected(self):
        self.manifest["base_commit"] = "0" * 40
        with self.assertRaisesRegex(ProtocolError, "base"):
            validate_manifest(self.manifest)
        self.manifest["base_commit"] = self.base
        other = Path(self.manifest["worktree_parent"]) / "other"
        subprocess.check_call(["git", "clone", "-q", str(self.repo), str(other)], env=git_env())
        git(other, "switch", "-qc", "claude/two", self.base)
        self.manifest["authors"]["claude"]["worktree"] = str(other)
        with self.assertRaisesRegex(ProtocolError, "repository"):
            validate_manifest(self.manifest)

    def test_active_checkout_outside_approved_parent_is_rejected(self):
        self.manifest["authors"]["codex"]["worktree"] = str(self.repo)
        self.manifest["authors"]["codex"]["branch"] = git(self.repo, "branch", "--show-current")
        with self.assertRaisesRegex(ProtocolError, "approved worktree parent"):
            validate_manifest(self.manifest)

    def test_missing_worktree_parent_is_rejected(self):
        del self.manifest["worktree_parent"]
        with self.assertRaisesRegex(ProtocolError, "worktree parent"):
            validate_manifest(self.manifest)


if __name__ == "__main__":
    unittest.main()
