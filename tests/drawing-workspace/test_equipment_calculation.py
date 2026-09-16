"""Independent approved EC01-EC20 expectations and unsafe-input rejection."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location('test_' + name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = load('equipment_calculation')
CASES = json.loads((ROOT / 'tests/fixtures/equipment-takeoff/connected-equipment-cases.json').read_text())['cases']


def request(case='EC01'):
    value = copy.deepcopy(next(c['request'] for c in CASES if c['id'] == case))
    value['binding'] = core.rule_binding()
    return value


def result_value(result, target):
    parts = target.split('/')
    value = result
    for part in parts:
        if isinstance(value, list):
            if part=='length':
                value=len(value);continue
            key, selected = part.split('=', 1)
            value = next(v for v in value if str(v[key]) == selected)
        else:
            value = value[part]
    return value


class EquipmentCalculationTests(unittest.TestCase):
    def test_all_twenty_approved_examples(self):
        self.assertEqual([c['id'] for c in CASES], ['EC%02d' % n for n in range(1, 21)])
        for case in CASES:
            with self.subTest(case=case['id']):
                value = request(case['id']); before = copy.deepcopy(value)
                result = core.calculate(value)
                self.assertEqual(value, before)
                if case['id']=='EC02':
                    self.assertEqual([s['role'] for s in value['sources']],['plan','detail','schedule'])
                    self.assertEqual(len({core.fingerprint(e['source']) for e in value['evidence']}),3)
                    self.assertEqual(len(value['observations']),2,'Schedule evidence is not a third physical observation')
                for path, expected in case['expected'].items():
                    self.assertEqual(result_value(result, path), expected, path)
                for variant in case.get('variants', []):
                    changed = copy.deepcopy(variant['request']); changed['binding'] = core.rule_binding()
                    output = core.calculate(changed)
                    for path, expected in variant['expected'].items():
                        self.assertEqual(result_value(output, path), expected, path)

    def test_no_total_input_and_rule_binding_mismatch(self):
        for mutate in (lambda r: r.update(total_each=123), lambda r: r['binding'].update(rules_sha256='0'*64)):
            value=request(); mutate(value)
            with self.assertRaises(core.EquipmentCalculationError): core.calculate(value)

    def test_unknown_identity_is_not_zero_or_one(self):
        value=request(); value['observations'][0]['identity']='unresolved'
        result=core.calculate(value)
        self.assertIsNone(result['rows'][0]['physical_each'])
        self.assertEqual(result['known_subtotal_each'],1)
        self.assertIsNone(result['total_each'])

    def test_tag_text_and_schedule_are_not_physical_graphic_evidence(self):
        value=request()
        for evidence in value['evidence']: evidence['kind']='schedule'
        result=core.calculate(value)
        self.assertEqual(result['known_subtotal_each'],0)
        self.assertIsNone(result['total_each'])

    def test_invalid_reference_quantity_bool_and_parent_cycle_rejected(self):
        changes=[lambda r:r['observations'][0].update(evidence_ids=['missing']),
                 lambda r:r['observations'][0].update(parent_id=r['observations'][0]['id']),
                 lambda r:r['schedules'][0].update(declared_each=True),
                 lambda r:r['observations'][0]['bbox'].__setitem__(0,float('nan'))]
        for mutate in changes:
            value=request();mutate(value)
            with self.assertRaises(core.EquipmentCalculationError):core.calculate(value)

    def test_unknown_procurement_does_not_destroy_physical_count(self):
        value=request();value['observations'][0]['procurement']='unknown'
        output=core.calculate(value)
        self.assertEqual(output['total_each'],2)
        self.assertIsNone(output['rows'][0]['procurement_each'])

    def test_air_device_category_overlap_is_not_equipment(self):
        for family in ['air_device','diffuser','register','grille','linear_diffuser','mechanical_louver']:
            value=request();value['observations'][0]['family']=family
            output=core.calculate(value)
            self.assertIsNone(output['rows'][0]['physical_each'])
            self.assertIn('category_overlap',output['rows'][0]['issues'])

    def test_unsupported_multiplicity_and_overlapping_relationships_rejected(self):
        value=request('EC15');value['relations'][0]['evidence_ids']=[]
        self.assertIsNone(core.calculate(value)['total_each'])
        value=request('EC15');value['relations'].append(dict(value['relations'][0],id='again'))
        with self.assertRaises(core.EquipmentCalculationError):core.calculate(value)

    def test_only_changed_group_dependency_moves(self):
        value=request('EC20');before=core.calculate(value)
        value['observations'][0]['disposition']='exclude'
        after=core.calculate(value)
        old=next(r for r in before['rows'] if r['family']=='ahu')
        new=next(r for r in after['rows'] if r['family']=='ahu')
        self.assertEqual(old,new)

    def test_unselected_unused_context_change_does_not_invalidate_scope(self):
        value=request();context=copy.deepcopy(value['sources'][0]);context['source']['index']=1
        value['sources'].append(context)
        self.assertEqual(core.calculate(value,unavailable_source_keys=[core.fingerprint(context['source'])])['total_each'],2)

    def test_included_component_requires_relationship_evidence(self):
        value=request('EC08');value['observations'][1]['evidence_ids']=['graphic']
        output=core.calculate(value)
        self.assertEqual(output['total_each'],1)
        self.assertIsNone(next(r for r in output['rows'] if r['row_id']=='s')['procurement_each'])

    def test_relocation_requires_two_distinct_source_locations(self):
        value=request('EC16')
        for o in value['observations']:o['bbox']=[.1,.1,.2,.2]
        self.assertIsNone(core.calculate(value)['total_each'])

    def test_mutually_exclusive_phase_values_do_not_sum(self):
        value=request();value['observations'][0]['attributes']['alternate']='A';value['observations'][1]['attributes']['alternate']='B'
        output=core.calculate(value)
        self.assertIsNone(output['total_each']);self.assertIsNone(output['known_subtotal_each'])
        self.assertEqual([g['total_each'] for g in output['groups']],[1,1])

    def test_unknown_required_attribute_keeps_known_physical_subtotal(self):
        value=request();value['scope']['required_attributes']=['capacity']
        output=core.calculate(value)
        self.assertEqual(output['known_subtotal_each'],2)
        self.assertIsNone(output['total_each'])

    def test_unit_counts_do_not_have_scale_input(self):
        value=request();value['scale']=48
        with self.assertRaises(core.EquipmentCalculationError):core.calculate(value)

    def test_excluded_false_positive_does_not_block_final_or_add_quantity(self):
        value=request();value['observations'][0].update(disposition='exclude',issues=['false_positive'])
        value['schedules']=[]
        output=core.calculate(value)
        self.assertEqual(output['total_each'],1)

    def test_system_unknown_member_does_not_become_zero(self):
        value=request('EC18');value['observations'][0]['identity']='unresolved'
        self.assertIsNone(core.calculate(value)['systems'][0]['physical_each'])

    def test_overlapping_procurement_packages_do_not_duplicate_purchase(self):
        value=request();relation=dict(id='pkg1',kind='package',member_ids=['p1'],each=1,evidence_ids=['relation'],scope_text='Explicit purchase package')
        value['relations']=[relation,dict(relation,id='pkg2')]
        with self.assertRaises(core.EquipmentCalculationError):core.calculate(value)
        value=request('EC02');relation['member_ids']=['a1']
        value['relations'] += [relation,dict(relation,id='pkg2',member_ids=['a2'])]
        with self.assertRaises(core.EquipmentCalculationError):core.calculate(value)

    def test_package_uncertainty_preserves_physical_counts(self):
        value=request();value['relations']=[dict(id='package',kind='package',member_ids=['p1','p2'],each=None,evidence_ids=[],scope_text='Unknown package')]
        output=core.calculate(value)
        self.assertEqual(output['known_subtotal_each'],2);self.assertEqual(output['total_each'],2)
        self.assertTrue(all(r['procurement_each'] is None for r in output['rows']))
        self.assertIsNone(output['procurement_packages'])

    def test_excluded_package_members_do_not_create_purchase(self):
        value=request();value['schedules']=[]
        value['observations'][0]['disposition']='exclude'
        value['relations']=[dict(id='package',kind='package',member_ids=['p1'],each=1,evidence_ids=['relation'],scope_text='Explicit package')]
        output=core.calculate(value)
        self.assertEqual(output['procurement_packages'],0);self.assertEqual(output['total_each'],1)


if __name__=='__main__':unittest.main()
