"""Evidence and resume guards judged from real Git indexes, trees and receipt history.

Structural validation is not source-truth certification. These checks prove that
pinned research records resolve to exact Git bytes and that a closed task cannot be
resumed silently; independent human review still decides whether a finding is true.
"""

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
POLICY = "docs/operations/checkpoint-evidence-policy.json"
PACKET = "docs/operations/notebooklm-findings-fixture.json"
SOURCE = "docs/research/notebooklm/duct-source.txt"
QUERY = "docs/research/notebooklm/duct-query.json"
VERIFICATION = "docs/research/notebooklm/duct-verification.json"
CHECKS = "docs/operations/fixture-checks.txt"
REVIEW = "docs/operations/fixture-review.txt"
REOPEN = "docs/operations/fixture-reopen.txt"
RETIRED = "docs/notes/retired.md"
POLICY_BYTES = json.dumps({"schema_version": 1, "require_notebooklm_evidence": True,
                           "reject_completed_resume": True}, indent=2) + "\n"
RESEARCH_FIELDS = ("applicability", "behavior_and_checks", "gaps_and_next_action",
                   "external_submissions")
SOURCE_TEXT = ("Notebook source fixture body.\n"
               "Duct length is measured along the centerline of the run.\n"
               "Fittings are counted separately from straight lengths.\n")
PASSAGE = "measured along the centerline of the run"
ABSENT_PASSAGE = "measured along the outside corner of the run"


def digest(data):
    return hashlib.sha256(data).hexdigest()


