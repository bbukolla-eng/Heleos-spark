"""Atomic source-region admissions, integrity, replay and selective stale views."""
import copy
import io
import json
from pathlib import Path
import unittest
from unittest import mock

from test_equipment_calculation import load,request,ROOT

project=load('project_equipment_takeoff')


class Workspace:
    def __init__(self):
        self.metadata={'project':{'id':'project-one'}}
        self.calls=[]
    def verified_pdf(self,revision):
        self.calls.append(revision)
        return io.BytesIO(b'verified by Foundation fixture'),{}


class ProjectEquipmentTests(unittest.TestCase):
    def setUp(self):
        self.workspace=Workspace()
        self.data={'project_id':'project-one','project':{'scopes':['equipment']},'pages':{'a'*64+':0':{'role':'plan'}}}
        self.sheets={'a'*64+':0':{'sheet_id':'b'*64}}
        self.patch=mock.patch.object(project,'_geometry',return_value='c'*64);self.patch.start();self.addCleanup(self.patch.stop)
        project.initialize(self.data)
    def view(self):return project.view(self.workspace,self.data,self.sheets)
    def save(self,value=None,generation=None):
        if generation is None:generation=self.data['equipment_count_current']
        return project.apply(self.workspace,self.data,self.sheets,'equipment_count_save',
            {'generation':generation,'request':request('EC20') if value is None else value},'Estimator','Reviewed physical source region')

    def test_empty_view_has_bound_request_and_current_sources(self):
        view=self.view();self.assertFalse(view['available']);self.assertIsNone(view['result'])
        self.assertEqual(view['request']['binding'],project.core.rule_binding())
        self.assertEqual(len(view['current_source_contexts']),1)
        self.assertEqual(self.workspace.calls,[])

    def test_save_correction_reopen_and_export_without_model_or_renderer(self):
        self.save();first=self.view();self.assertEqual(first['result']['total_each'],4)
        corrected=copy.deepcopy(first['request']);corrected['observations'][2]['disposition']='exclude'
        self.save(corrected);view=self.view();self.assertEqual(view['result']['total_each'],3)
        self.assertEqual(view['history'][0]['result']['total_each'],4)
        frozen=json.loads(json.dumps(self.data));before=len(self.workspace.calls)
        reopened=project.view(self.workspace,frozen,self.sheets)
        self.assertEqual(reopened['result'],view['result']);self.assertEqual(len(self.workspace.calls),before)
        files=project.export_files(self.workspace,frozen,self.sheets)
        self.assertIn('equipment-counts.csv',files);self.assertIn('equipment-count-history.json',files)
        self.assertIn(('a'*64).encode(),files['equipment-counts.csv'])
        self.assertEqual(frozen,self.data)

    def test_replay_same_intent_and_stale_generation_are_atomic(self):
        values={'generation':None,'request':request()}
        project.apply(self.workspace,self.data,self.sheets,'equipment_count_save',values,'Estimator','Reviewed physical source region')
        frozen=copy.deepcopy(self.data)
        project.apply(self.workspace,self.data,self.sheets,'equipment_count_save',values,'Estimator','Reviewed physical source region')
        self.assertEqual(self.data,frozen)
        changed=copy.deepcopy(values);changed['request']['coverage']['state']='unknown'
        with self.assertRaises(project.ProjectEquipmentError):
            project.apply(self.workspace,self.data,self.sheets,'equipment_count_save',changed,'Estimator','Reviewed physical source region')
        self.assertEqual(self.data,frozen)

    def test_source_drift_stales_affected_group_only(self):
        value=request('EC20');other=copy.deepcopy(value['sources'][0]);other['source']['index']=1
        value['sources'].append(other);otherkey=project.core.fingerprint(other['source']);value['scope']['source_keys'].append(otherkey)
        self.sheets['a'*64+':1']={'sheet_id':'b'*64};self.data['pages']['a'*64+':1']={'role':'plan'}
        other_evidence=copy.deepcopy(value['evidence'][0]);other_evidence.update(id='other',source=other['source']);value['evidence'].append(other_evidence)
        cover=copy.deepcopy(other_evidence);cover.update(id='other-cover',kind='coverage');value['evidence'].append(cover);value['coverage']['evidence_ids'].append('other-cover')
        value['observations'][-1].update(source=other['source'],evidence_ids=['other'])
        self.save(value);before=self.view();self.data['pages']['a'*64+':0']['role']='legend'
        after=self.view();self.assertTrue(after['stale']);self.assertIsNone(after['result']['total_each'])
        group=next(g for g in after['result']['groups'] if g['key']['family']=='ahu')
        self.assertEqual(group['total_each'],1)
        self.assertEqual(next(r for r in before['result']['rows'] if r['family']=='ahu'),next(r for r in after['result']['rows'] if r['family']=='ahu'))

    def test_source_forgery_and_original_verification_failure_reject_atomically(self):
        before=copy.deepcopy(self.data);value=request();value['sources'][0]['source']['geometry_fingerprint']='d'*64
        with self.assertRaises((project.ProjectEquipmentError,ValueError)):self.save(value)
        self.assertEqual(before,self.data)
        with mock.patch.object(self.workspace,'verified_pdf',side_effect=ValueError('original changed')):
            with self.assertRaises(project.ProjectEquipmentError):self.save()
        self.assertEqual(before,self.data)

    def test_rehashed_result_and_event_tampering_fail_reopen(self):
        self.save();self.data['equipment_count_generations'][0]['result']['total_each']=900
        item=self.data['equipment_count_generations'][0];item['sha256']=project.core.fingerprint({k:v for k,v in item.items() if k!='sha256'})
        with self.assertRaises(project.ProjectEquipmentError):self.view()

    def test_code_drift_blocks_writes_but_marks_saved_result_stale(self):
        self.save();before=copy.deepcopy(self.data)
        with mock.patch.object(project,'_implementation',return_value={'changed':'yes'}):
            view=self.view();self.assertTrue(view['stale']);self.assertIsNone(view['result']['total_each'])
        self.assertEqual(self.data,before)

    def test_explicit_review_after_code_reload_clears_staleness(self):
        self.save();saved=copy.deepcopy(self.view()['request']);prior=self.data['equipment_count_current']
        changed={'new_implementation':'v2'}
        with mock.patch.object(project,'_implementation',return_value=changed),mock.patch.object(project,'LOADED_IMPLEMENTATION',changed):
            self.assertTrue(self.view()['stale'])
            self.save(saved,generation=prior)
            self.assertFalse(self.view()['stale'])
            self.assertNotEqual(prior,self.data['equipment_count_current'])
            self.assertEqual(len(self.view()['history']),2)


if __name__=='__main__':unittest.main()
