"""Adversarial policy cases; no network or repository mutations."""
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('delivery', Path(__file__).resolve().parents[1] / 'tools/github/delivery_controller.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
HEAD = 'a' * 40
BASE = 'b' * 40
BOT = 'heleos-automation-bbukolla[bot]'
ACTIVATION = '2026-09-23T00:00:00Z'


class DeliveryPolicy(unittest.TestCase):
    def setUp(self):
        self.pr = dict(head={'sha':HEAD,'repo':{'full_name':m.REPO}},base={'ref':'main','sha':BASE},state='open',draft=False,mergeable=True,mergeable_state='clean',created_at=ACTIVATION,labels=[{'name':'heleos-delivery'}],changed_files=1,user={'login':'author'},commits=1)
        self.files = [{'filename':'src/quantity.py'}]
        self.review = [{'id':1,'user':{'login':'coderabbitai[bot]','type':'Bot'},'state':'APPROVED','commit_id':HEAD}]
        self.comments = []
        self.checks = [dict(id=i,name=name,head_sha=HEAD,conclusion='success',app={'id':15368}) for i,name in enumerate(['checks','dependency-review'])]
        self.checks.append(dict(id=3,name='CodeQL',head_sha=HEAD,conclusion='success',app={'slug':'github-code-scanning'}))
        self.writers = {'author'}

    def result(self):
        return m.evaluate(self.pr,self.files,self.review,self.comments,self.checks,self.writers,BASE,ACTIVATION,BOT,security_protected=True)

    def fix(self):
        self.pr['labels']=[{'name':'autofix'},{'name':'heleos-delivery'}]
        self.review[0]['state']='CHANGES_REQUESTED'
        return m.fix_request(self.pr,self.files,self.review,self.comments,ACTIVATION,BOT)

    def test_routine_checkpoint_evidence_does_not_need_owner_reapproval(self):
        for name in ['CURRENT_STATUS.md','docs/operations/build-checkpoint.json','docs/operations/task-proof.json','scripts/equipment_calculation.py']:
            with self.subTest(name=name):
                self.files=[{'filename':name}]
                self.assertTrue(self.result()['eligible'])

    def test_unenrolled_pr_is_not_automatically_merged(self):
        self.pr['labels']=[]
        self.assertFalse(self.result()['eligible'])

    def test_complete_current_head_eligible(self): self.assertTrue(self.result()['eligible'])

    def test_approval_requires_current_head(self):
        self.review[0]['commit_id']='c'*40
        self.assertFalse(self.result()['eligible'])

    def test_comment_is_not_approval(self):
        self.review[0]['state']='COMMENTED'
        self.assertFalse(self.result()['eligible'])

    def test_comment_does_not_withdraw_approval_but_dismissal_does(self):
        self.review.append(dict(self.review[0],id=2,state='COMMENTED'))
        self.assertTrue(self.result()['eligible'])
        self.review.append(dict(self.review[0],id=3,state='DISMISSED'))
        self.assertFalse(self.result()['eligible'])

    def test_governing_documents_require_sensitive_consent(self):
        for name in ['GOAL.md','ROADMAP.md','docs/architecture/decisions/0001-foundation-runtime.md','docs/roadmap/skills-and-plugins.md','docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md']:
            with self.subTest(name=name):
                self.files=[{'filename':name}]
                self.assertFalse(self.result()['eligible'])

    def test_writer_cannot_supply_independent_review(self):
        self.writers.add('coderabbitai[bot]')
        self.assertFalse(self.result()['eligible'])

    def test_latest_changes_requested_blocks(self):
        r=copy.deepcopy(self.review[0]);r.update(id=2,state='CHANGES_REQUESTED');self.review.append(r)
        self.assertFalse(self.result()['eligible'])

    def test_check_wrong_publisher_or_stale_fails(self):
        for field,value in [('app',{'id':999}),('head_sha','c'*40),('conclusion','failure')]:
            with self.subTest(field=field):
                old=self.checks[0][field];self.checks[0][field]=value
                self.assertFalse(self.result()['eligible']);self.checks[0][field]=old

    def test_new_failed_run_overrides_old_pass(self):
        self.checks.append(dict(self.checks[0],id=10,conclusion='failure'))
        self.assertFalse(self.result()['eligible'])

    def test_unverified_native_security_blocks(self):
        result=m.evaluate(self.pr,self.files,self.review,self.comments,self.checks,self.writers,BASE,ACTIVATION,BOT)
        self.assertFalse(result['eligible'])

    def test_native_codeql_does_not_require_nonexistent_publisher(self):
        self.checks.pop()
        self.assertTrue(self.result()['eligible'])

    def test_renamed_sensitive_path_needs_exact_owner_consent(self):
        self.files[0]['previous_filename']='.github/workflows/trusted.yml'
        self.assertFalse(self.result()['eligible'])
        self.comments=[{'user':{'login':m.OWNER},'body':'Heleos owner consent '+HEAD}]
        self.assertTrue(self.result()['eligible'])
        self.comments[0]['body']='Heleos owner consent '+'c'*40
        self.assertFalse(self.result()['eligible'])

    def test_fork_old_pr_changed_target_and_hold_fail(self):
        for key,value in [('created_at','2020-01-01T00:00:00Z'),('base',{'ref':'main','sha':'c'*40}),('head',{'sha':HEAD,'repo':{'full_name':'other/repo'}}),('labels',[{'name':'automation-hold'}]),('draft',True),('changed_files',2),('mergeable',None)]:
            with self.subTest(key=key):
                old=self.pr[key];self.pr[key]=value
                self.assertFalse(self.result()['eligible']);self.pr[key]=old

    def test_fix_is_separate_stacked_candidate(self):
        self.assertIn('@coderabbitai autofix stacked pr',self.fix())

    def test_fix_never_changes_sensitive_paths(self):
        self.files[0]['previous_filename']='.github/workflows/foo.yml'
        self.assertIsNone(self.fix())

    def test_fix_dedup_and_retry_bound(self):
        message=self.fix()
        self.comments=[{'user':{'login':BOT},'body':message}]
        self.assertIsNone(self.fix())
        self.comments=[{'user':{'login':BOT},'body':f'<!-- heleos-autofix:{x*40} -->'} for x in ['b','c']]
        self.assertIsNone(self.fix())

    def test_untrusted_comment_cannot_exhaust_fix_budget(self):
        message=self.fix();self.comments=[{'user':{'login':'stranger'},'body':message}]
        self.assertIsNotNone(self.fix())

    def test_hold_blocks_fix(self):
        self.fix();self.pr['labels'].append({'name':'automation-hold'})
        self.assertIsNone(m.fix_request(self.pr,self.files,self.review,self.comments,ACTIVATION,BOT))

    def test_reviewer_authored_fix_needs_another_reviewer(self):
        self.pr['user']['login']='coderabbitai[bot]'
        self.assertFalse(self.result()['eligible']);self.assertIsNone(self.fix())

    def test_pagination_does_not_truncate(self):
        with patch.object(m,'gh',side_effect=[[{}]*100,[{'id':101}]]) as api:
            self.assertEqual(101,len(m.pages('endpoint')))
            self.assertIn('page=2',api.call_args.args[0])

    def test_main_ci_only_accepts_latest_matching_push(self):
        runs=[dict(id=1,head_sha=BASE,head_branch='main',event='push',path='.github/workflows/ci.yml',conclusion='success')]
        self.assertFalse(m.main_ci_ready(runs,BASE))  # security absent
        runs.append(dict(runs[0],id=3,event='dynamic',path='dynamic/github-code-scanning/codeql'))
        self.assertTrue(m.main_ci_ready(runs,BASE))
        self.assertFalse(m.main_ci_ready(runs,HEAD))
        runs.append(dict(runs[0],id=2,conclusion='failure'))
        self.assertFalse(m.main_ci_ready(runs,BASE))
        runs[2].update(conclusion='success',event='pull_request')
        self.assertTrue(m.main_ci_ready(runs,BASE))

    def test_protection_drift_stops_delivery(self):
        import json
        root=Path(__file__).resolve().parents[1]
        rule=json.loads((root/'docs/operations/github-automation-2026-09-23/ruleset-after.json').read_text())
        settings=dict(default_branch='main',allow_merge_commit=True,allow_squash_merge=False,allow_rebase_merge=False)
        self.assertFalse(m.protection_ready(rule,settings))  # unpublished dependency review
        for r in rule['rules']:
            if r['type']=='required_status_checks':r['parameters']['required_status_checks'].append({'context':'dependency-review','integration_id':15368})
        self.assertTrue(m.protection_ready(rule,settings))
        rule['bypass_actors']=[{'actor_id':123,'actor_type':'Integration','bypass_mode':'always'}]
        self.assertFalse(m.protection_ready(rule,settings))
        rule['bypass_actors']=[]
        settings['allow_squash_merge']=True
        self.assertFalse(m.protection_ready(rule,settings))

    def test_refresh_rejects_revoked_consent(self):
        self.files=[{'filename':'.github/workflows/delivery.yml'}]
        self.comments=[{'user':{'login':m.OWNER},'body':'Heleos owner consent '+HEAD}]
        self.assertTrue(self.result()['eligible'])
        with patch.object(m,'gh',return_value=self.pr), patch.object(m,'pages',side_effect=[self.review,[],self.checks]):
            _,result,_,_=m.refresh_candidate('pr-url',26,HEAD,self.files,self.writers,BASE,ACTIVATION,BOT,security_protected=True)
        self.assertFalse(result['eligible'])
        self.assertIn('sensitive change requires owner consent for this head',result['reasons'])

    def test_unzoned_activation_rejected(self):
        with self.assertRaises(ValueError):m.stamp('2026-09-23T00:00:00')


if __name__=='__main__':unittest.main()