class Fixture:
    """Build honest candidates in a real repository; never read the worktree as evidence."""

    adopt_policy = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.serial = 0
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        self.git("config", "commit.gpgsign", "false")
        self.write(".gitignore", "scratch/\n")
        self.write("README.md", "baseline\n")
        self.write("scripts/example.py", "baseline\n")
        self.write(STATUS, "initial status\n")
        self.write(RETIRED, "retired note\n")
        self.write(SOURCE, SOURCE_TEXT)
        self.write(QUERY, '{"question": "Which duct length rule applies?"}\n')
        self.write(VERIFICATION, '{"verified_against_source": true}\n')
        self.write(CHECKS, "fixture checks passed\n")
        self.write(REVIEW, "independent reviewer accepted\n")
        self.write(REOPEN, "owner recorded exact-byte drift in the closed task\n")
        self.packet()
        if self.adopt_policy:
            self.write(POLICY, POLICY_BYTES)
        self.git("add", "-A")
        self.git("commit", "-qm", "baseline")
        self.base = self.rev("HEAD")

    def git(self, *args, input=None, check=True):
        return subprocess.run(["git", "-C", str(self.repo), *args], input=input,
                              capture_output=True, check=check)

    def rev(self, revision):
        return self.git("rev-parse", revision).stdout.decode().strip()

    def write(self, path, value):
        destination = self.repo / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(value.encode() if isinstance(value, str) else value)

    def proof(self, path):
        return {"path": path, "sha256": digest((self.repo / path).read_bytes())}

    def check(self, good=True, message=None, commit=None):
        command = [sys.executable, str(CHECKER), "--repo", str(self.repo)]
        command += ["--commit", commit] if commit else ["--staged"]
        result = subprocess.run(command, capture_output=True, text=True)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0 if good else 1, output)
        if message:
            self.assertIn(message, output)
        return output

    def finding(self, **overrides):
        value = {"id": "F-1", "notebook_id": "notebook-fixture", "source_id": "source-fixture",
                 "locator": "Indexed source character 30",
                 "finding": "Duct length follows the centerline of the run.",
                 "passage": PASSAGE, "verification_status": "verified_applicable",
                 "behaviors": ["Guard requires pinned verified findings for active receipts."],
                 "checks": ["tests/test_checkpoint_evidence.py"],
                 "source": self.proof(SOURCE)}
        value.update(overrides)
        return value

    def packet(self, findings=None, **overrides):
        value = {"schema_version": 1, "kind": "notebooklm_verified_findings",
                 "query": self.proof(QUERY), "verification": self.proof(VERIFICATION),
                 "findings": [self.finding()] if findings is None else findings}
        value.update(overrides)
        self.write(PACKET, json.dumps(value, indent=2) + "\n")
        return value

    def research(self, disposition="new_verified", records=None, **overrides):
        value = {"disposition": disposition,
                 "records": [self.proof(PACKET)] if records is None else records,
                 "applicability": "Covers the fixture duct length rule and its edition limit.",
                 "behavior_and_checks": "Informed the guard behavior and its unit checks.",
                 "gaps_and_next_action": "No research gaps remain for this bounded fixture.",
                 "external_submissions": "Reused verified findings; no new external submission."}
        value.update(overrides)
        return value

    def manifest(self):
        raw = self.git("diff", "--cached", "--name-only", "--no-renames", "-z", self.base).stdout
        files = []
        for record in raw.split(b"\0"):
            if not record:
                continue
            path = record.decode()
            if path == RECEIPT:
                continue
            blob = self.git("show", ":" + path, check=False)
            files.append({"path": path,
                          "sha256": digest(blob.stdout) if blob.returncode == 0 else None})
        return files

    def save_receipt(self, receipt, manifest=True):
        if manifest:
            receipt["changed_files"] = self.manifest()
        self.write(RECEIPT, json.dumps(receipt, indent=2) + "\n")
        self.git("add", RECEIPT)

    def checkpoint(self, outcome="in_progress", task="TASK-1", next_task=None,
                   research=True, code=True, extra=None):
        if next_task is None:
            next_task = task if outcome == "in_progress" else "NEXT-1"
        self.serial += 1
        if code:
            self.write("scripts/example.py", f"candidate = {self.serial}\n")
        action = f"Build bounded capability {self.serial}."
        marker = {"schema_version": 1, "receipt": RECEIPT, "task_id": task, "outcome": outcome,
                  "next_task_id": next_task, "next_action": action}
        self.write(STATUS, f"# Status\n\n**Primary product task: {next_task}.** Details.\n\n"
                   f"**Next executable action:** {action}\n\n"
                   "<!-- build-checkpoint:v1 -->\n```json\n" + json.dumps(marker) +
                   "\n```\n<!-- /build-checkpoint -->\n")
        self.git("add", "-A")
        receipt = {"schema_version": 1, "task_id": task, "outcome": outcome,
                   "base_commit": self.base, "summary": "Bounded delivery checkpoint.",
                   "remaining_limits": ["Representative acceptance remains open."],
                   "next_task_id": next_task, "next_action": action,
                   "changed_files": [], "checks": [], "review": None,
                   "missing_prerequisite": None, "independent_action": None}
        if outcome == "complete":
            receipt["checks"] = [{"command": "python3 -m unittest", "exit_code": 0,
                                  "evidence": self.proof(CHECKS)}]
            receipt["review"] = {"reviewer": "Independent reviewer", "outcome": "accepted",
                                 "evidence": self.proof(REVIEW)}
        if outcome == "blocked":
            receipt.update(missing_prerequisite="The notebook connector is unavailable.",
                           independent_action="Continue the ready export task.")
        if research:
            receipt["notebooklm_research"] = self.research() if research is True else research
        if extra:
            receipt.update(extra)
        self.save_receipt(receipt)
        return receipt

    def advance(self, message, **kwargs):
        """Commit one accepted checkpoint so its exact receipt bytes enter history."""
        self.checkpoint(**kwargs)
        self.check()
        self.git("commit", "-qm", message)
        self.base = self.rev("HEAD")

    def close_task(self, task, next_task):
        self.advance(f"complete {task}", outcome="complete", task=task, next_task=next_task)


