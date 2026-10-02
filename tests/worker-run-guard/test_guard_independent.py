"""Controller-defined regressions from independently reproduced guard defects."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from test_build_run_guard import GuardTestCase, IntegrationFixture, SCRIPT, guard_module as g, sha


class IndependentGuardChecks(GuardTestCase):
    def test_live_pid_missing_metadata_remains_uncertain(self):
        with patch.object(g, '_ps', return_value=''):
            self.assertEqual(g.identity_state(os.getpid(), 'known'), 'unverifiable_live')

    def test_process_table_failure_is_not_empty_table(self):
        failed = subprocess.CompletedProcess(['ps'], 2, '', 'observation failed')
        with patch.object(g.subprocess, 'run', return_value=failed):
            with self.assertRaises(g.GuardError) as cm:
                g.process_table()
        self.assertEqual(cm.exception.reason, 'process_observation_failed')

    def test_review_of_other_base_or_destinations_is_rejected(self):
        fx = IntegrationFixture(self)
        for override, reason in [({'expected_head': '0' * 40}, 'review_base_mismatch'),
                                 ({'before_files': {'a.txt': sha(b'wrong')}}, 'review_destination_mismatch')]:
            fx.write_review(**override)
            code, result = self.guard('integrate', '--manifest', fx.manifest_path())
            self.assertEqual((code, result['reason']), (4, reason))
            self.assertEqual((self.repo / 'a.txt').read_bytes(), b'old\n')
            self.assertFalse((self.repo / 'sub/b.txt').exists())

    def test_head_changes_between_files_leaves_partial_journal(self):
        fx = IntegrationFixture(self)
        manifest = fx.manifest_path()
        ctx = g.resolve_context(str(self.repo))
        original = g.apply_file
        moved = False

        def apply_then_head_moves(*args):
            nonlocal moved
            original(*args)
            moved = True

        args = g.build_parser().parse_args(['integrate', '--manifest', manifest])
        with patch.object(g, 'apply_file', side_effect=apply_then_head_moves), \
             patch.object(g, 'current_head', side_effect=lambda _ctx: '0' * 40 if moved else self.head):
            code, result = g.cmd_integrate(ctx, args)
        self.assertEqual(code, 5)
        self.assertEqual(result['record']['state'], 'needs_reconciliation')
        self.assertEqual((self.repo / 'a.txt').read_bytes(), b'new\n')
        self.assertFalse((self.repo / 'sub/b.txt').exists())

    def test_real_sigkill_mid_integration_retains_journal_and_blocks_replay(self):
        fx = IntegrationFixture(self)
        manifest = fx.manifest_path()
        driver = '''import importlib.util, os, signal, sys
spec=importlib.util.spec_from_file_location("guard",sys.argv[1]);g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
original=g.apply_file
def crash_after_first_write(*args):
 original(*args)
 os.kill(os.getpid(), signal.SIGKILL)
g.apply_file=crash_after_first_write
g.main(["--repo",sys.argv[2],"integrate","--manifest",sys.argv[3]])
'''
        child = subprocess.run([sys.executable, '-B', '-c', driver, str(SCRIPT), str(self.repo), manifest],
                               env=self.env, capture_output=True, timeout=20)
        self.assertEqual(child.returncode, -9)
        code, status = self.status(fx.task_id)
        self.assertEqual(code, 5)
        self.assertEqual((self.repo / 'a.txt').read_bytes(), b'new\n')
        self.assertFalse((self.repo / 'sub/b.txt').exists())
        self.assertIn('writing', [row['step'] for row in status['task']['journal']])
        self.assertEqual(self.guard('integrate', '--manifest', manifest)[0], 5)
        ev, digest = self.evidence(fx.task_id)
        self.assertEqual(self.reconcile(fx.task_id, ev, digest)[1]['reason'], 'integration_journal_not_reconciled')
        ev, digest = self.evidence(fx.task_id, integration_files={'a.txt': sha(b'new\n'), 'sub/b.txt': None})
        self.assertEqual(self.reconcile(fx.task_id, ev, digest)[0], 0)
        self.assertEqual(self.guard('integrate', '--manifest', manifest)[0], 4)


if __name__ == '__main__':
    unittest.main()
