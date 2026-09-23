"""Contract tests use real Git indexes and trees, never working-tree substitutes."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/verify-build-checkpoint.py"
RECEIPT = "docs/operations/build-checkpoint.json"
STATUS = "CURRENT_STATUS.md"


def digest(data):
    return hashlib.sha256(data).hexdigest()


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        self.write("README.md", "baseline\n")
        self.write("scripts/example.py", "baseline\n")
        self.write(STATUS, "initial status\n")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.base = self.git("rev-parse", "HEAD").stdout.decode().strip()

    def git(self, *args, input=None, check=True):
        return subprocess.run(["git", "-C", str(self.repo), *args], input=input,
                              capture_output=True, check=check)

    def write(self, path, value):
        destination = self.repo / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(value.encode() if isinstance(value, str) else value)

    def check(self, good=True, message=None, commit=None):
        command = [sys.executable, str(CHECKER), "--repo", str(self.repo)]
        command += ["--commit", commit] if commit else ["--staged"]
        result = subprocess.run(command, capture_output=True, text=True)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0 if good else 1, output)
        if message:
            self.assertIn(message, output)
        return output

    def checkpoint(self, outcome="in_progress", extra=None):
        self.write("scripts/example.py", "candidate\n")
        next_task = "NEXT-1" if outcome == "complete" else "TASK-1"
        next_action = "Build the next bounded capability."
        marker = {"schema_version": 1, "receipt": RECEIPT, "task_id": "TASK-1",
                  "outcome": outcome, "next_task_id": next_task, "next_action": next_action}
        self.write(STATUS, f"# Status\n\n**Primary product task: {next_task}.** Details.\n\n"
                   f"**Next executable action:** {next_action}\n\n"
                   "<!-- build-checkpoint:v1 -->\n```json\n" + json.dumps(marker) +
                   "\n```\n<!-- /build-checkpoint -->\n")
        self.write("docs/evidence/check.txt", "fixture checks passed\n")
        self.write("docs/evidence/review.txt", "independent reviewer accepted\n")
        self.git("add", ".")
        changed = self.git("diff", "--cached", "--name-only", "--no-renames", "-z", self.base).stdout
        files = []
        for raw in changed.split(b"\0"):
            if not raw:
                continue
            path = raw.decode()
            if path == RECEIPT:
                continue
            blob = self.git("show", ":" + path, check=False)
            files.append({"path": path, "sha256": digest(blob.stdout) if blob.returncode == 0 else None})
        receipt = {"schema_version": 1, "task_id": "TASK-1", "outcome": outcome,
                   "base_commit": self.base, "summary": "Bounded delivery checkpoint.",
                   "remaining_limits": ["Representative acceptance remains open."],
                   "next_task_id": next_task, "next_action": next_action,
                   "changed_files": files, "checks": [], "review": None,
                   "missing_prerequisite": None, "independent_action": None}
        if outcome == "complete":
            receipt["checks"] = [{"command": "python3 fixture_check.py", "exit_code": 0,
                                  "evidence": self.proof("docs/evidence/check.txt")}]
            receipt["review"] = {"reviewer": "Independent reviewer", "outcome": "accepted",
                                 "evidence": self.proof("docs/evidence/review.txt")}
        if outcome == "blocked":
            receipt.update(missing_prerequisite="Missing representative drawing.",
                           independent_action="Implement the ready export task.")
        if extra:
            receipt.update(extra)
        self.save_receipt(receipt)
        return receipt

    def proof(self, path):
        return {"path": path, "sha256": digest((self.repo / path).read_bytes())}

    def save_receipt(self, receipt):
        self.write(RECEIPT, json.dumps(receipt, indent=2) + "\n")
        self.git("add", RECEIPT)

    def test_protected_change_requires_changed_status_and_receipt(self):
        self.write("scripts/example.py", "candidate")
        self.git("add", "scripts/example.py")
        self.check(False, "CURRENT_STATUS.md must change")

    def test_valid_progress_checkpoint(self):
        self.checkpoint()
        self.check()

    def test_valid_complete_checkpoint_and_committed_tree_ignore_dirty_worktree(self):
        self.checkpoint("complete")
        self.check()
        self.git("commit", "-qm", "complete")
        commit = self.git("rev-parse", "HEAD").stdout.decode().strip()
        self.write(RECEIPT, "broken unstaged receipt")
        self.write(STATUS, "broken unstaged status")
        self.write("docs/evidence/check.txt", "broken unstaged evidence")
        self.check(commit=commit)

    def test_staged_checkpoint_ignores_dirty_evidence_and_status(self):
        self.checkpoint("complete")
        self.write(STATUS, "unstaged replacement")
        self.write("docs/evidence/review.txt", "unstaged replacement")
        self.check()

    def test_unstaged_status_cannot_fix_index(self):
        self.checkpoint()
        self.git("reset", "-q", "HEAD", "--", STATUS)
        self.check(False, "CURRENT_STATUS.md must change")

    def test_unstaged_receipt_repair_cannot_fix_index(self):
        receipt = self.checkpoint()
        receipt["summary"] = ""
        self.save_receipt(receipt)
        receipt["summary"] = "Only the worktree is repaired."
        self.write(RECEIPT, json.dumps(receipt))
        self.check(False, "summary")

    def test_untracked_evidence_cannot_satisfy_complete(self):
        receipt = self.checkpoint("complete")
        self.write("untracked-review.txt", "review")
        receipt["review"]["evidence"] = self.proof("untracked-review.txt")
        self.save_receipt(receipt)
        self.check(False, "untracked-review.txt")

    def test_evidence_hash_drift_is_rejected_even_with_current_manifest(self):
        receipt = self.checkpoint("complete")
        self.write("docs/evidence/check.txt", "changed evidence\n")
        self.git("add", "docs/evidence/check.txt")
        for item in receipt["changed_files"]:
            if item["path"] == "docs/evidence/check.txt":
                item["sha256"] = digest((self.repo / item["path"]).read_bytes())
        self.save_receipt(receipt)
        self.check(False, "evidence SHA-256")

    def test_complete_cannot_resume_same_closed_task(self):
        receipt = self.checkpoint("complete", {"next_task_id": "TASK-1"})
        self.check(False, "complete must advance")

    def test_complete_cannot_disguise_same_task_with_whitespace(self):
        receipt = self.checkpoint("complete", {"next_task_id": " TASK-1 "})
        self.write(STATUS, (self.repo / STATUS).read_text().replace("NEXT-1", " TASK-1 "))
        self.git("add", STATUS)
        next(item for item in receipt["changed_files"] if item["path"] == STATUS)["sha256"] = digest((self.repo / STATUS).read_bytes())
        self.save_receipt(receipt)
        self.check(False, "complete must advance")

    def test_failed_checks_cannot_close_task(self):
        receipt = self.checkpoint("complete")
        receipt["checks"][0]["exit_code"] = 1
        self.save_receipt(receipt)
        self.check(False, "complete requires successful checks")

    def test_complete_requires_independent_review(self):
        self.checkpoint("complete", {"review": None})
        self.check(False, "accepted review")

    def test_blocked_checkpoint_requires_prerequisite_and_independent_action(self):
        receipt = self.checkpoint("blocked")
        self.check()
        receipt["independent_action"] = None
        self.save_receipt(receipt)
        self.check(False, "independent_action")

    def test_deletion_requires_null_manifest_hash(self):
        self.write("old.txt", "retired")
        self.git("add", "old.txt")
        self.git("commit", "-qm", "old file")
        self.base = self.git("rev-parse", "HEAD").stdout.decode().strip()
        self.git("rm", "-q", "old.txt")
        receipt = self.checkpoint()
        self.check()
        next(item for item in receipt["changed_files"] if item["path"] == "old.txt")["sha256"] = "a" * 64
        self.save_receipt(receipt)
        self.check(False, "deleted path")

    def test_malformed_receipt_and_unknown_outcome_fail_clearly(self):
        self.checkpoint()
        self.write(RECEIPT, "[]")
        self.git("add", RECEIPT)
        self.check(False, "receipt must be an object")
        self.checkpoint(extra={"outcome": "probably_done"})
        self.check(False, "outcome")

    def test_manifest_is_exact_and_rejects_duplicate_paths(self):
        receipt = self.checkpoint()
        receipt["changed_files"].append(dict(receipt["changed_files"][0]))
        self.save_receipt(receipt)
        self.check(False, "duplicate")
        receipt["changed_files"] = receipt["changed_files"][1:-1]
        self.save_receipt(receipt)
        self.check(False, "changed_files must exactly")

    def test_status_receipt_and_visible_next_action_must_agree(self):
        receipt = self.checkpoint()
        receipt["next_action"] = "A different unrecorded action."
        self.save_receipt(receipt)
        self.check(False, "next_action")

    def test_self_proof_and_unsafe_evidence_paths_rejected(self):
        receipt = self.checkpoint("complete")
        for path in (STATUS, RECEIPT, "../outside", "/absolute", "docs\\escape"):
            with self.subTest(path=path):
                receipt["review"]["evidence"] = {"path": path, "sha256": "a" * 64}
                self.save_receipt(receipt)
                self.check(False)

    def test_symlink_evidence_is_not_read(self):
        self.write("outside", "not evidence")
        os.symlink("../../outside", self.repo / "link")
        receipt = self.checkpoint("complete")
        receipt["review"]["evidence"] = {"path": "link", "sha256": digest(b"../../outside")}
        self.save_receipt(receipt)
        self.check(False, "regular file")

    def test_unmerged_index_rejected(self):
        oid = self.git("hash-object", "-w", "--stdin", input=b"conflicting").stdout.decode().strip()
        self.git("update-index", "--index-info", input=(f"0 {'0' * 40}\tscripts/example.py\n"
                 f"100644 {oid} 1\tscripts/example.py\n"
                 f"100644 {oid} 2\tscripts/example.py\n"
                 f"100644 {oid} 3\tscripts/example.py\n").encode())
        self.check(False, "unmerged index")

    def test_empty_index_and_archival_document_changes_skip(self):
        self.check()
        self.write("docs/operations/status-archive/old.md", "archival correction")
        self.git("add", ".")
        self.check()

    def test_existing_configuration_and_instruction_surfaces_are_protected(self):
        paths = (".claude/workflows/test.js", ".codex/config.toml", "governance/policy.json",
                 "deny.toml", "rustfmt.toml", ".gitignore", ".gitattributes", "ROADMAP.md", "SECURITY.md",
                 "docs/roadmap.md", "docs/policies/external-submissions.md")
        for path in paths:
            with self.subTest(path=path):
                self.git("reset", "-q", "HEAD")
                self.write(path, "candidate\n")
                self.git("add", path)
                self.check(False, "CURRENT_STATUS.md must change")

    def test_archival_exemption_does_not_hide_executable_code(self):
        self.write("docs/operations/status-archive/check.py", "print('changed')\n")
        self.git("add", ".")
        self.check(False, "CURRENT_STATUS.md must change")

    def test_local_replace_objects_cannot_change_committed_evidence(self):
        self.checkpoint("complete")
        self.git("commit", "-qm", "complete")
        commit = self.git("rev-parse", "HEAD").stdout.decode().strip()
        receipt_oid = self.git("rev-parse", f"HEAD:{RECEIPT}").stdout.decode().strip()
        replacement = self.git("hash-object", "-w", "--stdin", input=b"{}").stdout.decode().strip()
        self.git("replace", receipt_oid, replacement)
        self.check(commit=commit)

    def test_committed_mode_requires_full_commit_hash(self):
        self.checkpoint("complete")
        self.git("commit", "-qm", "complete")
        self.check(False, "full 40-character commit SHA", commit="HEAD")

    def test_base_must_be_exact_parent(self):
        self.checkpoint(extra={"base_commit": "0" * 40})
        self.check(False, "base_commit")

    def test_csi_validation_runs_only_for_selected_changed_scope(self):
        # Literal checker verifies a staged file; unstaged repair must not satisfy it.
        self.write("scripts/verify-csi-division23.py", "import pathlib, sys\n"
                   "root = pathlib.Path(sys.argv[sys.argv.index('--root') + 1])\n"
                   "sys.exit(0 if (root/'docs/plans/division-23-section-register.json').read_text() == 'valid' else 7)\n")
        self.write("docs/plans/division-23-task-contracts.json", "{}")
        self.git("add", ".")
        self.git("commit", "-qm", "checker baseline")
        self.base = self.git("rev-parse", "HEAD").stdout.decode().strip()
        self.checkpoint()
        self.check()  # No register exists; unrelated changes must not invoke checker.
        self.write("docs/plans/division-23-section-register.json", "invalid")
        self.checkpoint()
        self.write("docs/plans/division-23-section-register.json", "valid")
        self.check(False, "CSI")
        self.checkpoint()
        self.check()

    def test_actual_csi_checker_rejects_invalid_selected_register(self):
        self.write("scripts/verify-csi-division23.py", (ROOT / "scripts/verify-csi-division23.py").read_bytes())
        self.write("docs/plans/division-23-task-contracts.json", "{}")
        self.write("docs/plans/division-23-section-register.json", "{}")
        self.checkpoint()
        self.check(False, "hierarchy requires exactly one root")

    def test_actual_csi_card_prefix_triggers_checker(self):
        self.write("scripts/verify-csi-division23.py", "import sys\nsys.exit(9)\n")
        self.write("docs/plans/division-23-section-register.json", "{}")
        self.write("docs/plans/division-23-task-contracts.json", "{}")
        self.git("add", ".")
        self.git("commit", "-qm", "checker baseline")
        self.base = self.git("rev-parse", "HEAD").stdout.decode().strip()
        self.write("docs/superpowers/plans/division23-sections/csi-23-21-23.md", "Changed card")
        self.checkpoint()
        self.check(False, "CSI selected-tree check failed (exit 9)")

    def test_root_commit_uses_empty_tree_as_base(self):
        # A separate repository has no parent commit or inherited Git objects.
        fresh = self.repo / "fresh"
        fresh.mkdir()
        self.repo = fresh
        self.git("init", "-q")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        self.base = self.git("hash-object", "-t", "tree", "--stdin", input=b"").stdout.decode().strip()
        self.checkpoint()
        self.check()
        self.git("commit", "-qm", "first checkpoint")
        self.check(commit=self.git("rev-parse", "HEAD").stdout.decode().strip())


if __name__ == "__main__":
    unittest.main()