class ResearchEvidenceTests(Fixture, unittest.TestCase):
    def test_valid_new_and_reused_verified_checkpoints_pass(self):
        for disposition in ("new_verified", "reused_verified"):
            with self.subTest(disposition=disposition):
                self.checkpoint(research=self.research(disposition))
                self.check()

    def test_complete_checkpoint_carries_verified_research(self):
        self.checkpoint("complete")
        self.check()

    def test_missing_research_object_is_rejected(self):
        self.checkpoint(research=False)
        self.check(False, "notebooklm_research must be an object")
        self.checkpoint(research="new_verified")
        self.check(False, "notebooklm_research must be an object")

    def test_research_fields_must_be_explicit(self):
        for field in RESEARCH_FIELDS:
            with self.subTest(field=field):
                self.checkpoint(research=self.research(**{field: "   "}))
                self.check(False, f"notebooklm_research {field} must be a nonempty string")

    def test_unknown_disposition_and_malformed_records_are_rejected(self):
        self.checkpoint(research=self.research("used_notebooklm"))
        self.check(False, "disposition must be one of")
        self.checkpoint(research=self.research(records=PACKET))
        self.check(False, "records must be an array")
        self.checkpoint(research=self.research(records=["docs/operations/fixture-checks.txt"]))
        self.check(False, "evidence must be a path/SHA-256 object")

    def test_verified_disposition_requires_a_pinned_record(self):
        for disposition in ("new_verified", "reused_verified"):
            with self.subTest(disposition=disposition):
                self.checkpoint(research=self.research(disposition, records=[]))
                self.check(False, "requires at least one pinned verified findings record")

    def test_findings_packet_requires_query_and_verification_proofs(self):
        for field in ("query", "verification"):
            with self.subTest(field=field):
                packet = self.packet()
                packet.pop(field)
                self.write(PACKET, json.dumps(packet) + "\n")
                self.checkpoint()
                self.check(False, f"records[0].{field}: evidence must be a path/SHA-256 object")

    def test_findings_packet_cannot_cite_itself(self):
        for field in ("query", "verification"):
            with self.subTest(field=field):
                self.packet(**{field: {"path": PACKET, "sha256": "0" * 64}})
                self.checkpoint()
                self.check(False, "cannot cite itself as evidence")
        self.packet(findings=[self.finding(source={"path": PACKET, "sha256": "0" * 64})])
        self.checkpoint()
        self.check(False, "cannot cite itself as evidence")

    def test_arbitrary_log_cannot_serve_as_a_findings_packet(self):
        self.write(PACKET, "2026-09-22 notebook query finished with 3 answers\n")
        self.checkpoint()
        self.check(False, "malformed JSON")

    def test_malformed_packet_structures_fail_cleanly(self):
        cases = [({"schema_version": 2}, "findings schema_version must be 1"),
                 ({"schema_version": True}, "findings schema_version must be 1"),
                 ({"kind": "notebooklm_notes"}, "findings kind must be notebooklm_verified_findings"),
                 ({"findings": []}, "findings must be a nonempty array"),
                 ({"findings": {"id": "F-1"}}, "findings must be a nonempty array"),
                 ({"findings": ["F-1"]}, "findings[0] must be an object")]
        for overrides, message in cases:
            with self.subTest(message=message):
                self.packet(**overrides)
                self.checkpoint()
                self.check(False, message)

    def test_malformed_findings_fail_cleanly(self):
        cases = [({"id": "  "}, "findings[0] id must be a nonempty string"),
                 ({"notebook_id": None}, "findings[0] notebook_id must be a nonempty string"),
                 ({"source_id": 7}, "findings[0] source_id must be a nonempty string"),
                 ({"locator": ""}, "findings[0] locator must be a nonempty string"),
                 ({"finding": ""}, "findings[0] finding must be a nonempty string"),
                 ({"verification_status": "unsupported"},
                  "verification_status must be verified_applicable"),
                 ({"behaviors": []}, "behaviors must be a nonempty array"),
                 ({"behaviors": "one behavior"}, "behaviors must be a nonempty array"),
                 ({"checks": ["   "]}, "checks must be a nonempty array")]
        for overrides, message in cases:
            with self.subTest(message=message):
                self.packet(findings=[self.finding(**overrides)])
                self.checkpoint()
                self.check(False, message)

    def test_duplicate_finding_identifiers_are_rejected(self):
        self.packet(findings=[self.finding(), self.finding(locator="Indexed source character 60")])
        self.checkpoint()
        self.check(False, "duplicate finding id F-1")

    def test_passage_must_occur_verbatim_in_the_pinned_source(self):
        self.packet(findings=[self.finding(passage=ABSENT_PASSAGE)])
        self.checkpoint()
        self.check(False, f"passage does not occur verbatim in {SOURCE}")

    def test_unstaged_source_repair_cannot_supply_a_missing_passage(self):
        self.packet(findings=[self.finding(passage=ABSENT_PASSAGE)])
        self.checkpoint()
        self.write(SOURCE, SOURCE_TEXT + ABSENT_PASSAGE + "\n")
        self.check(False, "does not occur verbatim")

    def test_worktree_only_source_cannot_support_a_finding(self):
        self.write("scratch/source.txt", SOURCE_TEXT)
        self.packet(findings=[self.finding(source=self.proof("scratch/source.txt"))])
        self.checkpoint()
        self.check(False, "scratch/source.txt: missing from selected Git index/tree")

    def test_absent_source_path_is_rejected(self):
        self.packet(findings=[self.finding(source={"path": "docs/research/notebooklm/absent.txt",
                                                   "sha256": digest(SOURCE_TEXT.encode())})])
        self.checkpoint()
        self.check(False, "missing from selected Git index/tree")

    def test_symlink_evidence_is_not_read(self):
        os.symlink("../../../README.md", self.repo / "docs/research/notebooklm/link.txt")
        self.packet(findings=[self.finding(source={"path": "docs/research/notebooklm/link.txt",
                                                   "sha256": digest(b"../../../README.md")})])
        self.checkpoint()
        self.check(False, "must be a regular file in selected Git index/tree")

    def test_wrong_hashes_are_rejected_for_records_and_sources(self):
        receipt = self.checkpoint()
        receipt["notebooklm_research"]["records"][0]["sha256"] = "a" * 64
        self.save_receipt(receipt)
        self.check(False, f"evidence SHA-256 does not match selected Git bytes: {PACKET}")
        self.packet(findings=[self.finding(source={"path": SOURCE, "sha256": "b" * 64})])
        self.checkpoint()
        self.check(False, f"evidence SHA-256 does not match selected Git bytes: {SOURCE}")

    def test_records_cannot_cite_the_status_or_receipt(self):
        for path in (RECEIPT, STATUS):
            with self.subTest(path=path):
                receipt = self.checkpoint()
                receipt["notebooklm_research"]["records"] = [{"path": path, "sha256": "a" * 64}]
                self.save_receipt(receipt)
                self.check(False, "status/receipt cannot serve as self-proof")

    def test_unsafe_record_paths_are_rejected(self):
        for path in ("../outside.json", "/absolute.json", "docs\\escape.json", ".git/config"):
            with self.subTest(path=path):
                receipt = self.checkpoint()
                receipt["notebooklm_research"]["records"] = [{"path": path, "sha256": "a" * 64}]
                self.save_receipt(receipt)
                self.check(False, "unsafe evidence path")

    def test_non_utf8_source_fails_cleanly(self):
        binary = "docs/research/notebooklm/binary-source.txt"
        self.write(binary, b"\xff\xfe raw indexed bytes")
        self.packet(findings=[self.finding(source=self.proof(binary))])
        self.checkpoint()
        self.check(False, "must be UTF-8 text")

    def test_unavailable_research_cannot_close_a_task(self):
        self.checkpoint("complete", research=self.research("unavailable", records=[]))
        self.check(False, "unavailable research cannot close a task as complete")

    def test_unavailable_research_requires_prerequisite_and_independent_action(self):
        self.checkpoint(research=self.research("unavailable", records=[]))
        self.check(False, "unavailable research requires receipt missing_prerequisite")
        self.checkpoint("blocked", research=self.research("unavailable", records=[]),
                        extra={"independent_action": None})
        self.check(False, "unavailable research requires receipt independent_action")

    def test_honest_blocked_unavailable_checkpoint_passes(self):
        self.checkpoint("blocked", research=self.research("unavailable", records=[]))
        self.check()

    def test_unavailable_records_are_still_validated_when_supplied(self):
        self.checkpoint("blocked", research=self.research(
            "unavailable", records=[{"path": PACKET, "sha256": "c" * 64}]))
        self.check(False, "evidence SHA-256 does not match")

    def test_administrative_documentation_checkpoint_passes(self):
        self.write("docs/notes/new.md", "queue maintenance\n")
        self.write("docs/operations/fixture-note.txt", "transcribed owner instruction\n")
        self.checkpoint(code=False, research=self.research("administrative_no_new_claims", records=[]))
        self.check()

    def test_administrative_disposition_rejects_changed_code(self):
        self.checkpoint(research=self.research("administrative_no_new_claims", records=[]))
        self.check(False, "administrative_no_new_claims cannot change scripts/example.py")

    def test_administrative_disposition_rejects_executable_document(self):
        path = "docs/operations/executable.md"
        self.write(path, "#!/bin/sh\necho executable\n")
        (self.repo / path).chmod(0o755)
        self.checkpoint(code=False, research=self.research("administrative_no_new_claims", records=[]))
        self.check(False, "requires non-executable regular file")

    def test_administrative_disposition_rejects_deletions(self):
        self.git("rm", "-q", RETIRED)
        self.checkpoint(code=False, research=self.research("administrative_no_new_claims", records=[]))
        self.check(False, f"administrative_no_new_claims cannot delete {RETIRED}")

    def test_administrative_disposition_rejects_non_documentation_paths(self):
        extra = "docs/research/notebooklm/extra.json"
        self.write(extra, "{}\n")
        self.checkpoint(code=False, research=self.research("administrative_no_new_claims", records=[]))
        self.check(False, f"administrative_no_new_claims cannot change {extra}")

    def test_administrative_records_need_not_be_findings_packets(self):
        self.write("docs/notes/new.md", "queue maintenance\n")
        self.checkpoint(code=False, research=self.research(
            "administrative_no_new_claims", records=[self.proof(RETIRED)]))
        self.check()

    def test_administrative_records_are_still_proofs(self):
        self.write("docs/notes/new.md", "queue maintenance\n")
        self.checkpoint(code=False, research=self.research(
            "administrative_no_new_claims", records=[{"path": RETIRED, "sha256": "d" * 64}]))
        self.check(False, "evidence SHA-256 does not match")


