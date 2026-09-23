"""Connected air-device saved state with fake transport and original local bytes."""
import copy
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import test_air_device_source_producer as source_fixture

ROOT = source_fixture.ROOT
PATH = ROOT / 'scripts/project_air_device_takeoff.py'
project = source_fixture.fixtures.fixtures.load('project_air_device_takeoff') if PATH.exists() else None


class ProjectAirDeviceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(project, 'Project air-device adapter is not implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = source_fixture.Workspace(Path(self.temp.name))
        self.inputs = source_fixture.fixtures.engine_module.InferenceEngine(self.workspace,
            source_fixture.fixtures.fixtures.baseline.BaselineStore(self.workspace.documents.knowledge_store))
        self.addCleanup(self.inputs.close)
        self.adapter = source_fixture.Adapter()
        self.producer = source_fixture.producer_module.AirDeviceSourceProducer(self.workspace, self.inputs, self.adapter)
        self.workspace.air_device_producer = self.producer
        self.addCleanup(self.producer.close)
        self.data = {'project': {'scopes': ['air_devices']}, 'pages': {}}
        for index in range(3):
            self.data['pages'][source_fixture.fixtures.REVISION + ':' + str(index)] = {'role': 'plan'}
        self.workspace.workflow.data = self.data
        project.initialize(self.data)

    def sheets(self):
        return {d['revision_id'] + ':' + str(s['index']): s
            for d in self.workspace.state()['documents'] for s in d['sheets']}

    def simple(self, count=1):
        value = source_fixture.proposal()
        value.update(correspondences=[], multiplicities=[], relocations=[], schedule_declarations=[])
        value['observations'] = value['observations'][:count]
        return value

    def read(self, index=0, value=None):
        self.adapter.value = self.simple() if value is None else value
        job = self.producer.start({'revision_id': source_fixture.fixtures.REVISION, 'index': index}, 'Estimator', 'Read original fixture')
        self.producer.thread.join(3)
        self.assertFalse(self.producer.thread.is_alive())
        return self.producer.result(job['id'])

    def act(self, command, **values):
        if command != 'air_device_prepare':
            values.setdefault('generation', self.data['air_device_current'])
        return project.apply(self.workspace, self.data, self.sheets(), command, values, 'Estimator', 'Checked source drawing')

    def view(self):
        return project.view(self.workspace, self.data, self.sheets())

    def prepare(self, index=0, value=None):
        reading = self.read(index, value)
        self.act('air_device_prepare', reading_id=reading['id'])
        return reading

    def test_empty_detector_requires_explicit_current_review_for_zero(self):
        value = self.simple(0)
        value['evidence'] = []
        self.prepare(value=value)
        self.assertIsNone(self.view()['result']['total_each'])
        self.act('air_device_coverage', state='complete')
        view = self.view()
        self.assertEqual(view['result']['total_each'], 0)
        self.assertTrue(view['result']['complete'])
        self.assertTrue(view['coverage']['evidence_ids'])
        self.workspace.changed_geometry = True
        view = self.view()
        self.assertTrue(view['stale'])
        self.assertIsNone(view['result']['total_each'])

    def test_prepare_admits_supported_observation_replay_is_idempotent(self):
        reading = self.prepare()
        self.assertEqual(self.view()['result']['known_subtotal_each'], 1)
        before = copy.deepcopy(self.data)
        self.act('air_device_prepare', reading_id=reading['id'])
        self.assertEqual(self.data, before)
        self.act('air_device_coverage', state='complete')
        self.assertEqual(self.view()['result']['total_each'], 1)

    def test_duplicate_job_stages_without_inflating_and_discard_is_durable(self):
        self.prepare()
        self.act('air_device_coverage', state='complete')
        initial = self.data['air_device_current']
        second = self.read()
        self.act('air_device_prepare', reading_id=second['id'])
        view = self.view()
        self.assertEqual(self.data['air_device_current'], initial)
        self.assertEqual(view['result']['known_subtotal_each'], 1)
        self.assertIsNone(view['result']['total_each'])
        pending = view['proposals']['readings'][0]
        with self.assertRaises(project.ProjectAirDeviceError):
            self.act('air_device_intake', reading_id=second['id'], reading_sha256=pending['reading_sha256'], action='add')
        self.act('air_device_intake', reading_id=second['id'], reading_sha256=pending['reading_sha256'], action='discard')
        self.assertEqual(self.view()['result']['total_each'], 1)
        before = copy.deepcopy(self.data)
        self.act('air_device_prepare', reading_id=second['id'])
        self.assertEqual(self.data, before)

    def test_correction_preserves_history_and_unrelated_dependencies(self):
        self.prepare(value=self.simple(2))
        view = self.view()
        old = copy.deepcopy(view['result'])
        target = view['observations'][0]
        other_id = view['observations'][1]['observation']['id']
        self.act('air_device_review', observation_id=target['observation']['id'],
            observation_sha256=target['observation_sha256'], action='exclude')
        view = self.view()
        self.assertEqual(view['result']['known_subtotal_each'], 1)
        old_other = next(r for r in old['rows'] if other_id in r['member_ids'])
        new_other = next(r for r in view['result']['rows'] if other_id in r['member_ids'])
        self.assertEqual(old_other, new_other)
        self.assertEqual(view['history'][0]['result']['known_subtotal_each'], 2)
        frozen = copy.deepcopy(self.data)
        with mock.patch.object(self.workspace, 'rendered_page', side_effect=AssertionError('reopen must not render')):
            reopened = project.view(self.workspace, json.loads(json.dumps(self.data)), self.sheets())
        self.assertEqual(reopened['result'], view['result'])
        self.assertEqual(self.data, frozen)

    def test_pending_same_proposal_is_not_an_admission(self):
        value = self.simple(2)
        value['correspondences'] = source_fixture.proposal()['correspondences']
        self.prepare(value=value)
        view = self.view()
        self.assertEqual(view['result']['known_subtotal_each'], 0)
        proposal = view['proposals']['correspondences'][0]
        self.act('air_device_proposal', proposal_id=proposal['id'], proposal_sha256=proposal['sha256'],
            action='accept', member_ids=[])
        self.assertEqual(self.view()['result']['known_subtotal_each'], 1)

    def test_source_and_scale_changes_have_separate_effects(self):
        self.prepare()
        self.act('air_device_coverage', state='complete')
        before = self.view()
        self.data['scale_facts'] = [{'id': 'not-a-count-dependency', 'state': 'withdrawn'}]
        self.assertEqual(self.view()['fingerprint'], before['fingerprint'])
        self.workspace.changed_geometry = True
        stale = self.view()
        self.assertTrue(stale['stale'])
        self.assertEqual(stale['result']['known_subtotal_each'], 0)
        self.assertIsNone(stale['result']['total_each'])
        self.assertEqual(stale['history'][-1]['result']['total_each'], 1)

    def test_exact_generation_and_observation_pins_reject_atomically(self):
        self.prepare()
        before = copy.deepcopy(self.data)
        target = self.view()['observations'][0]
        for patch in ({'generation': 'stale'}, {'observation_sha256': '0' * 64}):
            values = {'generation': self.data['air_device_current'], 'observation_id': target['observation']['id'],
                'observation_sha256': target['observation_sha256'], 'action': 'exclude'}
            values.update(patch)
            with self.assertRaises(project.ProjectAirDeviceError):
                self.act('air_device_review', **values)
            self.assertEqual(self.data, before)

    def test_export_retains_raw_png_source_and_stale_history(self):
        reading = self.prepare()
        self.act('air_device_coverage', state='complete')
        self.workspace.changed_geometry = True
        with mock.patch.object(self.workspace, 'rendered_page', side_effect=AssertionError('export must not render')):
            files = project.export_files(self.workspace, self.data, self.sheets())
        self.assertIn('air-device-counts.csv', files)
        self.assertIn('air-device-history.json', files)
        self.assertIn(self.adapter.raw, files.values())
        self.assertIn(source_fixture.fixtures.png(), files.values())
        current = json.loads(files['air-device-counts.json'])
        self.assertTrue(current['stale'])
        self.assertIsNone(current['result']['total_each'])
        self.assertEqual(current['history'][-1]['result']['total_each'], 1)


    def accept_intake(self, reading, action='add'):
        self.act('air_device_intake', reading_id=reading['id'],
            reading_sha256=source_fixture.producer_module.digest(reading), action=action)

    def member(self, wrapper):
        return {'observation_id': wrapper['observation']['id'], 'observation_sha256': wrapper['observation_sha256']}

    def save_relation(self, kind, record, previous=None):
        self.act('air_device_relation', kind=kind, relation_id=record['id'],
            relation_sha256=project._digest(previous) if previous else None, action='save', record=record)

    def test_multiplicity_is_proposal_until_review_and_covers_representative(self):
        value = self.simple()
        value['multiplicities'] = source_fixture.proposal()['multiplicities']
        self.prepare(value=value)
        self.assertEqual(self.view()['result']['known_subtotal_each'], 0)
        p = self.view()['proposals']['multiplicities'][0]
        self.act('air_device_proposal', proposal_id=p['id'], proposal_sha256=p['sha256'], action='accept', member_ids=[])
        self.assertEqual(self.view()['result']['known_subtotal_each'], 4)
        self.assertEqual(len(self.view()['result']['rows']), 1)

    def test_relation_replacement_does_not_resurrect_removed_member(self):
        self.prepare(value=self.simple(2))
        reading = self.prepare(index=1)
        self.accept_intake(reading)
        view = self.view()
        a, b, c = view['observations']
        old = {'id': 'reviewed-same', 'members': [self.member(a), self.member(b)],
            'state': 'same', 'evidence_ids': a['observation']['evidence_ids']}
        self.save_relation('correspondence', old)
        self.assertEqual(self.view()['result']['known_subtotal_each'], 2)
        new = dict(old, members=[self.member(a), self.member(c)])
        self.save_relation('correspondence', new, old)
        view = self.view()
        self.assertEqual(view['result']['known_subtotal_each'], 1)
        abandoned = next(r for r in view['result']['rows'] if b['observation']['id'] in r['member_ids'])
        self.assertIsNone(abandoned['physical_each'])
        self.assertEqual(view['relationships']['correspondences'][0]['sha256'], project._digest(new))

    def test_schedule_conflict_retains_physical_subtotal_and_context_reread_retires_link(self):
        self.prepare()
        self.data['pages'][source_fixture.fixtures.REVISION + ':1']['role'] = 'schedule'
        value = self.simple(0)
        value['schedule_declarations'] = source_fixture.proposal()['schedule_declarations']
        reading = self.prepare(index=1, value=value)
        self.accept_intake(reading)
        view = self.view()
        p = view['proposals']['schedules'][0]
        self.assertEqual(view['result']['known_subtotal_each'], 1)
        self.act('air_device_proposal', proposal_id=p['id'], proposal_sha256=p['sha256'], action='accept',
            member_ids=[view['observations'][0]['observation']['id']])
        self.act('air_device_coverage', state='complete')
        self.assertEqual(self.view()['result']['known_subtotal_each'], 1)
        self.assertIsNone(self.view()['result']['total_each'])
        value['schedule_declarations'][0]['declared_each'] = 1
        replacement = self.prepare(index=1, value=value)
        self.accept_intake(replacement, 'replace')
        view = self.view()
        self.assertEqual(view['relationships']['schedules'], [])
        self.assertEqual(len(view['proposals']['schedules']), 1)
        p = view['proposals']['schedules'][0]
        self.act('air_device_proposal', proposal_id=p['id'], proposal_sha256=p['sha256'], action='accept',
            member_ids=[view['observations'][0]['observation']['id']])
        self.act('air_device_coverage', state='complete')
        self.assertEqual(self.view()['result']['total_each'], 1)

    def test_relocation_preserves_one_physical_and_separate_operations(self):
        value = self.simple(2)
        value['observations'][1]['bbox'] = [0.6, 0.6, 0.7, 0.7]
        value['evidence'].append({'id': 'new-location', 'bbox': [0.6, 0.6, 0.7, 0.7], 'text': None})
        value['observations'][1]['evidence_ids'].append('new-location')
        value['observations'][0]['attributes']['work_status']['value'] = 'demolition'
        value['relocations'] = [{'id': 'move', 'members': ['device-a', 'device-b'],
            'remove_members': ['device-a'], 'reinstall_members': ['device-b'],
            'remove_operation_id': 'remove-1', 'reinstall_operation_id': 'install-1',
            'reused': True, 'evidence_ids': ['note']}]
        self.prepare(value=value)
        view = self.view()
        p = view['proposals']['relocations'][0]
        self.act('air_device_proposal', proposal_id=p['id'], proposal_sha256=p['sha256'], action='accept', member_ids=[])
        scope = self.view()['scope']
        self.act('air_device_scope', **{k: scope[k] for k in ('source_keys', 'group_by', 'required_fields')}, work_statuses=['relocated'])
        result = self.view()['result']
        self.assertEqual(result['physical_known_each'], 1)
        self.assertEqual(result['known_subtotal_each'], 1)
        self.assertEqual(result['rows'][0]['operations'], {'remove': 1, 'reinstall': 1})
        self.assertEqual(result['rows'][0]['new_purchase_each'], 0)

    def test_same_page_replace_keeps_original_history_without_accumulation(self):
        original = self.prepare(value=self.simple(2))
        replacement = self.prepare(value=self.simple())
        self.accept_intake(replacement, 'replace')
        view = self.view()
        self.assertEqual(view['result']['known_subtotal_each'], 1)
        self.assertEqual(len(view['observations']), 1)
        self.assertEqual(view['history'][0]['result']['known_subtotal_each'], 2)
        files = project.export_files(self.workspace, self.data, self.sheets())
        for record in (original, replacement):
            self.assertIn('air-device-sources/' + record['id'] + '/producer.json', files)

    def test_role_reassignment_stales_only_affected_source(self):
        self.prepare()
        reading = self.prepare(index=1)
        self.accept_intake(reading)
        before = self.view()
        other_id = before['observations'][1]['observation']['id']
        other_row = next(r for r in before['result']['rows'] if other_id in r['member_ids'])
        self.data['pages'][source_fixture.fixtures.REVISION + ':0']['role'] = 'legend'
        after = self.view()
        self.assertEqual(after['result']['known_subtotal_each'], 1)
        self.assertEqual(other_row, next(r for r in after['result']['rows'] if other_id in r['member_ids']))
        self.assertTrue(after['stale'])
        self.assertFalse(after['sources'][0]['current'])

    def test_expected_coded_original_source_failure_is_visible_stale(self):
        self.prepare()
        class SourceUnavailable(Exception):
            code, message = 'original_missing', 'Original is unavailable'
        with mock.patch.object(self.producer, 'result', side_effect=SourceUnavailable()):
            view = self.view()
        self.assertTrue(view['stale'])
        self.assertEqual(view['result']['known_subtotal_each'], 0)
        self.assertTrue(any(i['code'] == 'original_missing' for i in view['issues']))

    def test_explicit_source_issue_resolution_preserves_original(self):
        value = self.simple()
        value['observations'][0]['issues'] = ['Unreadable work status needs review']
        self.prepare(value=value)
        self.act('air_device_coverage', state='complete')
        self.assertIsNone(self.view()['result']['total_each'])
        self.assertEqual(self.view()['result']['known_subtotal_each'], 1)
        self.assertTrue(any('Reader issue' in r for r in self.data['air_device_generations'][-1]['request']['coverage']['unresolved_requirements']))
        target = self.view()['observations'][0]
        attrs = copy.deepcopy(target['observation']['attributes'])
        self.act('air_device_correct', observation_id=target['observation']['id'],
            observation_sha256=target['observation_sha256'], attributes=attrs, depiction='physical', issues=[])
        self.assertEqual(self.view()['observations'][0]['observation']['issues'], [])
        self.assertEqual(self.view()['history'][0]['draft']['observations'][0]['issues'], value['observations'][0]['issues'])
        self.assertEqual(self.view()['result']['known_subtotal_each'], 1)
        self.act('air_device_coverage', state='complete')
        self.assertEqual(self.view()['result']['total_each'], 1)

    def test_initial_request_has_no_artificial_pending_intake(self):
        self.prepare()
        saved = self.data['air_device_generations'][-1]
        self.assertFalse(any('later air-device source' in x for x in saved['request']['coverage']['unresolved_requirements']))

    def test_loaded_adapter_drift_blocks_new_generation_atomically(self):
        self.prepare()
        before = copy.deepcopy(self.data)
        with mock.patch.object(project, 'SOURCE_SHA256', '0' * 64):
            with self.assertRaises(project.ProjectAirDeviceError) as caught:
                self.act('air_device_recalculate')
        self.assertEqual(caught.exception.code, 'air_device_implementation_changed')
        self.assertEqual(self.data, before)

    def test_rehashed_saved_result_cannot_pass_reopen(self):
        self.prepare()
        generation = self.data['air_device_generations'][0]
        generation['result']['known_subtotal_each'] = 999
        generation.pop('sha256')
        project._seal(generation)
        with self.assertRaises(project.ProjectAirDeviceError):
            self.view()

    def test_failed_producer_is_not_admitted(self):
        self.adapter.failure = True
        job = self.producer.start({'revision_id': source_fixture.fixtures.REVISION, 'index': 0}, 'Estimator', 'Failure fixture')
        self.producer.thread.join(3)
        before = copy.deepcopy(self.data)
        with self.assertRaises(project.ProjectAirDeviceError):
            self.act('air_device_prepare', reading_id=job['id'])
        self.assertEqual(self.data, before)

    def test_schedule_first_is_retained_context_without_quantity_or_prepare_loop(self):
        self.data['pages'][source_fixture.fixtures.REVISION + ':1']['role'] = 'schedule'
        reading = self.prepare(index=1, value=self.simple(0))
        view = self.view()
        self.assertFalse(view['available'])
        self.assertIsNone(view['result'])
        self.assertIn(reading['id'], view['prepared_reading_ids'])
        before = copy.deepcopy(self.data)
        self.act('air_device_prepare', reading_id=reading['id'])
        self.assertEqual(self.data, before)
        self.prepare()
        self.accept_intake(reading)
        self.assertEqual(self.view()['result']['known_subtotal_each'], 1)

    def test_exact_command_replay_is_noop_after_new_generation(self):
        self.prepare()
        view = self.view()
        target = view['observations'][0]
        values = {'generation': view['generation'], 'observation_id': target['observation']['id'],
            'observation_sha256': target['observation_sha256'], 'action': 'exclude'}
        self.act('air_device_review', **values)
        before = copy.deepcopy(self.data)
        self.act('air_device_review', **values)
        self.assertEqual(self.data, before)

    def test_unconsumed_context_loss_does_not_stale_selected_complete_count(self):
        self.prepare()
        self.data['pages'][source_fixture.fixtures.REVISION + ':1']['role'] = 'legend'
        reading = self.prepare(index=1, value=self.simple(0))
        self.accept_intake(reading)
        self.act('air_device_coverage', state='complete')
        self.assertEqual(self.view()['result']['total_each'], 1)
        self.data['pages'][source_fixture.fixtures.REVISION + ':1']['role'] = 'schedule'
        view = self.view()
        self.assertFalse(view['stale'])
        self.assertEqual(view['result']['total_each'], 1)
        self.assertFalse(view['sources'][1]['current'])

    def test_proposed_raw_member_pin_is_rebound_to_canonical_draft_only(self):
        value = self.simple()
        value['observations'][0]['bbox'] = [0, 0, 1, 1]
        value['evidence'][0]['bbox'] = [0, 0, 1, 1]
        value['multiplicities'] = source_fixture.proposal()['multiplicities']
        reading = self.prepare(value=value)
        view = self.view()
        p = view['proposals']['multiplicities'][0]
        original = reading['proposed_multiplicities'][0]
        self.assertNotEqual(original['members'][0]['observation_sha256'], p['record']['members'][0]['observation_sha256'])
        self.assertEqual(p['record']['members'][0]['observation_sha256'], view['observations'][0]['observation_sha256'])
        self.assertEqual(p['source_proposal_sha256'], project._digest(original))
        self.act('air_device_proposal', proposal_id=p['id'], proposal_sha256=p['sha256'], action='accept', member_ids=[])
        self.assertEqual(self.view()['result']['known_subtotal_each'], 4)
        self.assertEqual(self.producer.result(reading['id'])['proposed_multiplicities'][0], original)


if __name__ == '__main__':
    unittest.main()
