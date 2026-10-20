"""Black-box draft contracts; every repository and decision is synthetic.

The expectations in this file are hand-derived from the operator contract. The
production scripts are launched as subprocesses and never imported here.
"""

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/prepare-github-app-decisions.py"
APPLIER = ROOT / "scripts/apply-github-app-decisions.py"
APPS = ["Azure Pipelines", "AWS Connector for GitHub", "Amazon Q Developer", "ECC Tools"]
REGISTRY = "governance/github-apps.toml"
INBOX = "OWNER_ACTION_REQUIRED"
SECRET = "SYNTHETIC_SECRET_SENTINEL_NEVER_ECHO_7d84"
REPORT_KEYS = {
    "schema_version", "status", "main", "registry", "packet", "unresolved_fields",
    "write_performed", "account_changes_performed", "owner_authenticity_verified",
    "release_authority", "errors",
}


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


class PrepareGitHubAppDecisionsTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="heleos-prepare-decisions-test-")
        self.addCleanup(scratch.cleanup)
        self.base = Path(scratch.name).resolve()
        self.repo = self.base / "visible main"
        self.repo.mkdir()
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update({
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Synthetic Test", "GIT_AUTHOR_EMAIL": "test@example.invalid",
            "GIT_COMMITTER_NAME": "Synthetic Test", "GIT_COMMITTER_EMAIL": "test@example.invalid",
            "GIT_AUTHOR_DATE": "2026-09-09T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-09-09T00:00:00Z", "GIT_OPTIONAL_LOCKS": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        self.git("init", "--initial-branch=main")
        self.git("config", "core.autocrlf", "false")
        self.write(".gitignore", ".worktrees/\n/OWNER_ACTION_REQUIRED/*.json\n")
        self.write("README.md", "Synthetic disposable fixture.\n")
        self.write(INBOX + "/README.md", "Synthetic owner inbox.\n")
        self.write(".github/workflows/existing.yml", "name: fixture\non: workflow_dispatch\n")
        self.original = self.registry_bytes("owner_decision_required")
        self.registry = self.write(REGISTRY, self.original)
        self.registry.chmod(0o640)
        self.commit("synthetic base")
        self.head = self.git("rev-parse", "HEAD").strip()
        self.packet = self.repo / INBOX / "owner-draft.json"

    def git(self, *args, cwd=None, check=True):
        return subprocess.run(
            ["git", "-C", str(cwd or self.repo), *args], env=self.env,
            capture_output=True, text=True, check=check,
        ).stdout

    def write(self, relative, value):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else value.encode("utf-8"))
        return path

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-qm", message)

    def registry_bytes(self, disposition):
        sections = ["# Synthetic inventory; never owner authorization.\n"]
        for number, name in enumerate(APPS):
            fields = {
                "name": name,
                "origin": "synthetic account inventory",
                "version_or_digest": "not inventoried" if disposition == "owner_decision_required"
                else "synthetic-runtime-sha256-" + str(number),
                "license_or_rights": "reviewed synthetic terms",
                "data_class": "PROJECT_CONFIDENTIAL",
                "owner": "repository owner",
                "permissions": "not inventoried" if disposition == "owner_decision_required"
                else "synthetic metadata read permission",
                "egress": "prohibited pending owner decision" if disposition == "owner_decision_required"
                else "prohibited by synthetic owner decision",
                "evaluation": "not evaluated" if disposition == "owner_decision_required"
                else "synthetic installation was evaluated against policy",
                "rollback": "owner removes synthetic installation",
                "disposition": disposition,
            }
            if disposition != "owner_decision_required":
                fields.update({
                    "decision_owner": "Bekim Bukolla",
                    "decision_date": "2026-09-09",
                    "decision_evidence": "evidence/synthetic-owner-decision.md",
                    "installation_evidence": "evidence/synthetic-installation-%d.json" % number,
                })
            sections.append("[[github_app]]\n" + "".join(
                key + " = " + json.dumps(value) + "\n" for key, value in fields.items()))
        return "\n".join(sections).encode("ascii")

    def expected_value(self, head=None, registry=None):
        records = []
        for name in APPS:
            records.append({
                "name": name,
                "disposition": None,
                "version_or_digest": None,
                "permissions": None,
                "egress": None,
                "evaluation": None,
                "decision_evidence": None,
                "installation_evidence": None,
            })
        return {
            "schema": "heleos.github-app-decisions/v1",
            "repository": "bbukolla-eng/Heleos-spark",
            "expected_head": head or self.head,
            "expected_registry_sha256": sha256(self.original if registry is None else registry),
            "decision_owner": "Bekim Bukolla",
            "decision_date": None,
            "apps": records,
        }

    def expected_bytes(self, head=None, registry=None):
        return (json.dumps(self.expected_value(head, registry), ensure_ascii=True, indent=2)
                + "\n").encode("ascii")

    def invoke(self, repo=None, output=None, extra=(), env=None, default_repo=False, cwd=None):
        argv = [sys.executable, "-B", str(SCRIPT)]
        if not default_repo:
            argv.extend(["--repo", str(repo if repo is not None else self.repo)])
        argv.extend(["--output", str(output if output is not None else self.packet)])
        argv.extend(extra)
        return subprocess.run(
            argv, cwd=str(cwd or self.repo), env=self.env if env is None else env,
            capture_output=True, text=True, timeout=20,
        )

    def report(self, run, status):
        self.assertEqual(run.returncode, 0 if status == "PREPARED" else 1,
                         run.stderr + run.stdout)
        self.assertEqual(run.stderr, "")
        value = json.loads(run.stdout)
        self.assertEqual(set(value), REPORT_KEYS)
        self.assertEqual(value["status"], status)
        self.assertFalse(value["account_changes_performed"])
        self.assertFalse(value["owner_authenticity_verified"])
        self.assertFalse(value["release_authority"])
        return value

    def assert_failure(self, run, code=None):
        value = self.report(run, "FAIL")
        self.assertFalse(value["write_performed"])
        self.assertNotIn(SECRET, run.stdout + run.stderr)
        if code is not None:
            self.assertEqual([item["code"] for item in value["errors"]], [code])
        return value

    def tracked_snapshot(self):
        raw = subprocess.run(
            ["git", "-C", str(self.repo), "ls-files", "-z"], env=self.env,
            capture_output=True, check=True,
        ).stdout
        result = {}
        for encoded in raw.split(b"\0"):
            if not encoded:
                continue
            relative = os.fsdecode(encoded)
            path = self.repo / relative
            info = path.lstat()
            result[relative] = (stat.S_IFMT(info.st_mode), stat.S_IMODE(info.st_mode), path.read_bytes())
        return {
            "head": self.git("rev-parse", "HEAD"),
            "index": self.git("ls-files", "--stage"),
            "status": self.git("status", "--porcelain=v1", "--untracked-files=all"),
            "files": result,
        }

    def filled_packet(self, path=None):
        target = path or self.packet
        value = json.loads(target.read_bytes())
        value["decision_date"] = "2026-09-09"
        for number, app in enumerate(value["apps"]):
            app.update({
                "disposition": "remove",
                "version_or_digest": "unavailable: synthetic installation was removed",
                "permissions": "unavailable: synthetic installation was removed",
                "egress": "prohibited by synthetic owner decision",
                "evaluation": "synthetic removal evaluated against the policy",
                "decision_evidence": "evidence/synthetic-owner-decision.md",
                "installation_evidence": "evidence/synthetic-installation-%d.json" % number,
            })
        target.write_text(json.dumps(value), encoding="ascii")
        return value

    def invoke_applier(self, apply=False, packet=None):
        argv = [sys.executable, "-B", str(APPLIER), "--repo", str(self.repo),
                "--packet", str(packet or self.packet)]
        if apply:
            argv.append("--apply")
        return subprocess.run(argv, cwd=str(self.repo), env=self.env,
                              capture_output=True, text=True, timeout=20)

    def hook_environment(self, source):
        hook = self.base / "hook"
        hook.mkdir(exist_ok=True)
        (hook / "sitecustomize.py").write_text(source, encoding="ascii")
        env = dict(self.env)
        env["PYTHONPATH"] = str(hook)
        return env

    def test_prepares_exact_private_unresolved_draft_and_report_without_git_mutation(self):
        before = self.tracked_snapshot()
        expected = self.expected_bytes()
        value = self.report(self.invoke(), "PREPARED")
        self.assertEqual(self.packet.read_bytes(), expected)
        self.assertEqual(value, {
            "schema_version": 1,
            "status": "PREPARED",
            "main": {"path": str(self.repo), "head": self.head},
            "registry": {"path": str(self.registry), "sha256": sha256(self.original)},
            "packet": {"path": str(self.packet), "sha256": sha256(expected)},
            "unresolved_fields": 29,
            "write_performed": True,
            "account_changes_performed": False,
            "owner_authenticity_verified": False,
            "release_authority": False,
            "errors": [],
        })
        decoded = json.loads(expected)
        nulls = sum(item is None for item in decoded.values())
        nulls += sum(item is None for app in decoded["apps"] for item in app.values())
        self.assertEqual(nulls, 29)
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.packet.stat().st_mode), 0o600)
        self.assertEqual(self.tracked_snapshot(), before)

    def test_different_fresh_filenames_are_byte_identical_and_default_repo_works(self):
        first = self.repo / INBOX / "first.json"
        second = self.repo / INBOX / "second.json"
        self.report(self.invoke(output=first, default_repo=True), "PREPARED")
        self.report(self.invoke(output=second), "PREPARED")
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual(first.read_bytes(), self.expected_bytes())

    def test_untracked_unrelated_file_is_preserved_and_does_not_block(self):
        unrelated = self.write("local-notes.txt", "Untouched untracked bytes.\n")
        self.report(self.invoke(), "PREPARED")
        self.assertEqual(unrelated.read_bytes(), b"Untouched untracked bytes.\n")

    def test_untouched_draft_is_rejected_and_filled_synthetic_draft_dry_runs(self):
        self.report(self.invoke(), "PREPARED")
        rejected = json.loads(self.invoke_applier().stdout)
        self.assertEqual(rejected["status"], "FAIL")
        self.assertFalse(rejected["write_performed"])
        self.filled_packet()
        accepted = json.loads(self.invoke_applier().stdout)
        self.assertEqual(accepted["status"], "DRY_RUN")
        self.assertFalse(accepted["write_performed"])
        self.assertEqual(self.registry.read_bytes(), self.original)

    def test_resolved_registry_values_are_never_copied_into_a_new_draft(self):
        self.report(self.invoke(), "PREPARED")
        self.filled_packet()
        applied = json.loads(self.invoke_applier(apply=True).stdout)
        self.assertEqual(applied["status"], "APPLIED")
        resolved = self.registry.read_bytes()
        self.commit("synthetic resolved registry")
        head = self.git("rev-parse", "HEAD").strip()
        second = self.repo / INBOX / "resolved-source-draft.json"
        self.report(self.invoke(output=second), "PREPARED")
        self.assertEqual(second.read_bytes(), self.expected_bytes(head=head, registry=resolved))

    def test_nested_linked_worktree_routes_to_unique_visible_main(self):
        worktree = self.repo / ".worktrees" / "candidate"
        self.git("worktree", "add", "-b", "candidate", str(worktree))
        value = self.report(self.invoke(repo=worktree / "governance"), "PREPARED")
        self.assertEqual(value["main"], {"path": str(self.repo), "head": self.head})
        self.assertTrue(self.packet.is_file())
        self.assertFalse((worktree / INBOX / self.packet.name).exists())

    def test_dirty_and_staged_tracked_files_fail_without_output(self):
        self.write("README.md", "Dirty tracked work.\n")
        self.assert_failure(self.invoke(), "tracked_changes")
        self.assertFalse(self.packet.exists())
        self.git("add", "README.md")
        self.assert_failure(self.invoke(), "tracked_changes")
        self.assertFalse(self.packet.exists())

    def test_unstaged_staged_and_mode_changed_registry_fail(self):
        self.registry.write_bytes(self.original + b"# drift\n")
        self.assert_failure(self.invoke())
        self.registry.write_bytes(self.original)
        self.registry.chmod(0o740)
        if os.name != "nt":
            self.assert_failure(self.invoke(), "registry_drift")
        self.registry.chmod(0o640)
        self.registry.write_bytes(self.original + b"# staged drift\n")
        self.git("add", REGISTRY)
        self.assert_failure(self.invoke())

    def test_malformed_committed_registry_fails_closed(self):
        self.registry.write_bytes(self.original + b"unknown syntax\n")
        self.commit("synthetic malformed registry")
        self.assert_failure(self.invoke())
        self.assertFalse(self.packet.exists())

    def test_missing_registered_main_and_duplicate_main_checkouts_fail_closed(self):
        self.git("branch", "-m", "candidate")
        self.assert_failure(self.invoke(), "main_checkout_unavailable")
        self.git("branch", "-m", "main")
        second = self.repo / ".worktrees" / "second-main"
        self.git("worktree", "add", "--force", str(second), "main")
        self.assert_failure(self.invoke(repo=second), "main_checkout_unavailable")

    def test_git_routing_overrides_cannot_redirect_preparation(self):
        poisoned = self.base / "poisoned"
        poisoned.mkdir()
        self.git("init", "--initial-branch=main", cwd=poisoned)
        variants = [
            {"GIT_DIR": str(poisoned / ".git"), "GIT_WORK_TREE": str(poisoned)},
            {"GIT_COMMON_DIR": str(poisoned / ".git")},
            {"GIT_INDEX_FILE": str(self.base / "index")},
            {"GIT_NAMESPACE": "poisoned"},
            {"GIT_OBJECT_DIRECTORY": str(poisoned / ".git" / "objects")},
            {"GIT_ALTERNATE_OBJECT_DIRECTORIES": str(poisoned / ".git" / "objects")},
            {"GIT_CONFIG_PARAMETERS": "'core.worktree=poisoned'"},
            {"GIT_CONFIG_COUNT": "1"},
        ]
        for additions in variants:
            with self.subTest(additions=sorted(additions)):
                env = dict(self.env)
                env.update(additions)
                self.assert_failure(self.invoke(env=env), "git_environment_override")
                self.assertFalse(self.packet.exists())

    def test_output_must_be_canonical_direct_ignored_json_child_of_owner_inbox(self):
        invalid = [
            self.repo / "outside.json",
            self.repo / INBOX / "nested" / "draft.json",
            self.repo / INBOX / "draft.txt",
            str(self.repo / INBOX) + "/../OWNER_ACTION_REQUIRED/alias.json",
            str(self.repo) + "/./OWNER_ACTION_REQUIRED/alias.json",
        ]
        for target in invalid:
            with self.subTest(target=str(target)):
                self.assert_failure(self.invoke(output=target))
                self.assertFalse(Path(target).exists())

    def test_missing_symlinked_and_unignored_inbox_fail_closed(self):
        readme = self.repo / INBOX / "README.md"
        readme.unlink()
        (self.repo / INBOX).rmdir()
        self.assert_failure(self.invoke())
        real = self.repo / "real-inbox"
        real.mkdir()
        (self.repo / INBOX).symlink_to(real, target_is_directory=True)
        self.assert_failure(self.invoke())
        (self.repo / INBOX).unlink()
        (self.repo / INBOX).mkdir()
        self.write(INBOX + "/README.md", "Synthetic owner inbox.\n")
        self.write(".gitignore", ".worktrees/\n")
        self.commit("synthetic unignored inbox")
        self.assert_failure(self.invoke(), "invalid_output")

    def test_existing_file_symlink_directory_and_fifo_are_never_replaced(self):
        cases = ["file", "symlink", "directory"]
        if hasattr(os, "mkfifo"):
            cases.append("fifo")
        for kind in cases:
            target = self.repo / INBOX / (kind + ".json")
            if kind == "file":
                target.write_bytes(b"existing-file")
            elif kind == "symlink":
                outside = self.base / ("outside-" + kind)
                outside.write_bytes(b"outside")
                target.symlink_to(outside)
            elif kind == "directory":
                target.mkdir()
            else:
                os.mkfifo(target)
            before = target.lstat()
            self.assert_failure(self.invoke(output=target))
            after = target.lstat()
            self.assertEqual((stat.S_IFMT(after.st_mode), after.st_ino),
                             (stat.S_IFMT(before.st_mode), before.st_ino))
            if kind == "file":
                self.assertEqual(target.read_bytes(), b"existing-file")
            if kind == "symlink":
                self.assertEqual(target.readlink(), outside)
                target.unlink()
            elif kind == "directory":
                target.rmdir()
            else:
                target.unlink()

    def test_competing_target_creation_wins_without_overwrite_or_temp_leak(self):
        source = (
            "import os\nfrom pathlib import Path\n_real = os.link\n_done = False\n"
            "def link(src, dst, *args, **kwargs):\n"
            "    global _done\n"
            "    if not _done:\n"
            "        Path(dst).write_bytes(b'competing-writer')\n"
            "        _done = True\n"
            "    return _real(src, dst, *args, **kwargs)\n"
            "os.link = link\n"
        )
        self.assert_failure(self.invoke(env=self.hook_environment(source)), "output_exists")
        self.assertEqual(self.packet.read_bytes(), b"competing-writer")
        self.assertEqual(sorted(path.name for path in (self.repo / INBOX).iterdir()),
                         ["README.md", self.packet.name])

    def test_registry_drift_after_temp_fsync_fails_before_publication(self):
        source = (
            "import os\nfrom pathlib import Path\n_real = os.fsync\n_done = False\n"
            "def fsync(fd):\n"
            "    global _done\n"
            "    result = _real(fd)\n"
            "    if not _done:\n"
            "        Path(os.environ['HELEOS_TEST_REGISTRY']).write_bytes(b'changed during publication')\n"
            "        _done = True\n"
            "    return result\n"
            "os.fsync = fsync\n"
        )
        env = self.hook_environment(source)
        env["HELEOS_TEST_REGISTRY"] = str(self.registry)
        self.assert_failure(self.invoke(env=env), "state_changed")
        self.assertFalse(self.packet.exists())
        self.assertEqual(sorted(path.name for path in (self.repo / INBOX).iterdir()), ["README.md"])

    def test_required_unknown_arguments_help_and_human_output_are_bounded(self):
        missing = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "--repo", str(self.repo)],
            cwd=str(self.repo), env=self.env, capture_output=True, text=True, timeout=20,
        )
        self.assert_failure(missing, "invalid_arguments")
        self.assert_failure(self.invoke(extra=("--" + SECRET,)), "invalid_arguments")
        help_run = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "--help"], cwd=str(self.repo), env=self.env,
            capture_output=True, text=True, timeout=20,
        )
        self.assertEqual(help_run.returncode, 0)
        self.assertIn("--output", help_run.stdout)
        self.assertNotIn("--force", help_run.stdout)
        self.assertNotIn("--apply", help_run.stdout)
        human_target = self.repo / INBOX / "human.json"
        human = self.invoke(output=human_target, extra=("--human",))
        self.assertEqual(human.returncode, 0, human.stderr + human.stdout)
        self.assertTrue(human.stdout.startswith("PREPARED\n"))
        self.assertIn("Unresolved fields: 29", human.stdout)
        self.assertIn("account changes: no", human.stdout)
        self.assertNotIn("READY", human.stdout)
        self.assertNotIn("APPLIED", human.stdout)


if __name__ == "__main__":
    unittest.main()
