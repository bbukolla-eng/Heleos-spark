"""Independent ZIP/XML checks for the portable application XLSX writer."""
import copy
import importlib.util
import io
from pathlib import Path
import posixpath
import unittest
import xml.etree.ElementTree as ET
import zipfile

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('test_xlsx_writer',ROOT/'scripts/xlsx_workbook.py')
xlsx=importlib.util.module_from_spec(spec);spec.loader.exec_module(xlsx)
NS={'s':xlsx.S,'r':xlsx.R,'p':xlsx.P}


def sample():
    return [{'name':'Takeoff','rows':[['<Title>'],['Value','Formula','Link'],[12,{'formula':'SUM(A3:A3)', 'cached':12,'cache_type':'number'},
            {'text':'Go to source','location':"'Sources'!A1",'style':'link'}],['=1+1','+SUM(A1)','@SUM(A1)'],
            [{'formula':'IF(A3=12,"UNKNOWN","known")','cached':'UNKNOWN','cache_type':'string'}]],
            'widths':[28,24,30],'freeze_rows':2,'filter_row':2}, {'name':'Sources','rows':[['Source & locator']]}]


class WorkbookWriterTests(unittest.TestCase):
    def archive(self,sheets=None):
        return zipfile.ZipFile(io.BytesIO(xlsx.write_workbook(sheets or sample())))

    def test_deterministic_package_and_relationships_resolve(self):
        self.assertEqual(xlsx.write_workbook(sample()),xlsx.write_workbook(sample()))
        with self.archive() as archive:
            self.assertEqual(archive.testzip(),None)
            for info in archive.infolist():
                self.assertEqual(info.date_time,(1980,1,1,0,0,0));self.assertEqual(info.compress_type,zipfile.ZIP_STORED)
            for path in archive.namelist():
                root=ET.fromstring(archive.read(path))
                if path.endswith('.rels'):
                    base='' if path=='_rels/.rels' else 'xl'
                    for rel in root:
                        self.assertNotIn('TargetMode',rel.attrib)
                        self.assertIn(posixpath.normpath(posixpath.join(base,rel.attrib['Target'])),archive.namelist())
            self.assertFalse(any('externalLink' in path or 'vba' in path for path in archive.namelist()))
            book=ET.fromstring(archive.read('xl/workbook.xml'))
            self.assertEqual(book.find('s:calcPr',NS).get('fullCalcOnLoad'),'1')
            self.assertEqual([e.get('name') for e in book.find('s:sheets',NS)],['Takeoff','Sources'])

    def test_cells_formula_caches_literal_injection_and_escaping(self):
        with self.archive() as archive:
            root=ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        cells={e.get('r'):e for e in root.findall('.//s:c',NS)}
        for ref,text in [('A1','<Title>'),('A4','=1+1'),('B4','+SUM(A1)'),('C4','@SUM(A1)')]:
            self.assertEqual(cells[ref].get('t'),'inlineStr');self.assertEqual(cells[ref].find('s:is/s:t',NS).text,text)
            self.assertIsNone(cells[ref].find('s:f',NS))
        self.assertEqual(cells['B3'].find('s:f',NS).text,'SUM(A3:A3)')
        self.assertEqual(cells['B3'].find('s:v',NS).text,'12')
        self.assertEqual(cells['A5'].get('t'),'str');self.assertEqual(cells['A5'].find('s:v',NS).text,'UNKNOWN')
        self.assertEqual(root.find('s:hyperlinks/s:hyperlink',NS).get('location'),"'Sources'!A1")
        self.assertEqual(root.find('s:sheetViews/s:sheetView/s:pane',NS).get('topLeftCell'),'A3')
        self.assertEqual(root.find('s:autoFilter',NS).get('ref'),'A2:C5')
        self.assertEqual(root.find('s:cols/s:col',NS).get('width'),'28')

    def test_rejects_invalid_values_without_partial_output(self):
        cells=[True,1.5,{'number':'NaN'},{'number':'1000000000000000'}, {'number':'1e2'},'bad\x01',
            '\ud800',{'text':'x','style':'bogus'},{'text':'x','location':"'Missing'!A1"},
            {'text':'x','location':'https://example.com'}, {'text':'x','location':"'Sources'!A50001"},
            {'formula':'SUM(A1:A2)','cached':1,'cache_type':'bogus'},{'number':12,'text':'a'}]
        for value in cells:
            with self.subTest(value=repr(value)),self.assertRaises(ValueError):
                xlsx.write_workbook([{'name':'Sources','rows':[[value]]}])
        for formula in ('=SUM(A1)', 'WEBSERVICE("https://example.com")','HYPERLINK("file","x")',"cmd|'x'!A1",'[evil.xlsx]Sheet!A1','NOW()'):
            with self.subTest(formula=formula),self.assertRaises(ValueError):
                xlsx.write_workbook([{'name':'Sources','rows':[[{'formula':formula,'cached':1,'cache_type':'number'}]]}])

    def test_sheet_names_and_size_limits(self):
        for names in ([],['A','a'],['A/B'],['a'*32],["'bad"],['bad\x00']):
            with self.subTest(names=names),self.assertRaises(ValueError):
                xlsx.write_workbook([{'name':name,'rows':[]} for name in names])
        for rows in ([[None]*257], [[]]*50001, [['x'*32001]], [[None]*256]*782):
            with self.assertRaises(ValueError): xlsx.write_workbook([{'name':'A','rows':rows}])
        self.assertTrue(xlsx.write_workbook([{'name':'A'*31,'rows':[['x'*32000],[{'number':'999999999999999'}]]}]))


if __name__=='__main__': unittest.main()
