"""Actual contained runner receipt with a local fake provider, no model call."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from loop_runner_handoff import HandoffError, import_candidate  # noqa: E402


def sha(data):
    return hashlib.sha256(data).hexdigest()


@unittest.skipUnless(sys.platform == "darwin", "macOS Seatbelt proof")
class ActualRunnerHandoffTest(unittest.TestCase):
    def test_contained_fake_provider_receipt_imports_exact_candidate(self):
        runner = ROOT / "target/debug/heleos-worker-runner"
        if not runner.is_file():
            self.skipTest("offline runner binary has not been built")
        with tempfile.TemporaryDirectory(prefix="loop-runner-proof-") as scratch:
            root = Path(scratch).resolve()
            source, runs, destination = root / "source", root / "runs", root / "destination"
            for path in (source, runs, destination):
                path.mkdir()
            (source / "AGENTS.md").write_text("Synthetic fixture instructions only.\n")
            subprocess.run(["git", "init", "-q", "-b", "main", str(source)], check=True)
            subprocess.run(["git", "-C", str(source), "add", "AGENTS.md"], check=True)
            env = {"GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                   "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
            subprocess.run(["git", "-C", str(source), "commit", "-qm", "base"],
                           check=True, env={**os.environ, **env})
            head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"],
                                           text=True).strip()
            paths = ["scripts/experiments/grounding_dino_p1_harness.py",
                     "tests/experiments/test_grounding_dino_p1_harness.py"]
            task = {"schema": "heleos.worker-task/v1", "task_id": "synthetic-runner-receipt",
                    "provider": "claude_code", "mode": "implementation", "base_commit": head,
                    "objective": "Write exactly two synthetic candidate files; no model or network.",
                    "allowed_paths": paths, "forbidden_paths": [".git"],
                    "input_data_class": "INTERNAL", "egress_policy": "approved_external",
                    "instruction_sha256": {"AGENTS.md": sha((source / "AGENTS.md").read_bytes())},
                    "acceptance_commands": ["python3 -B -m unittest"],
                    "limits": {"max_actions": 1, "max_duration_seconds": None}}
            task_bytes = (json.dumps(task, sort_keys=True) + "\n").encode()
            task_path = root / "task.json"
            task_path.write_bytes(task_bytes)
            script = ("from pathlib import Path; "
                      "a=Path('scripts/experiments/grounding_dino_p1_harness.py'); "
                      "b=Path('tests/experiments/test_grounding_dino_p1_harness.py'); "
                      "a.parent.mkdir(parents=True); b.parent.mkdir(parents=True); "
                      "a.write_text('# fake candidate\\n'); b.write_text('# fake test\\n')")
            argv = [str(runner), "--task", str(task_path), "--source", str(source),
                    "--workspace-root", str(runs), "--provider", "claude_code",
                    "--command", sys.executable, "--git", "/usr/bin/git",
                    "--containment", "macos_seatbelt",
                    "--approved-internal-task-sha256", sha(task_bytes),
                    "--selected-source-file", "AGENTS.md=" + task["instruction_sha256"]["AGENTS.md"],
                    "--", "-B", "-c", script]
            result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            summary = json.loads(result.stdout)
            view = Path(summary["provider_view"])
            self.assertEqual(view.parent, Path(summary["run_directory"]))
            self.assertTrue((view / "AGENTS.md").is_file())
            self.assertFalse((view / ".git").exists())
            self.assertTrue((Path(summary["checkout_path"]) / ".git/HEAD").is_file())
            output = root / "guard-stdout.json"
            output.write_text(result.stdout)
            evidence = import_candidate(
                {"state": "succeeded", "outcome": "succeeded", "stdout_path": str(output)},
                task_bytes=task_bytes, workspace_root=runs,
                destination=destination, expected_paths=paths,
                expected_selected_source_sha256=task["instruction_sha256"])
            self.assertEqual(set(evidence["candidate_sha256"]), set(paths))
            self.assertTrue(all((destination / path).is_file() for path in paths))
            again = import_candidate(
                {"state": "succeeded", "outcome": "succeeded", "stdout_path": str(output)},
                task_bytes=task_bytes, workspace_root=runs,
                destination=destination, expected_paths=paths,
                expected_selected_source_sha256=task["instruction_sha256"])
            self.assertEqual(again, evidence)
            with self.assertRaisesRegex(HandoffError, "selected source"):
                import_candidate(
                    {"state": "succeeded", "outcome": "succeeded", "stdout_path": str(output)},
                    task_bytes=task_bytes, workspace_root=runs, destination=destination,
                    expected_paths=paths, expected_selected_source_sha256={"AGENTS.md": "0" * 64})
            summary["provider_view"] = str(Path(summary["run_directory"]) / "checkout")
            forged = json.dumps(summary) + "\n"
            output.write_text(forged)
            (Path(summary["run_directory"]) / "run.json").write_text(forged)
            with self.assertRaisesRegex(HandoffError, "selected provider view"):
                import_candidate(
                    {"state": "succeeded", "outcome": "succeeded", "stdout_path": str(output)},
                    task_bytes=task_bytes, workspace_root=runs, destination=destination,
                    expected_paths=paths,
                    expected_selected_source_sha256=task["instruction_sha256"])


if __name__ == "__main__":
    unittest.main()
