"""Exercise evidence enforcement through the actual commit hook and CI adapter."""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = 'docs/operations/build-checkpoint.json'
POLICY = 'docs/operations/checkpoint-evidence-policy.json'


class EvidenceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.write('README.md', 'Legacy fixture baseline.\n')
        self.git('add', 'README.md')
        self.git('commit', '-qm', 'legacy fixture root')
        self.legacy = self.git('rev-parse', 'HEAD').stdout.strip()
        self.write('scripts/verify-build-checkpoint.py', (ROOT / 'scripts/verify-build-checkpoint.py').read_text())
        self.write('.githooks/pre-commit', (ROOT / '.githooks/pre-commit').read_text())
        (self.repo / '.githooks/pre-commit').chmod(0o755)
        self.write(POLICY, json.dumps({'schema_version': 1, 'require_notebooklm_evidence': True, 'reject_completed_resume': True}))
        # Synthetic preexisting accepted task. Bootstrap is fixture setup, not a product commit.
        self.write(RECEIPT, json.dumps({'task_id': 'DONE-1', 'outcome': 'complete'}))
        self.git('add', '.')
        self.git('commit', '-qm', 'synthetic policy baseline')
        self.base = self.git('rev-parse', 'HEAD').stdout.strip()
        self.git('config', 'core.hooksPath', '.githooks')

    def run_cmd(self, args):
        return subprocess.run(args, cwd=self.repo, env=self.env, text=True, capture_output=True)

    def git(self, *args):
        result = self.run_cmd(['git', *args])
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def write(self, name, value):
        dest = self.repo / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(value)

    def proof(self, name):
        return {'path': name, 'sha256': hashlib.sha256((self.repo / name).read_bytes()).hexdigest()}

    def stage_receipt(self, receipt):
        self.git('add', '.')
        names = self.git('diff', '--cached', '--name-only', self.base).stdout.splitlines()
        receipt['changed_files'] = [self.proof(p) for p in names if p != RECEIPT]
        self.write(RECEIPT, json.dumps(receipt))
        self.git('add', RECEIPT)

    def candidate(self, next_id='NEXT-1', research=True):
        self.write('docs/change.md', 'A bounded documentation change.\n')
        self.write('docs/operations/check.txt', 'Synthetic successful check.\n')
        self.write('docs/operations/review.txt', 'Synthetic independent review.\n')
        marker = {'schema_version': 1, 'receipt': RECEIPT, 'task_id': 'NEW-1', 'outcome': 'complete', 'next_task_id': next_id, 'next_action': 'Prepare the next bounded task.'}
        self.write('CURRENT_STATUS.md', f'**Primary product task: {next_id}.**\n\n**Next executable action:** {marker["next_action"]}\n\n<!-- build-checkpoint:v1 -->\n```json\n{json.dumps(marker)}\n```\n<!-- /build-checkpoint -->\n')
        receipt = {k: v for k, v in marker.items() if k != 'receipt'}
        receipt.update(base_commit=self.base, summary='Synthetic bounded documentation completion.', remaining_limits=['No product acceptance.'], missing_prerequisite=None, independent_action=None, checks=[{'command': 'fixture check', 'exit_code': 0, 'evidence': self.proof('docs/operations/check.txt')}], review={'reviewer': 'Independent fixture reviewer', 'outcome': 'accepted', 'evidence': self.proof('docs/operations/review.txt')})
        if research:
            receipt['notebooklm_research'] = {'disposition': 'administrative_no_new_claims', 'records': [], 'applicability': 'Documentation only, no new knowledge-dependent behavior.', 'behavior_and_checks': 'Fixture note and exact staged checkpoint.', 'gaps_and_next_action': 'No research gap for administrative change.', 'external_submissions': 'None: administrative only.'}
        self.stage_receipt(receipt)
        return receipt

    def test_real_hook_rejects_missing_research_then_accepts_explicit_admin(self):
        receipt = self.candidate(research=False)
        rejected = self.run_cmd(['git', 'commit', '-qm', 'missing research'])
        self.assertNotEqual(rejected.returncode, 0, rejected.stdout + rejected.stderr)
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout.strip(), self.base)
        self.candidate()
        accepted = self.run_cmd(['git', 'commit', '-qm', 'documented administrative scope'])
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)

    def test_real_hook_blocks_completed_resume_until_pinned_reopen(self):
        receipt = self.candidate(next_id='DONE-1')
        rejected = self.run_cmd(['git', 'commit', '-qm', 'stale resume'])
        self.assertNotEqual(rejected.returncode, 0, rejected.stdout + rejected.stderr)
        self.assertEqual(self.git('rev-parse', 'HEAD').stdout.strip(), self.base)
        self.write('docs/operations/reopen.txt', 'New source change requires a specifically bounded reopened task.\n')
        receipt['next_task_reopen'] = {'task_id': 'DONE-1', 'reason': 'Changed source evidence.', 'evidence': self.proof('docs/operations/reopen.txt')}
        self.stage_receipt(receipt)
        accepted = self.run_cmd(['git', 'commit', '-qm', 'explicit reopening'])
        self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)

    def test_ci_range_checks_research_even_when_commit_hook_was_not_installed(self):
        self.candidate(research=False)
        # CI must protect repositories without a local hook. Disable only in this synthetic repo.
        self.git('config', '--unset', 'core.hooksPath')
        self.git('commit', '-qm', 'invalid checkpoint from hookless fixture')
        head = self.git('rev-parse', 'HEAD').stdout.strip()
        spec = importlib.util.spec_from_file_location('evidence_integration_ci', ROOT / 'tools/ci/check_build_checkpoints.py')
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        self.addCleanup(lambda: sys.modules.pop(spec.name, None))
        spec.loader.exec_module(module)
        with self.assertRaises(module.CheckpointError):
            module.validate_range(self.repo, self.base, head, baseline=self.base, validator_path=ROOT / 'scripts/verify-build-checkpoint.py')

    def test_completed_task_from_second_merge_parent_cannot_be_selected(self):
        self.git('checkout', '-qb', 'side')
        side_receipt = self.candidate()
        side_receipt['task_id'] = 'SIDE-DONE'
        status_path = self.repo / 'CURRENT_STATUS.md'
        status_path.write_text(status_path.read_text().replace('"task_id": "NEW-1"', '"task_id": "SIDE-DONE"'))
        self.stage_receipt(side_receipt)
        self.git('commit', '-qm', 'accepted task on side branch')
        side = self.git('rev-parse', 'HEAD').stdout.strip()
        self.git('checkout', '-q', '--detach', self.base)
        self.candidate(next_id='SIDE-DONE')
        tree = self.git('write-tree').stdout.strip()
        # Explicit merge tree models a resolved merge without trusting worktree receipts.
        merge = self.git('commit-tree', tree, '-p', self.base, '-p', side, '-m', 'synthetic merge').stdout.strip()
        checked = self.run_cmd([sys.executable, str(ROOT / 'scripts/verify-build-checkpoint.py'), '--repo', str(self.repo), '--commit', merge])
        self.assertNotEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_policy_in_second_merge_parent_cannot_be_dropped(self):
        adopted = self.base
        self.git('checkout', '-q', '--detach', self.legacy)
        self.base = self.legacy
        self.candidate(research=False)
        tree = self.git('write-tree').stdout.strip()
        merge = self.git('commit-tree', tree, '-p', self.legacy, '-p', adopted, '-m', 'merge discards second-parent policy').stdout.strip()
        checked = self.run_cmd([sys.executable, str(ROOT / 'scripts/verify-build-checkpoint.py'), '--repo', str(self.repo), '--commit', merge])
        self.assertNotEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_no_change_merge_cannot_resume_task_completed_on_second_parent(self):
        receipt = self.candidate(next_id='SIDE-DONE')
        self.git('commit', '-qm', 'first parent points to unfinished task')
        first = self.git('rev-parse', 'HEAD').stdout.strip()
        self.git('checkout', '-q', '--detach', self.base)
        side = self.candidate()
        side['task_id'] = 'SIDE-DONE'
        status_path = self.repo / 'CURRENT_STATUS.md'
        status_path.write_text(status_path.read_text().replace('"task_id": "NEW-1"', '"task_id": "SIDE-DONE"'))
        self.stage_receipt(side)
        self.git('commit', '-qm', 'side task completed')
        second = self.git('rev-parse', 'HEAD').stdout.strip()
        tree = self.git('rev-parse', first + '^{tree}').stdout.strip()
        merge = self.git('commit-tree', tree, '-p', first, '-p', second, '-m', 'unchanged first-parent status').stdout.strip()
        checked = self.run_cmd([sys.executable, str(ROOT / 'scripts/verify-build-checkpoint.py'), '--repo', str(self.repo), '--commit', merge])
        self.assertNotEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn('repeats a completed task', checked.stderr)

    def test_no_change_merge_cannot_discard_second_parent_policy(self):
        tree = self.git('rev-parse', self.legacy + '^{tree}').stdout.strip()
        merge = self.git('commit-tree', tree, '-p', self.legacy, '-p', self.base, '-m', 'discard adoption').stdout.strip()
        checked = self.run_cmd([sys.executable, str(ROOT / 'scripts/verify-build-checkpoint.py'), '--repo', str(self.repo), '--commit', merge])
        self.assertNotEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn('cannot be removed', checked.stderr)

    def test_staged_merge_cannot_discard_second_parent_policy(self):
        adopted = self.base
        self.git('checkout', '-q', '--detach', self.legacy)
        # Synthetic unresolved-history fixture; index itself is resolved and unchanged.
        merge_path = Path(self.git('rev-parse', '--git-path', 'MERGE_HEAD').stdout.strip())
        if not merge_path.is_absolute():
            merge_path = self.repo / merge_path
        merge_path.write_text(adopted + '\n')
        checked = self.run_cmd([sys.executable, str(ROOT / 'scripts/verify-build-checkpoint.py'), '--repo', str(self.repo), '--staged'])
        self.assertNotEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        self.assertIn('cannot be removed', checked.stderr)


if __name__ == '__main__':
    unittest.main()