class PolicyIdentityTests(Fixture, unittest.TestCase):
    def test_inherited_policy_cannot_be_removed(self):
        self.git("rm", "-q", POLICY)
        self.checkpoint()
        self.check(False, "an adopted evidence policy cannot be removed")

    def test_policy_removal_does_not_disable_evidence_enforcement(self):
        self.git("rm", "-q", POLICY)
        self.checkpoint(research=False)
        output = self.check(False, "an adopted evidence policy cannot be removed")
        self.assertIn("notebooklm_research must be an object", output)

    def test_downgraded_policy_is_rejected(self):
        cases = [{"schema_version": 1, "require_notebooklm_evidence": False,
                  "reject_completed_resume": True},
                 {"schema_version": 1, "require_notebooklm_evidence": True,
                  "reject_completed_resume": False},
                 {"schema_version": 1, "require_notebooklm_evidence": True},
                 {"schema_version": 2, "require_notebooklm_evidence": True,
                  "reject_completed_resume": True},
                 {"schema_version": 1, "require_notebooklm_evidence": "true",
                  "reject_completed_resume": True},
                 {"schema_version": True, "require_notebooklm_evidence": True,
                  "reject_completed_resume": True},
                 {"schema_version": 1, "require_notebooklm_evidence": True,
                  "reject_completed_resume": True, "enforce": False}]
        for value in cases:
            with self.subTest(policy=value):
                self.write(POLICY, json.dumps(value) + "\n")
                self.checkpoint()
                self.check(False, f"{POLICY} must be exactly")

    def test_malformed_policy_fails_cleanly(self):
        for value in ("not json\n", "[]\n", "\n"):
            with self.subTest(value=value):
                self.write(POLICY, value)
                self.checkpoint()
                self.check(False, POLICY)

    def test_unchanged_valid_policy_keeps_enforcement_active(self):
        self.checkpoint()
        self.check()
        self.checkpoint(research=False)
        self.check(False, "notebooklm_research must be an object")


