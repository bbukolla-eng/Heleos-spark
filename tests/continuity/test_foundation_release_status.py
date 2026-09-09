"""Release gate contracts through the CLI and real disposable Git repositories.

All receipts and owner decisions below are synthetic test inputs. No fixture is
native execution, fetched CI evidence, or authorization for the real repository.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/foundation-release-status.py"
GATES = ["github_apps", "workflow_candidate", "owner_git_handoff",
         "same_sha_platforms", "acceptance_dossier"]
APPS = ["Azure Pipelines", "AWS Connector for GitHub", "Amazon Q Developer", "ECC Tools"]
REMOTE = "https://github.com/bbukolla-eng/Heleos-spark"
DOSSIER = "docs/verification/foundation-0.1.md"


class FoundationReleaseStatusTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="heleos-release-status-")
        self.addCleanup(scratch.cleanup)
        self.base = Path(scratch.name).resolve()
        self.repo = self.base / "visible repo"
        self.repo.mkdir()
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("GIT_")}
        self.env.update({
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Synthetic Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Synthetic Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "GIT_AUTHOR_DATE": "2026-09-09T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-09-09T00:00:00Z",
            "GIT_OPTIONAL_LOCKS": "0",
        })
        self.git("init", "--initial-branch=main")
        self.git("config", "core.autocrlf", "false")
        self.write(".gitignore", "WINDOWS_NATIVE_HANDOFF*/\n.worktrees/\nevidence.json\n")
        self.write(".github/workflows/core-ci.yml", "name: synthetic fixture\non: workflow_dispatch\n")
        self.write("scripts/import-foundation-native-candidate.ps1", "# synthetic trusted importer\n")
        self.write("scripts/verify-supply-chain.ps1", "# synthetic native entry point\n")
        self.set_apps("owner_decision_required")
        self.commit("synthetic candidate")
        self.candidate = self.git("rev-parse", "HEAD").strip()
        self.branch = "release/foundation-0.1-native-" + self.candidate
        self.git("branch", self.branch)
        self.handoff = self.repo / "WINDOWS_NATIVE_HANDOFF_test"
        self.handoff.mkdir()
        self.bundle = self.handoff / ("candidate/heleos-spark-" + self.candidate + ".bundle")
        self.bundle.parent.mkdir()
        self.git("bundle", "create", str(self.bundle), "refs/heads/" + self.branch)
        self.manifest = {
            "schema": "heleos.foundation-windows-native-candidate/v1",
            "branch": self.branch, "commit": self.candidate,
            "bundle": self.bundle.name, "bundle_sha256": self.digest(self.bundle),
            "bundle_bytes": self.bundle.stat().st_size,
            "native_gate": "scripts/verify-supply-chain.ps1",
            "required_filesystem": "NTFS", "native_gate_status": "pending",
        }
        self.manifest_path = self.handoff / "candidate/candidate.json"
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        (self.handoff / "candidate/README.md").write_text("Synthetic transfer only.\n")
        (self.handoff / "HANDOFF.md").write_text("Synthetic transfer, native evidence pending.\n")
        (self.handoff / "scripts").mkdir()
        shutil.copyfile(self.repo / "scripts/import-foundation-native-candidate.ps1",
                        self.handoff / "scripts/import-foundation-native-candidate.ps1")
        self.refresh_checksums()
        self.evidence_path = self.repo / "evidence.json"

    def git(self, *args, cwd=None):
        return subprocess.run(["git", "-C", str(cwd or self.repo), *args],
                              env=self.env, check=True, capture_output=True, text=True).stdout

    def write(self, path, value):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(value, encoding="utf-8")
        return target

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-qm", message)

    @staticmethod
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def refresh_checksums(self):
        paths = ["candidate/candidate.json", "candidate/README.md",
                 "candidate/" + self.bundle.name,
                 "scripts/import-foundation-native-candidate.ps1"]
        text = "".join(self.digest(self.handoff / path) + "  " + path + "\n" for path in paths)
        (self.handoff / "SHA256SUMS.txt").write_text(text, encoding="utf-8")

    def set_apps(self, disposition="remove", names=None):
        records = []
        for name in APPS if names is None else names:
            fields = {
                "name": name, "origin": "synthetic installation inventory",
                "version_or_digest": "service-release-2026.09.1",
                "license_or_rights": "reviewed service terms", "data_class": "PROJECT_CONFIDENTIAL",
                "owner": "repository owner", "permissions": "metadata:read",
                "egress": "prohibited by owner decision", "evaluation": "reviewed against repository policy",
                "rollback": "owner removes installation", "disposition": disposition,
            }
            if disposition != "owner_decision_required":
                fields.update({"decision_owner": "Bekim Bukolla", "decision_date": "2026-09-09",
                               "decision_evidence": "evidence/synthetic-owner-decision.md",
                               "installation_evidence": "evidence/synthetic-installation.json"})
            records.append("[[github_app]]\n" + "".join(key + " = " + json.dumps(value) + "\n"
                                                       for key, value in fields.items()))
        return self.write("governance/github-apps.toml", "\n".join(records))

    def ledger(self):
        sha = self.candidate
        return {
            "schema": "heleos.foundation-release-evidence/v1", "release_candidate_sha": sha,
            "workflow": {"status": "pass", "candidate_sha": sha,
                         "path": ".github/workflows/core-ci.yml",
                         "blob_sha1": self.git("rev-parse", sha + ":.github/workflows/core-ci.yml").strip(),
                         "local_supply_chain": {"status": "pass", "candidate_sha": sha,
                                                "platform": "macos-arm64", "receipt_sha256": "1" * 64}},
            "git_handoff": {"status": "pass", "candidate_sha": sha,
                            "authorization_reference": REMOTE + "/issues/42#issuecomment-123",
                            "push_reference": REMOTE + "/commit/" + sha},
            "platforms": {"candidate_sha": sha,
                          "macos": {"status": "pass", "candidate_sha": sha, "platform": "macos-arm64",
                                    "run_url": REMOTE + "/actions/runs/123", "artifact_sha256": "2" * 64},
                          "windows": {"status": "pass", "candidate_sha": sha, "platform": "windows-x86_64",
                                      "filesystem": "NTFS", "suite_count": 7,
                                      "run_url": REMOTE + "/actions/runs/456", "artifact_sha256": "3" * 64,
                                      "native_receipt_sha256": "4" * 64}},
        }

    def put_evidence(self, ledger=None):
        self.evidence_path.write_text(json.dumps(self.ledger() if ledger is None else ledger), encoding="utf-8")

    def prepare_ready(self):
        self.set_apps()
        self.commit("synthetic owner dispositions")
        # The old native transfer is informational. Freeze the later release
        # candidate only after its own committed App dispositions are resolved.
        self.candidate = self.git("rev-parse", "HEAD").strip()
        self.put_evidence()

    def dossier_text(self, **changes):
        data = {"schema": "heleos.foundation-acceptance/v1", "status": "accepted",
                "release_candidate_sha": self.candidate, "decision_owner": "Bekim Bukolla",
                "accepted_at": "2026-09-09T12:00:00Z"}
        data.update(changes)
        return ("# Synthetic acceptance fixture\n<!-- foundation-acceptance:v1 -->\n```json\n"
                + json.dumps(data) + "\n```\n<!-- /foundation-acceptance -->\n")

    def probe(self, *args, evidence=False, explicit_handoff=True, cwd=None):
        self.assertTrue(SCRIPT.is_file(), "foundation-release-status.py is not implemented yet")
        command = [sys.executable, "-B", str(SCRIPT)]
        if explicit_handoff:
            command += ["--handoff", str(self.handoff)]
        if evidence:
            command += ["--evidence", str(self.evidence_path)]
        result = subprocess.run(command + list(args), cwd=cwd or self.repo, env=self.env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.stderr, "", result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual([gate["id"] for gate in report["gates"]], GATES)
        self.assertIn(report["status"], {"FAIL", "BLOCKED", "READY_FOR_DOSSIER", "ACCEPTED"})
        return result.returncode, report

    def assert_status(self, expected, code, **kwargs):
        actual_code, report = self.probe(**kwargs)
        self.assertEqual(actual_code, code, report)
        self.assertEqual(report["status"], expected, report)
        return report

    def hashes(self):
        return {str(path.relative_to(self.base)): self.digest(path)
                for path in self.base.rglob("*") if path.is_file()}

    def test_pending_four_apps_blocks_with_ordered_actionable_first_blocker(self):
        report = self.assert_status("BLOCKED", 0)
        self.assertEqual(report["blockers"][0], "github_apps")
        self.assertTrue(report["gates"][0]["message"].strip())
        self.assertIn("owner", report["gates"][0]["message"].lower())
        code, strict = self.probe("--require-ready")
        self.assertEqual(code, 2, strict)
        self.assertEqual(strict["status"], "BLOCKED")

    def test_valid_pending_transfer_never_claims_workflow_or_native_execution(self):
        report = self.assert_status("BLOCKED", 0)
        self.assertFalse(report["transfer"]["native_evidence"])
        self.assertEqual(report["transfer"]["candidate_sha"], self.candidate)
        self.assertEqual(report["transfer"]["manifest_sha256"], self.digest(self.manifest_path))
        for gate in report["gates"][1:4]:
            self.assertNotEqual(gate["status"], "PASS", gate)

    def test_missing_handoff_blocks_and_multiple_handoffs_fail(self):
        saved = self.base / "saved handoff"
        self.handoff.rename(saved)
        report = self.assert_status("BLOCKED", 0, explicit_handoff=False)
        self.assertIn("owner_git_handoff", report["blockers"])
        saved.rename(self.handoff)
        shutil.copytree(self.handoff, self.repo / "WINDOWS_NATIVE_HANDOFF_other")
        self.assert_status("FAIL", 1, explicit_handoff=False)

    def test_worker_package_does_not_make_foundation_discovery_ambiguous(self):
        worker = self.repo / "WINDOWS_NATIVE_HANDOFF_worker"
        shutil.copytree(self.handoff, worker)
        manifest = dict(self.manifest, schema="heleos.windows-native-candidate/v1",
                        native_gate="scripts/verify-windows-worker-containment.ps1")
        (worker / "candidate/candidate.json").write_text(json.dumps(manifest))
        report = self.assert_status("BLOCKED", 0, explicit_handoff=False)
        self.assertEqual(report["transfer"]["candidate_sha"], self.candidate)

    def test_valid_ledger_can_be_ready_without_optional_local_transfer(self):
        self.prepare_ready()
        self.handoff.rename(self.base / "saved handoff")
        report = self.assert_status("READY_FOR_DOSSIER", 0, evidence=True, explicit_handoff=False)
        self.assertEqual(report["transfer"]["status"], "MISSING")
        self.assertEqual([gate["status"] for gate in report["gates"][:4]], ["PASS"] * 4)

    def test_uncommitted_owner_dispositions_cannot_pass_apps_gate(self):
        self.set_apps()
        report = self.assert_status("FAIL", 1)
        self.assertNotEqual(report["gates"][0]["status"], "PASS")

    def test_missing_or_malformed_or_duplicate_apps_fail_closed(self):
        for names in ([], APPS[:-1], APPS + [APPS[0]], APPS + ["Unlisted App"]):
            with self.subTest(names=names):
                self.set_apps(names=names)
                self.commit("invalid app names " + str(len(names)))
                self.assert_status("FAIL", 1)
        registry = self.set_apps()
        registry.write_text(registry.read_text() + 'name = "duplicate field"\n')
        self.commit("duplicate app field")
        self.assert_status("FAIL", 1)
        registry.write_text("this is not TOML\n")
        self.commit("malformed app registry")
        self.assert_status("FAIL", 1)
        registry.unlink()
        self.commit("missing app registry")
        self.assert_status("FAIL", 1)

    def test_handoff_manifest_hash_size_branch_and_status_drift_fail(self):
        mutations = {"bundle_sha256": "a" * 64, "bundle_bytes": 1,
                     "branch": "release/foundation-0.1-native-" + "0" * 40,
                     "native_gate_status": "pass", "required_filesystem": "APFS",
                     "bundle": "../outside.bundle", "extra": True}
        for key, value in mutations.items():
            with self.subTest(key=key):
                self.manifest_path.write_text(json.dumps(dict(self.manifest, **{key: value})))
                self.refresh_checksums()
                self.assert_status("FAIL", 1)

    def test_stale_checksum_and_changed_bundle_advertised_head_fail(self):
        original = self.manifest_path.read_bytes()
        self.manifest_path.write_bytes(original + b" ")
        self.assert_status("FAIL", 1)
        self.manifest_path.write_bytes(original)
        bundle_bytes = self.bundle.read_bytes()
        self.bundle.write_bytes(bundle_bytes.replace(self.branch.encode(), b"release/wrong", 1))
        manifest = dict(self.manifest, bundle_sha256=self.digest(self.bundle),
                        bundle_bytes=self.bundle.stat().st_size)
        self.manifest_path.write_text(json.dumps(manifest))
        self.refresh_checksums()
        self.assert_status("FAIL", 1)

    def test_missing_evidence_blocks_after_resolved_apps(self):
        self.set_apps()
        self.commit("resolved apps")
        report = self.assert_status("BLOCKED", 0)
        self.assertEqual(report["blockers"][0], "workflow_candidate")

    def test_duplicate_extra_oversize_and_symlink_evidence_fail(self):
        self.prepare_ready()
        text = json.dumps(self.ledger())
        invalid = [text[:-1] + ',"schema":"heleos.foundation-release-evidence/v1"}',
                   json.dumps(dict(self.ledger(), extra=True)), " " * (1024 * 1024 + 1) + text,
                   '{"schema":', "[]"]
        for value in invalid:
            with self.subTest(kind=value[:60]):
                self.evidence_path.write_text(value)
                self.assert_status("FAIL", 1, evidence=True)
        self.put_evidence()
        target = self.base / "linked-evidence.json"
        self.evidence_path.rename(target)
        self.evidence_path.symlink_to(target)
        self.assert_status("FAIL", 1, evidence=True)

    def test_evidence_cross_field_candidate_mismatch_fails(self):
        self.prepare_ready()
        paths = [("workflow", "candidate_sha"), ("workflow", "local_supply_chain", "candidate_sha"),
                 ("git_handoff", "candidate_sha"), ("platforms", "candidate_sha"),
                 ("platforms", "macos", "candidate_sha"), ("platforms", "windows", "candidate_sha")]
        for path in paths:
            with self.subTest(path=path):
                ledger = self.ledger()
                node = ledger
                for key in path[:-1]:
                    node = node[key]
                node[path[-1]] = "0" * 40
                self.put_evidence(ledger)
                self.assert_status("FAIL", 1, evidence=True)

    def test_forged_platform_filesystem_suite_count_digest_and_url_fail(self):
        self.prepare_ready()
        mutations = [
            (("workflow", "local_supply_chain", "platform"), "linux-x86_64"),
            (("platforms", "macos", "platform"), "windows-x86_64"),
            (("platforms", "windows", "platform"), "linux-x86_64"),
            (("platforms", "windows", "filesystem"), "APFS"),
            (("platforms", "windows", "suite_count"), 6),
            (("platforms", "windows", "suite_count"), "7"),
            (("platforms", "windows", "suite_count"), True),
            (("platforms", "windows", "native_receipt_sha256"), "F" * 64),
            (("platforms", "macos", "artifact_sha256"), "a" * 63),
            (("platforms", "windows", "run_url"), REMOTE + "/actions/runs/not-a-run"),
            (("platforms", "windows", "run_url"), "https://github.com/attacker/Heleos-spark/actions/runs/123"),
            (("platforms", "macos", "run_url"), REMOTE + ".evil.invalid/actions/runs/123"),
            (("platforms", "macos", "run_url"), REMOTE + "/actions/runs/" + "1" * 2049),
            (("git_handoff", "push_reference"), REMOTE + "/commit/" + "0" * 40),
            (("git_handoff", "authorization_reference"), "x" * 2049),
            (("git_handoff", "authorization_reference"), " "),
            (("git_handoff", "authorization_reference"), "approval\nforged"),
        ]
        for path, value in mutations:
            with self.subTest(path=path, value=value):
                ledger = self.ledger()
                node = ledger
                for key in path[:-1]:
                    node = node[key]
                node[path[-1]] = value
                self.put_evidence(ledger)
                self.assert_status("FAIL", 1, evidence=True)

    def test_workflow_blob_must_match_the_candidate_git_object(self):
        self.prepare_ready()
        ledger = self.ledger()
        ledger["workflow"]["blob_sha1"] = "0" * 40
        self.put_evidence(ledger)
        self.assert_status("FAIL", 1, evidence=True)

    def test_each_evidence_object_rejects_missing_and_extra_fields(self):
        self.prepare_ready()
        objects = [(), ("workflow",), ("workflow", "local_supply_chain"),
                   ("git_handoff",), ("platforms",), ("platforms", "macos"), ("platforms", "windows")]
        for path in objects:
            for mutation in ("extra", "missing"):
                with self.subTest(path=path, mutation=mutation):
                    ledger = self.ledger()
                    node = ledger
                    for key in path:
                        node = node[key]
                    if mutation == "extra":
                        node["unexpected"] = True
                    else:
                        del node[next(iter(node))]
                    self.put_evidence(ledger)
                    self.assert_status("FAIL", 1, evidence=True)

    def test_owner_authorization_reference_can_identify_a_local_decision(self):
        self.prepare_ready()
        ledger = self.ledger()
        ledger["git_handoff"]["authorization_reference"] = "docs/decisions/synthetic-owner-handoff.md"
        self.put_evidence(ledger)
        self.assert_status("READY_FOR_DOSSIER", 0, evidence=True)

    def test_missing_evidence_blocks_but_non_git_repository_is_inspection_failure(self):
        self.assert_status("BLOCKED", 0, evidence=True)
        code, report = self.probe("--repo", str(self.base), explicit_handoff=False)
        self.assertEqual(code, 1, report)
        self.assertEqual(report["status"], "FAIL")

    def test_valid_first_four_gates_are_ready_for_absent_dossier_with_unfetched_links(self):
        self.prepare_ready()
        report = self.assert_status("READY_FOR_DOSSIER", 0, evidence=True)
        self.assertEqual([gate["status"] for gate in report["gates"][:4]], ["PASS"] * 4)
        self.assertTrue(report["evidence"]["links_not_fetched"])
        self.assertFalse((self.repo / DOSSIER).exists())
        self.assertEqual(self.probe("--require-ready", evidence=True)[0], 0)

    def test_dossier_cannot_override_missing_earlier_gates(self):
        self.write(DOSSIER, self.dossier_text())
        self.commit("synthetic premature acceptance")
        report = self.assert_status("BLOCKED", 0)
        self.assertEqual(report["blockers"][0], "github_apps")

    def test_untracked_or_working_tree_modified_dossier_cannot_accept(self):
        self.prepare_ready()
        dossier = self.write(DOSSIER, self.dossier_text())
        for phase in ("untracked", "modified"):
            with self.subTest(phase=phase):
                code, report = self.probe(evidence=True)
                self.assertIn(report["status"], {"FAIL", "BLOCKED", "READY_FOR_DOSSIER"}, report)
                self.assertNotEqual(report["gates"][-1]["status"], "PASS")
                self.assertIn(code, (0, 1))
            if phase == "untracked":
                self.commit("synthetic accepted dossier")
                dossier.write_text(dossier.read_text() + "Changed after acceptance.\n")

    def test_forged_duplicate_or_mismatched_committed_dossier_is_rejected(self):
        self.prepare_ready()
        invalid = [self.dossier_text(decision_owner="Someone Else"),
                   self.dossier_text(release_candidate_sha="0" * 40),
                   self.dossier_text(accepted_at="2026-02-30T00:00:00Z"),
                   self.dossier_text(accepted_at="2026-09-09T12:00:00+00:00"),
                   self.dossier_text(extra=True), self.dossier_text() * 2,
                   self.dossier_text().replace('"status": "accepted"',
                                               '"status": "accepted", "status": "accepted"')]
        for index, content in enumerate(invalid):
            with self.subTest(index=index):
                self.write(DOSSIER, content)
                self.commit("synthetic invalid dossier " + str(index))
                code, report = self.probe(evidence=True)
                self.assertIn(report["status"], {"FAIL", "BLOCKED"}, report)
                self.assertIn(code, (0, 1))

    def test_exact_committed_dossier_accepts_only_after_first_four_gates(self):
        self.prepare_ready()
        self.write(DOSSIER, self.dossier_text())
        self.commit("synthetic acceptance")
        report = self.assert_status("ACCEPTED", 0, evidence=True)
        self.assertEqual([gate["status"] for gate in report["gates"]], ["PASS"] * 5)
        self.assertEqual(self.probe("--require-ready", evidence=True)[0], 0)

    def test_committed_symlink_dossier_cannot_supply_acceptance(self):
        self.prepare_ready()
        target = self.write("docs/verification/synthetic-target.md", self.dossier_text())
        (self.repo / DOSSIER).symlink_to(target.name)
        self.commit("synthetic symlink dossier")
        code, report = self.probe(evidence=True)
        self.assertEqual(code, 1, report)
        self.assertEqual(report["status"], "FAIL")

    def test_nested_checkout_resolution_determinism_and_no_filesystem_writes(self):
        self.prepare_ready()
        active = self.repo / ".worktrees" / "nested checkout"
        self.git("worktree", "add", "-b", "build/test", str(active))
        nested = active / "docs" / "nested"
        nested.mkdir(parents=True)
        self.write(DOSSIER, self.dossier_text())
        self.commit("synthetic acceptance")
        before = self.hashes()
        first = self.probe(evidence=True)
        self.assertEqual(first[1]["status"], "ACCEPTED", first)
        self.assertEqual(self.probe(evidence=True, cwd=nested), first)
        self.assertEqual(self.probe("--repo", str(nested), evidence=True, cwd=self.base), first)
        self.assertEqual(self.probe(evidence=True), first)
        self.assertEqual(before, self.hashes(), "status inspection wrote checkout or Git files")

    def test_default_json_is_compact_deterministic_and_human_mode_is_actionable(self):
        self.assertTrue(SCRIPT.is_file(), "foundation-release-status.py is not implemented yet")
        command = [sys.executable, "-B", str(SCRIPT), "--handoff", str(self.handoff)]
        first = subprocess.run(command, cwd=self.repo, env=self.env, capture_output=True, text=True)
        second = subprocess.run(command, cwd=self.repo, env=self.env, capture_output=True, text=True)
        self.assertEqual(first.stdout, second.stdout)
        data = json.loads(first.stdout)
        self.assertEqual(first.stdout.strip(), json.dumps(data, sort_keys=True, separators=(",", ":")))
        human = subprocess.run(command + ["--human"], cwd=self.repo, env=self.env,
                               capture_output=True, text=True)
        self.assertEqual(human.returncode, 0, human.stderr)
        self.assertIn("BLOCKED", human.stdout)
        self.assertIn("github_apps", human.stdout)
        self.assertIn("owner", human.stdout.lower())


if __name__ == "__main__":
    unittest.main()
