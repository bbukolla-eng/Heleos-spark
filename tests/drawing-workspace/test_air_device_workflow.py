"""Connected each-count flow with retained synthetic local-reader responses.

These checks exercise the real producer, kernel, persistence, workflow and ZIP
export. The model and PDF renderer are doubles; recognition/native acceptance
remains separate.
"""
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

import test_takeoff_workflow as fixtures
import test_air_device_source_producer as source_fixtures


class Workspace(fixtures.Workspace):
    def rendered_page(self, revision, index):
        snapshot, unused = self.verified_pdf(revision)
        snapshot.close()
        return source_fixtures.fixtures.png(index)


class AirDeviceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Workspace(Path(self.temp.name))
        self.workspace.renderer = [sys.executable]
        reader = fixtures.ProjectReader()
        self.workspace.equipment = fixtures.drawing.EquipmentEngine(self.workspace, text_reader=reader)
        self.addCleanup(self.workspace.equipment.close)
        self.workspace.documents = fixtures.drawing.DocumentPipeline(self.workspace, reader)
        self.addCleanup(self.workspace.documents.close)
        self.flow = fixtures.drawing.TakeoffWorkflow(self.workspace)
        self.workspace.workflow = self.flow
        self.addCleanup(lambda: self.workspace.workflow.close())
        self.adapter = source_fixtures.Adapter()
        value = source_fixtures.proposal()
        value['observations'] = value['observations'][:1]
        value['correspondences'] = []
        value['multiplicities'] = []
        value['schedule_declarations'] = []
        self.adapter.value = value
        self.flow.air_device_producer.adapter = self.adapter
        self.command('project', {'name': 'Air count flow', 'location': 'Original fixture',
            'units': 'ft', 'scopes': ['air_devices']})
        self.command('page', {'source': self.source(), 'role': 'plan', 'label': 'M1',
            'building': 'A', 'level': '1', 'note': 'Original source fixture'})

    def source(self):
        return {'revision_id': fixtures.REVISION, 'index': 0}

    def command(self, action, values):
        return self.flow.command(action, {'version': self.flow.data['version'],
            'actor': 'Fixture reviewer', 'reason': 'Review original source fixture', 'values': values})

    def read(self):
        self.command('air_device_find', {'source': self.source()})
        self.flow.air_device_producer.thread.join(5)
        self.assertFalse(self.flow.air_device_producer.thread.is_alive())
        job = self.flow.air_device_producer.view()['jobs'][-1]
        self.assertEqual(job['state'], 'completed', job)
        return self.command('air_device_prepare', {'reading_id': job['id']}), job['id']

    def test_read_count_review_reopen_export_and_duplicate_replay(self):
        view, reading_id = self.read()
        air = view['air_device_takeoff']
        self.assertEqual(air['result']['known_subtotal_each'], 1)
        self.assertIsNone(air['result']['total_each'])
        view = self.command('air_device_coverage', {'generation': air['generation'], 'state': 'complete'})
        self.assertEqual(view['air_device_takeoff']['result']['total_each'], 1)
        view = self.command('scope_review', {'scope': 'air_devices', 'disposition': 'reviewed'})
        self.assertTrue(view['scopes']['air_devices']['reviewed'])
        view = self.command('air_device_prepare', {'reading_id': reading_id})
        self.assertEqual(view['air_device_takeoff']['result']['total_each'], 1)
        self.flow.close()
        self.flow = fixtures.drawing.TakeoffWorkflow(self.workspace)
        self.workspace.workflow = self.flow
        self.assertEqual(self.flow.view()['air_device_takeoff']['result']['total_each'], 1)
        self.assertEqual(len(self.adapter.calls), 1)
        archive = zipfile.ZipFile(io.BytesIO(self.flow.export()))
        names = archive.namelist()
        self.assertTrue(any('air-device' in name and name.endswith('.csv') for name in names), names)
        self.assertTrue(any('air-device' in name and name.endswith('.json') for name in names), names)
        raw = b'\n'.join(archive.read(name) for name in names if 'air-device' in name)
        self.assertIn(fixtures.REVISION.encode(), raw)

    def test_correction_recalculates_and_preserves_original_response(self):
        view, reading_id = self.read()
        air = view['air_device_takeoff']
        observation = air['observations'][0]['observation']
        original = self.flow.air_device_producer.result(reading_id)
        pin = source_fixtures.producer_module.digest(observation)
        view = self.command('air_device_review', {'generation': air['generation'],
            'observation_id': observation['id'], 'observation_sha256': pin, 'action': 'exclude'})
        self.assertEqual(view['air_device_takeoff']['result']['known_subtotal_each'], 0)
        self.assertEqual(self.flow.air_device_producer.result(reading_id), original)
        self.assertTrue(view['air_device_takeoff']['history'])
        with self.assertRaises(Exception):
            self.command('air_device_review', {'generation': air['generation'],
                'observation_id': observation['id'], 'observation_sha256': pin, 'action': 'include'})

    def test_generic_manual_count_cannot_complete_deterministic_air_device_scope(self):
        self.command('item', {'id': None, 'scope': 'air_devices', 'description': 'Manual candidate',
            'system': '', 'size': '', 'material': '', 'quantity': '12', 'unit': 'each',
            'evidence_class': 'drawn', 'source': self.source(), 'measurement_id': None, 'note': 'Draft only'})
        with self.assertRaises(fixtures.wf.WorkflowError) as caught:
            self.command('scope_review', {'scope': 'air_devices', 'disposition': 'reviewed'})
        self.assertEqual(caught.exception.code, 'scope_incomplete')
        self.assertFalse(self.flow.view()['scopes']['air_devices']['reviewed'])

    def test_saved_air_device_readings_cannot_be_hidden_by_removing_scope(self):
        self.read()
        before = copy.deepcopy(self.flow.data)
        with self.assertRaises(fixtures.wf.WorkflowError) as caught:
            self.command('project', {'name': 'Air count flow', 'location': 'Original fixture',
                'units': 'ft', 'scopes': ['ductwork']})
        self.assertEqual(caught.exception.code, 'scope_has_records')
        self.assertEqual(self.flow.data, before)

    def test_static_air_device_asset_and_package_policy_closure(self):
        self.assertIn('air_devices.js', fixtures.drawing.ASSET_TYPES)
        root = Path(__file__).resolve().parents[2]
        index = (root / 'apps/drawing-workspace/index.html').read_text()
        self.assertLess(index.index('air_devices.js'), index.index('workflow.js'))
        package = source_fixtures.fixtures.fixtures.load('workspace_package')
        for name in ('scripts/air_device_calculation.py', 'scripts/project_air_device_takeoff.py',
                     'apps/drawing-workspace/air_devices.js',
                     'tests/fixtures/air-device-takeoff/approved-rule-packet.md'):
            self.assertIn(name, package.PAYLOAD_PATHS)


if __name__ == '__main__':
    unittest.main()