class ResumeGuardTests(Fixture, unittest.TestCase):
    def reopen(self, **overrides):
        value = {"task_id": "TASK-A", "reason": "Owner recorded exact-byte drift in TASK-A.",
                 "evidence": self.proof(REOPEN)}
        value.update(overrides)
        return value

    def test_new_task_after_completion_passes(self):
        self.close_task("TASK-A", "TASK-B")
        self.checkpoint(task="TASK-B", next_task="TASK-C")
        self.check()

    def test_completed_task_cannot_be_resumed_silently(self):
        self.close_task("TASK-A", "TASK-B")
        self.checkpoint(task="TASK-B", next_task="TASK-A")
        self.check(False, "next_task_id TASK-A repeats a completed task")

    def test_older_completed_task_is_still_remembered(self):
        self.close_task("TASK-A", "TASK-B")
        self.advance("bounded progress", task="TASK-B")
        self.advance("further progress", task="TASK-B")
        self.checkpoint(task="TASK-B", next_task=" TASK-A ")
        self.check(False, "repeats a completed task")

    def test_valid_reopen_record_permits_a_deliberate_restart(self):
        self.close_task("TASK-A", "TASK-B")
        self.checkpoint(task="TASK-B", next_task="TASK-A",
                        extra={"next_task_reopen": self.reopen()})
        self.check()

    def test_malformed_reopen_records_are_rejected(self):
        self.close_task("TASK-A", "TASK-B")
        cases = [(self.reopen(task_id="TASK-C"), "next_task_reopen task_id must match"),
                 (self.reopen(reason="   "), "next_task_reopen reason must be a nonempty string"),
                 (self.reopen(evidence={"path": REOPEN, "sha256": "e" * 64}),
                  "evidence SHA-256 does not match"),
                 (self.reopen(evidence={"path": RECEIPT, "sha256": "e" * 64}),
                  "status/receipt cannot serve as self-proof"),
                 (self.reopen(evidence="docs/operations/fixture-reopen.txt"),
                  "next_task_reopen: evidence must be a path/SHA-256 object"),
                 ("reopened by the owner", "next_task_reopen must be a task_id/reason/evidence object")]
        for reopen, message in cases:
            with self.subTest(message=message):
                self.checkpoint(task="TASK-B", next_task="TASK-A",
                                extra={"next_task_reopen": reopen})
                self.check(False, message)

    def test_unnecessary_reopen_record_is_still_validated(self):
        self.checkpoint(task="TASK-B", next_task="TASK-C",
                        extra={"next_task_reopen": self.reopen(task_id="TASK-A")})
        self.check(False, "next_task_reopen task_id must match")

    def test_complete_still_cannot_name_its_own_task_as_next(self):
        self.checkpoint("complete", task="TASK-A", next_task="TASK-A")
        self.check(False, "complete must advance next_task_id")

    def test_blocked_checkpoint_cannot_name_itself_as_next(self):
        self.checkpoint("blocked", task="TASK-1", next_task="TASK-1")
        self.check(False, "must name an independent next_task_id")

    def test_deleted_historical_receipt_is_treated_as_absence(self):
        self.close_task("TASK-A", "TASK-B")
        self.git("rm", "-q", RECEIPT)
        self.git("commit", "-qm", "receipt removed from history")
        self.base = self.rev("HEAD")
        self.checkpoint(task="TASK-B", next_task="TASK-C")
        self.check()
        self.checkpoint(task="TASK-B", next_task="TASK-A")
        self.check(False, "repeats a completed task")

    def test_malformed_historical_receipt_fails_cleanly(self):
        self.close_task("TASK-A", "TASK-B")
        self.write(RECEIPT, "{ not a receipt\n")
        self.git("add", RECEIPT)
        self.git("commit", "-qm", "corrupted receipt")
        self.base = self.rev("HEAD")
        self.checkpoint(task="TASK-B", next_task="TASK-C")
        self.check(False, "malformed JSON")

    def test_committed_tree_selection_enforces_both_guards(self):
        self.close_task("TASK-A", "TASK-B")
        self.checkpoint(task="TASK-B", next_task="TASK-A")
        self.git("commit", "-qm", "silent resume")
        commit = self.rev("HEAD")
        self.write(RECEIPT, "unstaged repair that must not be read")
        self.write(POLICY, "unstaged policy removal that must not be read")
        self.check(False, "repeats a completed task", commit=commit)

    def test_merge_history_preserves_completed_task_identities(self):
        self.git("checkout", "-qb", "side")
        self.close_task("TASK-A", "TASK-B")
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff", "side", "-m", "merge side")
        self.base = self.rev("HEAD")
        self.checkpoint(task="TASK-B", next_task="TASK-A")
        self.check(False, "repeats a completed task")
        self.checkpoint(task="TASK-B", next_task="TASK-A",
                        extra={"next_task_reopen": self.reopen()})
        self.check()

    def test_root_commit_without_history_passes(self):
        fresh = self.repo / "fresh"
        fresh.mkdir()
        self.repo = fresh
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        self.write(SOURCE, SOURCE_TEXT)
        self.write(QUERY, '{"question": "Which duct length rule applies?"}\n')
        self.write(VERIFICATION, '{"verified_against_source": true}\n')
        self.packet()
        self.write(POLICY, POLICY_BYTES)
        self.base = self.git("hash-object", "-t", "tree", "--stdin",
                             input=b"").stdout.decode().strip()
        self.checkpoint()
        self.check()


