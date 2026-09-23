import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('publisher', ROOT/'tools/github/publish_task.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def git(repo, *args):
    return subprocess.check_output(['git','-C',str(repo),*args],stderr=subprocess.DEVNULL).decode().strip()


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.scratch=tempfile.TemporaryDirectory();self.addCleanup(self.scratch.cleanup)
        root=Path(self.scratch.name);self.main=root/'source';self.main.mkdir();self.repo=root/'task';self.remote=root/'remote.git'
        git(self.main,'init','-b','main');git(self.main,'config','user.email','test@example.invalid');git(self.main,'config','user.name','Test')
        (self.main/'scripts').mkdir();shutil.copy(ROOT/'scripts/verify-build-checkpoint.py',self.main/'scripts/verify-build-checkpoint.py')
        (self.main/'a.txt').write_text('before\n');git(self.main,'add','.');git(self.main,'commit','-m','base')
        self.base=git(self.main,'rev-parse','HEAD');git(self.main,'worktree','add','--detach',str(self.repo),self.base)
        subprocess.run(['git','init','--bare',str(self.remote)],capture_output=True,check=True)
        git(self.main,'remote','add','origin',str(self.remote));self.patch=patch.object(m,'ORIGINS',{str(self.remote)});self.patch.start();self.addCleanup(self.patch.stop)
        (self.repo/'a.txt').write_text('after\n');(self.repo/'proof.txt').write_text('checked by reviewer\n')
        status={'schema_version':1,'receipt':m.RECEIPT,'task_id':'TASK-1','outcome':'complete','next_task_id':'TASK-2','next_action':'Implement the next task.'}
        (self.repo/'CURRENT_STATUS.md').write_text('**Primary product task: TASK-2.**\n\n**Next executable action:** Implement the next task.\n\n<!-- build-checkpoint:v1 -->\n```json\n'+json.dumps(status)+'\n```\n<!-- /build-checkpoint -->\n')
        paths=['a.txt','proof.txt','CURRENT_STATUS.md'];hashes={p:hashlib.sha256((self.repo/p).read_bytes()).hexdigest() for p in paths}
        receipt={'schema_version':1,'task_id':'TASK-1','outcome':'complete','base_commit':self.base,'summary':'Complete the task.','remaining_limits':['No remote verification yet.'],'next_task_id':'TASK-2','next_action':'Implement the next task.','changed_files':[{'path':p,'sha256':h} for p,h in hashes.items()],'checks':[{'command':'fixture check','exit_code':0,'evidence':{'path':'proof.txt','sha256':hashes['proof.txt']}}],'review':{'reviewer':'Independent fixture reviewer','outcome':'accepted','evidence':{'path':'proof.txt','sha256':hashes['proof.txt']}},'missing_prerequisite':None,'independent_action':None,'notebooklm_research':{'disposition':'reused_verified_findings'}}
        dest=self.repo/m.RECEIPT;dest.parent.mkdir(parents=True);dest.write_text(json.dumps(receipt))
        hashes[m.RECEIPT]=hashlib.sha256(dest.read_bytes()).hexdigest()
        self.request=dict(schema_version=1,task_id='TASK-1',base_commit=self.base,branch='codex/task-1',title='Complete task one',body='Scoped completion with independent fixture evidence.',paths=hashes)
        self.calls=[]
        def api(endpoint,data=None,method=None):
            self.calls.append((endpoint,data,method))
            if '/pulls?' in endpoint:return []
            if endpoint.endswith('/pulls'):return {'number':99,'html_url':'https://github.com/bbukolla-eng/Heleos-spark/pull/99','head':{'sha':git(self.repo,'rev-parse','HEAD')}}
            return {}
        self.api=patch.object(m,'api',side_effect=api);self.api.start();self.addCleanup(self.api.stop)

    def test_dry_run_does_not_stage_or_commit(self):
        before=git(self.repo,'status','--porcelain')
        result=m.publish(self.repo,self.request,False)
        self.assertEqual('ready',result['state']);self.assertEqual(self.base,git(self.repo,'rev-parse','HEAD'))
        self.assertEqual(before,git(self.repo,'status','--porcelain'));self.assertEqual([],self.calls)

    def test_complete_task_commits_pushes_and_opens_pr(self):
        result=m.publish(self.repo,self.request,True)
        self.assertEqual('published',result['state']);self.assertEqual(99,result['pr_number'])
        self.assertEqual(result['commit'],git(self.remote,'rev-parse','refs/heads/codex/task-1'))
        self.assertEqual(self.base,git(self.repo,'rev-parse','HEAD^'))

    def test_unrelated_untracked_preserved(self):
        (self.repo/'private-note.txt').write_text('leave alone')
        m.publish(self.repo,self.request,True)
        self.assertTrue((self.repo/'private-note.txt').exists());self.assertNotIn('private-note.txt',git(self.repo,'ls-tree','--name-only','HEAD'))

    def test_unrelated_staging_rejected(self):
        (self.repo/'other.txt').write_text('other');git(self.repo,'add','other.txt')
        with self.assertRaises(m.PublishError):m.publish(self.repo,self.request,True)
        self.assertEqual(self.base,git(self.repo,'rev-parse','HEAD'))

    def test_drift_and_bad_path_rejected(self):
        (self.repo/'a.txt').write_text('drift')
        with self.assertRaises(m.PublishError):m.publish(self.repo,self.request,False)
        self.request['paths']['../outside']=None
        with self.assertRaises(m.PublishError):m.publish(self.repo,self.request,False)

    def test_incomplete_or_missing_research_rejected(self):
        for key,value in [('outcome','in_progress'),('notebooklm_research',None),('review',None)]:
            with self.subTest(key=key):
                path=self.repo/m.RECEIPT;original=path.read_text();data=json.loads(original);data[key]=value;path.write_text(json.dumps(data));self.request['paths'][m.RECEIPT]=hashlib.sha256(path.read_bytes()).hexdigest()
                with self.assertRaises(m.PublishError):m.publish(self.repo,self.request,False)
                path.write_text(original);self.request['paths'][m.RECEIPT]=hashlib.sha256(path.read_bytes()).hexdigest()

    def test_primary_checkout_rejected(self):
        with self.assertRaises(m.PublishError):m.publish(self.main,self.request,False)

    def test_push_failure_resumes_same_commit(self):
        real=m.run
        def fail_push(repo,*args,**kw):
            if args and args[0]=='push':raise m.PublishError('network interrupted')
            return real(repo,*args,**kw)
        with patch.object(m,'run',side_effect=fail_push):
            with self.assertRaises(m.PublishError):m.publish(self.repo,self.request,True)
        committed=git(self.repo,'rev-parse','HEAD');self.assertNotEqual(self.base,committed)
        result=m.publish(self.repo,self.request,True)
        self.assertEqual(committed,result['commit']);self.assertEqual(committed,git(self.repo,'rev-parse','HEAD'))

    def test_foreign_commit_cannot_be_resumed_as_accepted_task(self):
        git(self.repo,'switch','-c','codex/task-1')
        git(self.repo,'add','a.txt')
        git(self.repo,'commit','-m','Unrelated partial commit')
        with self.assertRaises(m.PublishError):m.publish(self.repo,self.request,True)

    def test_existing_pr_reused_without_duplicate(self):
        first=m.publish(self.repo,self.request,True)
        pr={'number':99,'state':'open','html_url':first['pr_url'],'body':'<!-- heleos-delivery:TASK-1 -->','head':{'sha':first['commit']}}
        with patch.object(m,'api',side_effect=lambda endpoint,data=None,method=None: [pr] if '/pulls?' in endpoint else {}) as api:
            second=m.publish(self.repo,self.request,True)
        self.assertEqual(first['commit'],second['commit'])
        self.assertFalse(any(c.args[0].endswith('/pulls') for c in api.call_args_list))

    def test_unapproved_remote_rejected(self):
        git(self.main,'remote','set-url','origin','https://example.invalid/other/repo.git')
        with self.assertRaises(m.PublishError):m.publish(self.repo,self.request,True)

    def test_request_can_be_created_from_accepted_checkpoint(self):
        request=m.from_checkpoint(self.repo,'codex/task-1')
        self.assertEqual(self.request['paths'],request['paths'])
        self.assertEqual('TASK-1',request['task_id'])
        self.assertEqual('ready',m.publish(self.repo,request,False)['state'])

    def test_symlink_parent_rejected(self):
        (self.repo/'link').symlink_to(self.main,target_is_directory=True);self.request['paths']['link/a.txt']=hashlib.sha256(b'before\n').hexdigest()
        with self.assertRaises(m.PublishError):m.publish(self.repo,self.request,False)


if __name__=='__main__':unittest.main()
