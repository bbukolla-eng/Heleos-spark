"""Projection reconciliation using deterministic core and connected application."""
import copy
from decimal import Decimal, ROUND_HALF_EVEN
import importlib.util
import io
import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
import zipfile

import test_air_device_workflow as air_fixtures
from test_xlsx_workbook import ROOT, NS


def load(name):
    spec=importlib.util.spec_from_file_location('workbook_test_'+name,ROOT/'scripts'/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


workbook=load('takeoff_workbook');core=load('duct_calculation')


def duct_view(stale=False, mixed=False):
    request=json.loads((ROOT/'tests/fixtures/workbook/duct-request.json').read_text())
    if mixed:
        request['observations'][1]['group']['work_status']='demolition'
        fingerprint=core.fingerprint(request['observations'][1])
        request['admissions'][1]['observation_fingerprint']=fingerprint
        request['coverage']['observation_fingerprints'][request['observations'][1]['id']]=fingerprint
    result=core.calculate(**request);dependencies=copy.deepcopy(result['dependencies'])
    if stale: dependencies['rows']['A-2']='e'*64
    current=core.result_view(result,dependencies)
    segments=[]
    for row,obs in zip(current['rows'],request['observations']):
        segments.append(dict(row,observation=copy.deepcopy(obs),source=copy.deepcopy(obs['source']),evidence=[]))
    old=copy.deepcopy(result);old.update(known_subtotal_ft='999.00',total_ft='999.00')
    return {'project':{'name':'Original workbook fixture'},'duct_takeoff':dict(current,available=True,
        current_generation_id='current',segments=segments,generations=[{'id':'current','result':result},
        {'id':'old','result':old}]),'air_device_takeoff':{'available':False}}


def value(cell):
    if isinstance(cell,dict): return cell.get('cached',cell.get('text',cell.get('number')))
    return cell


class ProjectionTests(unittest.TestCase):
    def test_current_core_totals_and_history_are_separate(self):
        view=duct_view();sheets=workbook.workbook_sheets(view);self.assertEqual(view,duct_view())
        summary=sheets[0]['rows'];scope=next(row for row in summary[5:] if row[0]=='Duct scope')
        self.assertEqual([value(scope[i]) for i in [3,4]],['12.00','12.00'])
        self.assertNotIn('History',scope[3]['formula'])
        self.assertEqual(len(sheets[3]['rows']),7)
        self.assertIn('999.00',json.dumps(sheets[3]));self.assertNotIn('999.00',json.dumps(sheets[0]))
        self.assertEqual([value(r[3]) for r in sheets[4]['rows'][5:]],[1,1])
        self.assertEqual(sheets[1]['rows'][5][7]['location'],"'Sources'!A6")

    def test_stale_preserves_history_but_only_current_contributes(self):
        sheets=workbook.workbook_sheets(duct_view(stale=True))
        scope=next(row for row in sheets[0]['rows'][5:] if row[0]=='Duct scope')
        self.assertEqual(value(scope[3]),'8.00');self.assertEqual(value(scope[4]),'UNKNOWN')
        stale=sheets[1]['rows'][6];self.assertEqual(stale[2],'stale');self.assertEqual(value(stale[3]),'UNKNOWN')
        self.assertEqual(stale[4],'1.219200');self.assertEqual(value(stale[6]),0)

    def test_mixed_work_status_keeps_group_totals_and_unknown_scope(self):
        rows=workbook.workbook_sheets(duct_view(mixed=True))[0]['rows'][5:]
        groups=[r for r in rows if r[0]=='Duct group'];self.assertEqual(sorted(value(r[4]) for r in groups),['4.00','8.00'])
        scope=next(r for r in rows if r[0]=='Duct scope');self.assertEqual(value(scope[3]),'UNKNOWN');self.assertEqual(value(scope[4]),'UNKNOWN')

    def test_rejects_quantity_drift_missing_generation_source_and_precision(self):
        changes=[lambda v:v['duct_takeoff']['segments'][0].update(feet='9.00'),
            lambda v:v['duct_takeoff'].update(current_generation_id='missing'),
            lambda v:v['duct_takeoff']['segments'][0]['observation']['source'].update(revision_id='bad'),
            lambda v:v['duct_takeoff']['segments'][0].update(meters='1000000.000000',stored_meters='1000000.000000'),
            lambda v:v['duct_takeoff']['groups'][0].update(known_subtotal_ft='1.00')]
        for change in changes:
            v=duct_view();change(v)
            with self.assertRaises(ValueError): workbook.workbook_bytes(v)

    def test_rounding_integer_bounds_and_ties(self):
        for n in [0,1,1523,1524,1525,4571,4572,4573,304800,999999999999]+[q*3048+1524 for q in range(1000)]:
            expected=format((Decimal(n)/Decimal(304800)).quantize(Decimal('.01'),rounding=ROUND_HALF_EVEN),'.2f')
            self.assertEqual(workbook._feet(n),expected)
        for n in (-1,1000000000000,True):
            with self.assertRaises(ValueError): workbook._feet(n)

    def test_no_calculation_is_unavailable_not_zero(self):
        rows=workbook.workbook_sheets({'project':{'name':'New project'}})[0]['rows'][5:]
        self.assertEqual([value(r[3]) for r in rows],['NOT AVAILABLE']*3)


class ConnectedWorkbookTests(air_fixtures.AirDeviceWorkflowTests):
    def test_xlsx_in_export_tracks_correction_and_reopening(self):
        view,unused=self.read();air=view['air_device_takeoff']
        view=self.command('air_device_coverage',{'generation':air['generation'],'state':'complete'})
        with zipfile.ZipFile(io.BytesIO(self.flow.export())) as outer:
            self.assertIn('takeoff.xlsx',outer.namelist());self.assertIn('WORKBOOK.txt',outer.namelist())
            with zipfile.ZipFile(io.BytesIO(outer.read('takeoff.xlsx'))) as archive:
                root=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
                values={c.get('r'):c.find('s:v',NS).text for c in root.findall('.//s:c',NS) if c.find('s:v',NS) is not None}
                self.assertEqual(values['D7'],'1');self.assertEqual(values['E7'],'1')
                source=archive.read('xl/worksheets/sheet5.xml')
                self.assertIn(air_fixtures.fixtures.REVISION.encode(),source)
        air=view['air_device_takeoff'];o=air['observations'][0]
        view=self.command('air_device_review',{'generation':air['generation'],'observation_id':o['observation']['id'],
             'observation_sha256':o['observation_sha256'],'action':'exclude'})
        before=workbook.workbook_bytes(view)
        self.flow.close();self.flow=air_fixtures.fixtures.drawing.TakeoffWorkflow(self.workspace);self.workspace.workflow=self.flow
        self.assertEqual(before,workbook.workbook_bytes(self.flow.view()))
        rows=workbook.workbook_sheets(self.flow.view())[0]['rows'][5:]
        scope=next(r for r in rows if r[0]=='Air-device scope');self.assertEqual(value(scope[3]),0);self.assertEqual(value(scope[4]),'UNKNOWN')

    def test_air_unknown_purchase_and_coverage_remain_unknown(self):
        view,_=self.read();sheets=workbook.workbook_sheets(view)
        row=sheets[2]['rows'][5];self.assertEqual(value(row[3]),1);self.assertEqual(value(row[7]),'UNKNOWN')
        scope=next(r for r in sheets[0]['rows'][5:] if r[0]=='Air-device scope')
        self.assertEqual(value(scope[3]),1);self.assertEqual(value(scope[4]),'UNKNOWN')


if __name__=='__main__':unittest.main()
