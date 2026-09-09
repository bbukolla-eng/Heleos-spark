"""Black-box packaging contracts using real, isolated Git repositories."""
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
SCRIPT = ROOT / "scripts/package-windows-candidate.py"


class WindowsCandidateTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / "target/packaging-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="candidate-", dir=scratch))
        self.repo = self.root / "source"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "build/native")
        self.git("config", "user.name", "Synthetic Packaging Test")
        self.git("config", "user.email", "synthetic@example.invalid")
        (self.repo / ".gitignore").write_text("/target/\n")
        (self.repo / "scripts").mkdir()
        (self.repo / "scripts/verify-windows-worker-containment.ps1").write_text("# synthetic gate\n")
        self.git("add", ".")
        self.git("commit", "-qm", "synthetic initial")
        self.commit = self.git("rev-parse", "HEAD").strip()

    def git(self, *args, repo=None):
        return subprocess.check_output(["git", "-C", str(repo or self.repo), *args], text=True)

    def run_package(self, *, repo=None, commit=None, branch="build/native", env=None):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "--repo", str(repo or self.repo),
             "--commit", commit or self.commit, "--branch", branch],
            capture_output=True, text=True, env=env,
        )

    def output(self, repo=None):
        return (repo or self.repo) / "target/windows-native-candidates" / self.commit

    def assert_rejected(self, result):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("WINDOWS_CANDIDATE_ERROR:", result.stderr)
        self.assertFalse((self.output() / "candidate.json").exists())

    def test_clean_commit_yields_verified_clone_and_matching_manifest(self):
        result = self.run_package()
        self.assertEqual(result.returncode, 0, result.stderr)
        directory = self.output()
        manifest = json.loads((directory / "candidate.json").read_text())
        self.assertEqual(json.loads(result.stdout).get("manifest_sha256"),
                         hashlib.sha256((directory / "candidate.json").read_bytes()).hexdigest())
        self.assertEqual(manifest["commit"], self.commit)
        self.assertEqual(manifest["branch"], "build/native")
        self.assertEqual(manifest["native_gate_status"], "pending")
        bundle = directory / manifest["bundle"]
        self.assertEqual(manifest["bundle_sha256"], hashlib.sha256(bundle.read_bytes()).hexdigest())
        self.assertEqual(manifest["bundle_bytes"], bundle.stat().st_size)
        self.assertEqual(set(p.name for p in directory.iterdir()), {bundle.name, "README.md", "candidate.json"})
        self.git("bundle", "verify", str(bundle))
        clone = self.root / "transferred"
        self.git("clone", "-q", "-b", "build/native", str(bundle), str(clone))
        self.assertEqual(self.git("rev-parse", "HEAD", repo=clone).strip(), self.commit)
        self.assertEqual(self.git("status", "--porcelain", repo=clone), "")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_same_git_inputs_produce_identical_artifact_bytes(self):
        second = self.root / "second"
        self.git("clone", "-q", str(self.repo), str(second))
        for repo in (self.repo, second):
            result = self.run_package(repo=repo)
            self.assertEqual(result.returncode, 0, result.stderr)
        first_bytes = {p.name: p.read_bytes() for p in self.output().iterdir()}
        second_bytes = {p.name: p.read_bytes() for p in self.output(second).iterdir()}
        self.assertEqual(first_bytes, second_bytes)

    def test_rejects_wrong_commit_before_creating_output(self):
        self.assert_rejected(self.run_package(commit="0" * 40))
        self.assertFalse((self.repo / "target").exists())

    def test_rejects_wrong_branch(self):
        self.assert_rejected(self.run_package(branch="main"))

    def test_rejects_detached_head(self):
        self.git("checkout", "--detach", "-q")
        self.assert_rejected(self.run_package())

    def test_rejects_shallow_history(self):
        shallow = self.root / "shallow"
        self.git("clone", "-q", "--depth=1", self.repo.as_uri(), str(shallow))
        result = self.run_package(repo=shallow)
        self.assert_rejected(result)
        self.assertFalse(self.output(shallow).exists())

    def test_rejects_corrupt_pack_before_publishing_manifest(self):
        # Fault injection at the actual Git output boundary. All commands still
        # execute real Git; only the emitted pack header is corrupted afterward.
        tools = self.root / "tools"
        tools.mkdir()
        wrapper = tools / "git"
        wrapper.write_text(
            "#!" + sys.executable + "\n"
            "import os, subprocess, sys\n"
            "pack = 'pack-objects' in sys.argv\n"
            "offset = os.lseek(1, 0, os.SEEK_CUR) if pack else 0\n"
            "result = subprocess.run([" + repr(shutil.which("git")) + ", *sys.argv[1:]])\n"
            "if pack and result.returncode == 0:\n"
            "    os.lseek(1, offset, os.SEEK_SET)\n"
            "    os.write(1, b'FAIL')\n"
            "sys.exit(result.returncode)\n"
        )
        wrapper.chmod(0o700)
        env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ["PATH"])
        self.assert_rejected(self.run_package(env=env))

    def test_rejects_modified_tracked_file(self):
        (self.repo / ".gitignore").write_text("/target/\n# modified\n")
        self.assert_rejected(self.run_package())

    def test_rejects_nonignored_untracked_file(self):
        (self.repo / "unsaved.txt").write_text("do not omit this work")
        self.assert_rejected(self.run_package())

    def test_rejects_output_that_is_not_ignored(self):
        (self.repo / ".gitignore").write_text("")
        self.git("commit", "-qam", "remove ignore")
        self.commit = self.git("rev-parse", "HEAD").strip()
        self.assert_rejected(self.run_package())

    def test_rejects_existing_output_without_overwriting(self):
        self.output().mkdir(parents=True)
        sentinel = self.output() / "sentinel"
        sentinel.write_bytes(b"preserve")
        self.assert_rejected(self.run_package())
        self.assertEqual(sentinel.read_bytes(), b"preserve")
        self.assertEqual(list(self.output().iterdir()), [sentinel])

    def test_rejects_symlink_target_without_outside_writes(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.repo / "target").symlink_to(outside, target_is_directory=True)
        self.assert_rejected(self.run_package())
        self.assertEqual(list(outside.iterdir()), [])

    def test_rejects_candidate_without_committed_gate(self):
        self.git("rm", "-q", "scripts/verify-windows-worker-containment.ps1")
        self.git("commit", "-qm", "remove gate")
        self.commit = self.git("rev-parse", "HEAD").strip()
        self.assert_rejected(self.run_package())


if __name__ == "__main__":
    unittest.main()
