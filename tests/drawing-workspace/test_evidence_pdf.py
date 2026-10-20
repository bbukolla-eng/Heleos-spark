"""Literal evidence-PDF rows from accepted duct, air-device and equipment producers.

Expected strings are frozen in tests/fixtures/evidence-pdf/specimen-rows.json.
This module does not calculate those quantities.
"""
import importlib.util
import io
import json
from pathlib import Path
import re
import unittest
import zipfile

import test_air_device_workflow as air_fixtures
import test_equipment_count_workflow as equipment_fixtures
import test_takeoff_workbook as duct_fixtures


ROOT = Path(__file__).resolve().parents[2]
SPECIMEN = json.loads((ROOT / 'tests/fixtures/evidence-pdf/specimen-rows.json').read_text())


def load_pdf():
    path = ROOT / 'scripts' / 'evidence_pdf.py'
    spec = importlib.util.spec_from_file_location('evidence_pdf_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _unescape(raw):
    text = raw.decode('latin1')
    return text.replace('\\n', '\n').replace('\\r', '\r').replace('\\t', '\t').replace('\\(', '(').replace('\\)', ')').replace('\\\\', '\\')


def parse_pdf(data):
    """Read page text, link destinations and named GoTo targets from an uncompressed PDF."""
    objects = {}
    for match in re.finditer(rb'(\d+) 0 obj\n(.*?)\nendobj\n', data, re.S):
        objects[int(match.group(1))] = match.group(2)
    catalog = objects[1]
    kids = [int(n) for n in re.findall(rb'(\d+) 0 R', objects[2])]
    pages = []
    page_index = {}
    for number in kids:
        body = objects[number]
        contents = int(re.search(rb'/Contents (\d+) 0 R', body).group(1))
        stream = objects[contents].split(b'stream\n', 1)[1].rsplit(b'\nendstream', 1)[0]
        pieces = [_unescape(item) for item in re.findall(rb'\(((?:\\.|[^\\)])*)\) Tj', stream)]
        page_index[number] = len(pages)
        pages.append('\n'.join(pieces))
    links = set(re.findall(rb'/D \(([^)]*)\)', data))
    links = {item.decode('ascii') for item in links}
    dests = {}
    for name, page in re.findall(rb'\(([^)]*)\) \[ (\d+) 0 R /XYZ', catalog):
        dests[name.decode('ascii')] = page_index[int(page)]
    return pages, links, dests


def dest_block(page, dest):
    marker = 'DEST ' + dest
    start = page.find(marker)
    if start < 0:
        raise AssertionError('Missing destination ' + dest)
    rest = page[start + len(marker):]
    nxt = rest.find('\nDEST ')
    return marker + (rest if nxt < 0 else rest[:nxt])


def page_named(pages, name):
    matches = [text for text in pages if 'PAGE ' + name + '\n' in text or text.startswith('PAGE ' + name)]
    if len(matches) != 1:
        raise AssertionError('Expected one ' + name + ' page, found ' + str(len(matches)))
    return matches[0]


class EvidencePdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pdf = load_pdf()

    def assert_case(self, data, case_name, view=None):
        spec = SPECIMEN['cases'][case_name]
        pages, links, dests = parse_pdf(data)
        text = '\n'.join(pages)
        for line in SPECIMEN['limits'] + spec['contains']:
            self.assertIn(line, text, line)
        summary = page_named(pages, 'summary')
        for line in spec.get('summary_excludes', []):
            self.assertNotIn(line, summary, line)
        for line in spec.get('history_contains', []):
            self.assertIn(line, page_named(pages, 'history'), line)
        for dest, parts in spec.get('links', {}).items():
            self.assertIn(dest, links, dest)
            block = dest_block(pages[dests[dest]], dest)
            for part in parts:
                self.assertIn(part, block, dest + ' ' + part)
        if spec.get('link_from_view') == 'air-device':
            observations = view['air_device_takeoff']['observations']
            self.assertEqual(len(observations), 1)
            dest = 'SRC-air-device-' + observations[0]['observation']['id']
            self.assertIn(dest, text)
            self.assertIn(dest, links)
            block = dest_block(pages[dests[dest]], dest)
            for part in (
                'page=1',
                'region=0.100000,0.200000,0.200000,0.300000',
                'revision=' + SPECIMEN['revision'],
            ):
                self.assertIn(part, block)
        return pages

    def test_duct_current_stale_and_mixed_match_frozen_rows(self):
        current = duct_fixtures.duct_view()
        first = self.pdf.evidence_bytes(current)
        self.assertEqual(first, self.pdf.evidence_bytes(current))
        self.assertTrue(first.startswith(b'%PDF-1.4\n'))
        self.assert_case(first, 'duct-current')
        self.assert_case(self.pdf.evidence_bytes(duct_fixtures.duct_view(stale=True)), 'duct-stale')
        self.assert_case(self.pdf.evidence_bytes(duct_fixtures.duct_view(mixed=True)), 'duct-mixed')

    def test_drift_and_empty_scope_stay_explicit(self):
        empty = {'project': {'name': 'New project'}}
        data = self.pdf.evidence_bytes(empty)
        self.assertEqual(data, self.pdf.evidence_bytes(empty))
        self.assert_case(data, 'empty')
        changed = duct_fixtures.duct_view()
        changed['duct_takeoff']['segments'][0]['feet'] = '9.00'
        with self.assertRaises(ValueError):
            self.pdf.evidence_bytes(changed)
        changed = duct_fixtures.duct_view()
        changed['duct_takeoff']['known_subtotal_ft'] = '1.00'
        with self.assertRaises(ValueError):
            self.pdf.evidence_bytes(changed)

    def test_air_channels_source_and_exclusion_history(self):
        case = air_fixtures.AirDeviceWorkflowTests('test_read_count_review_reopen_export_and_duplicate_replay')
        case.setUp()
        try:
            view, unused = case.read()
            data = self.pdf.evidence_bytes(view)
            self.assertEqual(data, self.pdf.evidence_bytes(view))
            self.assert_case(data, 'air-incomplete', view)
            air = view['air_device_takeoff']
            observation = air['observations'][0]
            excluded = case.command('air_device_review', {
                'generation': air['generation'],
                'observation_id': observation['observation']['id'],
                'observation_sha256': observation['observation_sha256'],
                'action': 'exclude'})
            self.assert_case(self.pdf.evidence_bytes(excluded), 'air-excluded')
            with zipfile.ZipFile(io.BytesIO(case.flow.export())) as archive:
                self.assertIn('evidence.pdf', archive.namelist())
                self.assertIn('EVIDENCE.txt', archive.namelist())
                self.assertIn(b'not a bid', archive.read('EVIDENCE.txt').lower())
                self.assert_case(archive.read('evidence.pdf'), 'air-excluded')
        finally:
            case.doCleanups()

    def test_equipment_channels_correction_schedule_and_stale_source(self):
        case = equipment_fixtures.EquipmentCountWorkflowTests(
            'test_correct_reopen_replay_and_export_preserve_prior_counts')
        case.setUp()
        try:
            original = case.draft()
            case.save(original)
            counts = case.flow.view()['equipment_count_takeoff']
            corrected = json.loads(json.dumps(counts['request']))
            corrected['observations'][2]['disposition'] = 'exclude'
            view = case.save(corrected)
            data = self.pdf.evidence_bytes(view)
            self.assertEqual(data, self.pdf.evidence_bytes(view))
            self.assert_case(data, 'equipment-corrected')
            with zipfile.ZipFile(io.BytesIO(case.flow.export())) as archive:
                self.assertEqual(archive.read('evidence.pdf'), data)
            stale = equipment_fixtures.EquipmentCountWorkflowTests(
                'test_source_role_change_blocks_only_affected_physical_rows')
        finally:
            case.doCleanups()
        stale.setUp()
        try:
            stale.save(stale.draft())
            changed = stale.command('page', {
                'source': {'revision_id': equipment_fixtures.fixtures.REVISION, 'index': 0},
                'role': 'legend', 'label': 'Legend', 'building': 'A', 'level': '1',
                'note': 'Corrected role'})
            self.assert_case(self.pdf.evidence_bytes(changed), 'equipment-stale-source')
        finally:
            stale.doCleanups()
        schedule = equipment_fixtures.EquipmentCountWorkflowTests(
            'test_schedule_conflict_exports_declared_and_physical_counts_separately')
        schedule.setUp()
        try:
            request = schedule.draft()
            context = next(item for item in schedule.flow.view()['equipment_count_takeoff']['current_source_contexts']
                           if item['source']['index'] == 1)
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
                'member_ids': ['pump-1', 'pump-2', 'pump-3'], 'declared_each': 4,
                'evidence_ids': ['pump-schedule']})
            saved = schedule.save(request)
            self.assert_case(self.pdf.evidence_bytes(saved), 'equipment-schedule')
        finally:
            schedule.doCleanups()


if __name__ == '__main__':
    unittest.main()
