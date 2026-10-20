"""Approved physical equipment counts through the real saved workflow.

Source regions are explicitly reviewed synthetic fixtures, not recognition
qualification. Expected counts come from EC01/EC20, not the count kernel.
"""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import test_takeoff_workflow as fixtures


class EquipmentCountWorkflowTests(unittest.TestCase):
    http = fixtures.WorkflowTests.http

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = fixtures.Workspace(Path(self.temp.name))
        reader = fixtures.ProjectReader()
        self.workspace.equipment = fixtures.drawing.EquipmentEngine(self.workspace, text_reader=reader)
        self.addCleanup(self.workspace.equipment.close)
        self.workspace.documents = fixtures.drawing.DocumentPipeline(self.workspace, reader)
        self.addCleanup(self.workspace.documents.close)
        self.flow = fixtures.drawing.TakeoffWorkflow(self.workspace)
        self.workspace.workflow = self.flow
        self.addCleanup(lambda: self.workspace.workflow.close())
        self.command('project', {'name': 'Physical equipment', 'location': 'Synthetic source fixture',
                                'units': 'ft', 'scopes': ['equipment']})
        for index, role in enumerate(('plan', 'schedule', 'plan')):
            self.command('page', {'source': {'revision_id': fixtures.REVISION, 'index': index},
                                 'role': role, 'label': 'M' + str(index), 'building': 'A',
                                 'level': '1', 'note': 'Reviewed fixture role'})

    def command(self, action, values):
        return self.flow.command(action, {'version': self.flow.data['version'],
            'actor': 'Fixture estimator', 'reason': 'Checked original fixture region', 'values': values})

    def draft(self):
        view = self.flow.view()['equipment_count_takeoff']
        request = copy.deepcopy(view['request'])
        contexts = {c['source']['index']: c for c in view['current_source_contexts']}
        request['sources'] = [{k: v for k, v in contexts[i].items() if k != 'source_key'} for i in (0, 2)]
        request['scope']['source_keys'] = [contexts[i]['source_key'] for i in (0, 2)]
        request['scope']['work_statuses'] = ['new']
        request['coverage'] = {'state': 'complete', 'evidence_ids': [], 'unresolved_requirements': []}
        for page in (0, 2):
            eid = 'coverage-' + str(page)
            context = contexts[page]
            request['evidence'].append({'id': eid, 'source': copy.deepcopy(context['source']),
                'bbox': [0, 0, 1, 1], 'kind': 'coverage', 'text': 'Reviewed complete selected equipment scope',
                'artifact_sha256': context['artifact_sha256']})
            request['coverage']['evidence_ids'].append(eid)
        for ordinal, (identifier, family, page) in enumerate([
                ('pump-1', 'pump', 0), ('pump-2', 'pump', 0), ('pump-3', 'pump', 0), ('ahu-1', 'ahu', 2)]):
            context = contexts[page]
            bounds = [.05 + ordinal * .2, .2, .18 + ordinal * .2, .4]
            eid = 'graphic-' + identifier
            request['evidence'].append({'id': eid, 'source': copy.deepcopy(context['source']),
                'bbox': bounds, 'kind': 'graphic', 'text': 'Physical assembly ' + identifier,
                'artifact_sha256': context['artifact_sha256']})
            request['observations'].append({'id': identifier, 'source': copy.deepcopy(context['source']),
                'bbox': bounds, 'kind': 'assembly', 'family': family, 'tag': identifier.upper(),
                'work_status': 'new', 'disposition': 'include', 'identity': 'established',
                'procurement': 'separate', 'installation': 'field', 'parent_id': None,
                'system_id': None, 'attributes': {}, 'evidence_ids': [eid], 'issues': []})
        return request

    def save(self, request):
        current = self.flow.view()['equipment_count_takeoff']
        return self.command('equipment_count_save', {'generation': current['generation'], 'request': request})

    def test_correct_reopen_replay_and_export_preserve_prior_counts(self):
        original = self.draft()
        view = self.save(original)
        counts = view['equipment_count_takeoff']
        self.assertEqual(counts['result']['total_each'], 4)
        self.assertEqual(sorted(g['total_each'] for g in counts['result']['groups']), [1, 3])
        prior_generation = counts['generation']
        corrected = copy.deepcopy(counts['request'])
        corrected['observations'][2]['disposition'] = 'exclude'
        view = self.save(corrected)
        counts = view['equipment_count_takeoff']
        self.assertEqual(counts['result']['total_each'], 3)
        self.assertEqual(sorted(g['total_each'] for g in counts['result']['groups']), [1, 2])
        old = next(g for g in counts['history'] if g['id'] == prior_generation)
        self.assertEqual(old['result']['total_each'], 4)
        history_size = len(counts['history'])
        self.assertEqual(len(self.save(corrected)['equipment_count_takeoff']['history']), history_size)
        self.flow.close()
        self.flow = fixtures.drawing.TakeoffWorkflow(self.workspace)
        self.workspace.workflow = self.flow
        reopened = self.flow.view()['equipment_count_takeoff']
        self.assertEqual(reopened['result']['total_each'], 3)
        self.assertEqual(reopened['history'], counts['history'])
        with zipfile.ZipFile(io.BytesIO(self.flow.export())) as archive:
            self.assertIn('equipment-counts.json', archive.namelist())
            exported = json.loads(archive.read('equipment-counts.json'))
            self.assertEqual(exported['result']['total_each'], 3)
            self.assertIn(fixtures.REVISION.encode(), archive.read('equipment-counts.csv'))
            with zipfile.ZipFile(io.BytesIO(archive.read('takeoff.xlsx'))) as book:
                self.assertIn(b'Equipment', book.read('xl/workbook.xml'))
                self.assertIn(fixtures.REVISION.encode(), book.read('xl/worksheets/sheet5.xml'))

    def test_stale_generation_and_scope_removal_do_not_change_saved_state(self):
        request = self.draft()
        self.save(request)
        before = copy.deepcopy(self.flow.data)
        stale_edit = copy.deepcopy(request)
        stale_edit['observations'][2]['disposition'] = 'exclude'
        with self.assertRaises(fixtures.wf.WorkflowError):
            self.command('equipment_count_save', {'generation': None, 'request': stale_edit})
        self.assertEqual(self.flow.data, before)
        with self.assertRaises(fixtures.wf.WorkflowError) as caught:
            self.command('project', {'name': 'Physical equipment', 'location': '', 'units': 'ft', 'scopes': ['ductwork']})
        self.assertEqual(caught.exception.code, 'scope_has_records')
        self.assertEqual(self.flow.data, before)

    def test_source_role_change_blocks_only_affected_physical_rows(self):
        self.save(self.draft())
        view = self.command('page', {'source': {'revision_id': fixtures.REVISION, 'index': 0},
            'role': 'legend', 'label': 'Legend', 'building': 'A', 'level': '1', 'note': 'Corrected role'})
        result = view['equipment_count_takeoff']['result']
        self.assertIsNone(result['total_each'])
        self.assertEqual(result['known_subtotal_each'], 1)
        ahu = next(row for row in result['rows'] if row['family'] == 'ahu')
        self.assertEqual(ahu['physical_each'], 1)

    def test_no_physical_review_and_generic_items_cannot_finish_equipment_scope(self):
        counts = self.flow.view()['equipment_count_takeoff']
        self.assertFalse(counts['available'])
        self.command('item', {'id': None, 'scope': 'equipment', 'description': 'Draft quantity',
            'system': '', 'size': '', 'material': '', 'quantity': '12', 'unit': 'each',
            'evidence_class': 'drawn', 'source': {'revision_id': fixtures.REVISION, 'index': 0},
            'measurement_id': None, 'note': 'Not physical identity'})
        with self.assertRaises(fixtures.wf.WorkflowError) as caught:
            self.command('scope_review', {'scope': 'equipment', 'disposition': 'reviewed'})
        self.assertEqual(caught.exception.code, 'scope_incomplete')
        self.assertFalse(self.flow.view()['scopes']['equipment']['reviewed'])

    def test_count_only_scope_needs_no_unrelated_scale_or_length(self):
        stages = {s['id']: s for s in self.flow.view()['stages']}
        self.assertTrue(stages['measurements']['can_review'])
        self.command('review', {'stage': 'measurements'})
        self.command('project', {'name': 'Physical equipment', 'location': '', 'units': 'ft',
                                 'scopes': ['equipment', 'piping']})
        stages = {s['id']: s for s in self.flow.view()['stages']}
        self.assertFalse(stages['measurements']['can_review'])

    def test_http_save_requires_origin_and_current_version_and_serves_review_asset(self):
        before = copy.deepcopy(self.flow.data)
        payload = {'version': self.flow.data['version'], 'actor': 'Fixture estimator',
            'reason': 'Reviewed source regions', 'values': {'generation': None, 'request': self.draft()}}
        status, _ = self.http('api/workflow/equipment_count_save', payload, origin=False)
        self.assertEqual(status, 403)
        self.assertEqual(self.flow.data, before)
        status, raw = self.http('api/workflow/equipment_count_save', payload)
        self.assertEqual(status, 200, raw)
        self.assertEqual(json.loads(raw)['equipment_count_takeoff']['result']['total_each'], 4)
        accepted = copy.deepcopy(self.flow.data)
        status, _ = self.http('api/workflow/equipment_count_save', payload)
        self.assertEqual(status, 409)
        self.assertEqual(self.flow.data, accepted)
        status, raw = self.http('equipment_counts.js')
        self.assertEqual(status, 200)
        self.assertTrue(raw)

    def test_excel_rows_match_channels_and_reject_projection_drift(self):
        view = self.save(self.draft())
        book = fixtures.wf.takeoff_workbook
        sheets = book.workbook_sheets(view)
        rows = next(s['rows'] for s in sheets if s['name'] == 'Equipment')[5:]
        self.assertEqual([r[3]['number'] for r in rows], [1, 1, 1, 1])
        self.assertEqual([r[5]['number'] for r in rows], [1, 1, 1, 1])
        self.assertEqual([r[6]['number'] for r in rows], [1, 1, 1, 1])
        self.assertEqual([r[7]['number'] for r in rows], [0, 0, 0, 0])
        self.assertTrue(all(r[10]['location'].startswith("'Sources'!A") for r in rows))
        corrupted = copy.deepcopy(view)
        corrupted['equipment_count_takeoff']['result']['groups'][0]['known_subtotal_each'] += 1
        with self.assertRaises(ValueError):
            book.workbook_bytes(corrupted)

    def test_schedule_conflict_exports_declared_and_physical_counts_separately(self):
        request = self.draft()
        context = next(c for c in self.flow.view()['equipment_count_takeoff']['current_source_contexts']
                       if c['source']['index'] == 1)
        request['sources'].append({k: v for k, v in context.items() if k != 'source_key'})
        request['scope']['source_keys'].append(context['source_key'])
        request['evidence'].append({'id': 'coverage-schedule', 'source': context['source'],
            'bbox': [0, 0, 1, 1], 'kind': 'coverage', 'text': 'Reviewed all equipment schedule rows',
            'artifact_sha256': context['artifact_sha256']})
        request['coverage']['evidence_ids'].append('coverage-schedule')
        request['evidence'].append({'id': 'pump-schedule', 'source': context['source'],
            'bbox': [.1, .2, .9, .4], 'kind': 'schedule', 'text': 'Pump quantity 4',
            'artifact_sha256': context['artifact_sha256']})
        request['coverage']['evidence_ids'].append('pump-schedule')
        request['schedules'].append({'id': 'pump-demand', 'family': 'pump', 'work_status': 'new',
            'member_ids': ['pump-1', 'pump-2', 'pump-3'], 'declared_each': 4, 'evidence_ids': ['pump-schedule']})
        view = self.save(request)
        result = view['equipment_count_takeoff']['result']
        self.assertEqual(result['known_subtotal_each'], 4)
        self.assertIsNone(result['total_each'])
        declaration = result['declarations'][0]
        self.assertEqual(declaration['declared_each'], 4)
        self.assertEqual(declaration['observed_each'], 3)
        sheets = fixtures.wf.takeoff_workbook.workbook_sheets(view)
        row = next(s for s in sheets if s['name'] == 'Equipment declarations')['rows'][5]
        self.assertEqual(row[3]['number'], 4)
        self.assertEqual(row[4]['number'], 3)
        with zipfile.ZipFile(io.BytesIO(self.flow.export())) as archive:
            with zipfile.ZipFile(io.BytesIO(archive.read('takeoff.xlsx'))) as book:
                self.assertIn(b'Equipment declarations', book.read('xl/workbook.xml'))


if __name__ == '__main__':
    unittest.main()