class LegacyCompatibilityTests(Fixture, unittest.TestCase):
    adopt_policy = False

    def test_receipt_without_research_still_passes(self):
        self.checkpoint(research=False)
        self.check()

    def test_legacy_research_field_is_not_reinterpreted(self):
        self.checkpoint(research="NotebookLM findings reused; see the task ledger.")
        self.check()
        self.checkpoint(research=self.research("used_notebooklm", records=["not a proof"]))
        self.check()

    def test_completed_task_may_be_repeated_without_the_policy(self):
        self.close_task("TASK-A", "TASK-B")
        self.checkpoint(task="TASK-B", next_task="TASK-A", research=False)
        self.check()

    def test_blocked_checkpoint_may_name_itself_without_the_policy(self):
        self.checkpoint("blocked", task="TASK-1", next_task="TASK-1", research=False)
        self.check()

    def test_staged_policy_adoption_activates_enforcement(self):
        self.write(POLICY, POLICY_BYTES)
        self.checkpoint(research=False)
        self.check(False, "notebooklm_research must be an object")
        self.checkpoint()
        self.check()

    def test_policy_adoption_cannot_be_administrative(self):
        self.write(POLICY, POLICY_BYTES)
        self.checkpoint(code=False, research=self.research(
            "administrative_no_new_claims", records=[]))
        self.check(False, f"administrative_no_new_claims cannot change {POLICY}")

    def test_empty_staging_is_unchanged_by_the_guard(self):
        self.check()
        self.write("docs/operations/status-archive/old.md", "archival correction\n")
        self.git("add", "-A")
        self.check()


if __name__ == "__main__":
    unittest.main()
