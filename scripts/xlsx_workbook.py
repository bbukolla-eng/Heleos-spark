"""Small deterministic SpreadsheetML writer. Python 3.9+ standard library.

Serialization only: quantities and cached formula results belong to the caller.
See the pinned 2026-09-16 workbook contract and verified research record.
"""
from decimal import Decimal, InvalidOperation
import io
import math
import re
import xml.etree.ElementTree as ET
import zipfile

S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
P = 'http://schemas.openxmlformats.org/package/2006/relationships'
C = 'http://schemas.openxmlformats.org/package/2006/content-types'
STYLES = ('body', 'title', 'header', 'note', 'warning', 'input', 'quantity', 'integer', 'link')
FUNCTIONS = set('IF IFERROR AND OR ISNUMBER COUNT COUNTA SUM SUMIF SUMIFS COUNTIF COUNTIFS INT MOD ROUND ABS'.split())
MAX_CELLS = 200000


def _element(parent, tag, **attributes):
    return ET.SubElement(parent, tag, {k: str(v) for k, v in attributes.items()})


def _xml(root):
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def _text(value):
    if not isinstance(value, str) or len(value) > 32000:
        raise ValueError('Workbook text must be a string of at most 32000 characters.')
    if any(not (c in '\t\n\r' or 32 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or
                0x10000 <= ord(c) <= 0x10FFFF) for c in value):
        raise ValueError('Workbook text contains XML-invalid characters.')
    return value


def _number(value):
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?', value):
        raise ValueError('Workbook numbers require integers or plain decimal text.')
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise ValueError('Invalid workbook number.') from None
    if len(parsed.as_tuple().digits) > 15 or not parsed.is_finite():
        raise ValueError('Workbook numbers exceed 15 decimal digits.')
    return value


def _formula(value):
    value = _text(value)
    if not value or value.startswith('=') or any(x in value for x in ('[', ']', '|', '\\', '://')):
        raise ValueError('Unsupported workbook formula.')
    # Remove string and quoted sheet literals before inspecting identifiers.
    stripped = re.sub(r'"(?:[^"]|"")*"|\'(?:[^\']|\'\')*\'', '', value)
    if re.search(r'[^A-Za-z0-9_.$!:,+*/()<> =%-]', stripped):
        raise ValueError('Unsupported formula character.')
    calls = re.findall(r'([A-Za-z_][A-Za-z0-9_.]*)\s*\(', stripped)
    if any(name not in FUNCTIONS for name in calls):
        raise ValueError('Unsupported workbook function.')
    return value


def _column(index):
    result = ''
    while index:
        index, digit = divmod(index - 1, 26)
        result = chr(65 + digit) + result
    return result


def _location(value, names):
    if not isinstance(value, str):
        raise ValueError('Invalid internal workbook link.')
    match = re.fullmatch(r"'((?:[^']|'')+)'!([A-Z]{1,3})([1-9][0-9]*)", value)
    if not match or match[1].replace("''", "'") not in names:
        raise ValueError('Links must target a named internal worksheet.')
    column = 0
    for c in match[2]:
        column = column * 26 + ord(c) - 64
    if column > 256 or int(match[3]) > 50000:
        raise ValueError('Internal workbook link exceeds supported bounds.')
    return value


def _styles():
    root = ET.Element('styleSheet', xmlns=S)
    formats = _element(root, 'numFmts', count=1)
    _element(formats, 'numFmt', numFmtId=164, formatCode='0.00')
    fonts = _element(root, 'fonts', count=5)
    for size, bold, italic, color, underline in ((10, False, False, 'FF172B4D', False),
            (14, True, False, 'FF172B4D', False), (10, True, False, 'FFFFFFFF', False),
            (10, False, True, 'FF526477', False), (10, False, False, 'FF0563C1', True)):
        font = _element(fonts, 'font')
        _element(font, 'sz', val=size); _element(font, 'name', val='Arial'); _element(font, 'color', rgb=color)
        if bold: _element(font, 'b')
        if italic: _element(font, 'i')
        if underline: _element(font, 'u')
    fills = _element(root, 'fills', count=5)
    for pattern, color in (('none', None), ('gray125', None), ('solid', 'FF17365D'),
                           ('solid', 'FFFFE8B0'), ('solid', 'FFF0F5FA')):
        fill = _element(fills, 'fill'); pat = _element(fill, 'patternFill', patternType=pattern)
        if color: _element(pat, 'fgColor', rgb=color); _element(pat, 'bgColor', indexed=64)
    borders = _element(root, 'borders', count=1); border = _element(borders, 'border')
    for edge in ('left', 'right', 'top', 'bottom', 'diagonal'): _element(border, edge)
    xfs = _element(root, 'cellStyleXfs', count=1)
    _element(xfs, 'xf', numFmtId=0, fontId=0, fillId=0, borderId=0)
    xfs = _element(root, 'cellXfs', count=len(STYLES))
    for font, fill, number, align in ((0,0,0,'left'), (1,0,0,'left'), (2,2,0,'center'),
            (3,0,0,'left'), (0,3,0,'left'), (0,4,0,'right'), (0,0,164,'right'),
            (0,0,1,'right'), (4,0,0,'left')):
        xf = _element(xfs, 'xf', numFmtId=number, fontId=font, fillId=fill, borderId=0,
                      xfId=0, applyAlignment=1, applyNumberFormat=1)
        _element(xf, 'alignment', horizontal=align, vertical='center', wrapText=1)
    cellstyles = _element(root, 'cellStyles', count=1)
    _element(cellstyles, 'cellStyle', name='Normal', xfId=0, builtinId=0)
    return _xml(root)


def _row_height(values, widths):
    if not values:
        return 9
    lines = 1
    for i, value in enumerate(values):
        if value is None:
            continue
        if isinstance(value, dict):
            value = value.get('text', value.get('cached', value.get('number', '')))
        width = widths[i] if i < len(widths) else 12
        lines = max(lines, sum(max(1, math.ceil(len(part) / max(1, width * .9)))
                               for part in str(value).split('\n')))
    return min(409, 14 * lines + 8)


def write_workbook(sheets, title='Heleos takeoff'):
    """Return portable XLSX bytes; reject invalid input without partial output."""
    _text(title)
    if not isinstance(sheets, list) or not 1 <= len(sheets) <= 16:
        raise ValueError('Expected 1 to 16 worksheets.')
    names = []
    for sheet in sheets:
        if not isinstance(sheet, dict): raise ValueError('Invalid worksheet.')
        name = _text(sheet.get('name'))
        if not name or len(name) > 31 or re.search(r'[\[\]:*?/\\]', name) or name.startswith("'") or name.endswith("'"):
            raise ValueError('Invalid worksheet name.')
        if name.casefold() in [v.casefold() for v in names]: raise ValueError('Duplicate worksheet name.')
        names.append(name)
    parts = {'xl/styles.xml': _styles()}
    workbook = ET.Element('workbook', xmlns=S, **{'xmlns:r': R})
    sheetlist = _element(workbook, 'sheets')
    relationships = ET.Element('Relationships', xmlns=P)
    types = ET.Element('Types', xmlns=C)
    _element(types, 'Default', Extension='rels', ContentType='application/vnd.openxmlformats-package.relationships+xml')
    _element(types, 'Default', Extension='xml', ContentType='application/xml')
    count = 0
    for index, sheet in enumerate(sheets, 1):
        rows = sheet.get('rows'); widths = sheet.get('widths', [])
        if not isinstance(rows, list) or len(rows) > 50000:
            raise ValueError('Invalid worksheet row count.')
        if not isinstance(widths, list) or len(widths) > 256 or any(type(w) not in (int, float) or not 1 <= w <= 255 for w in widths):
            raise ValueError('Invalid worksheet widths.')
        freeze = sheet.get('freeze_rows', 0); header = sheet.get('filter_row')
        if type(freeze) is not int or not 0 <= freeze <= len(rows) or (header is not None and
                (type(header) is not int or not 1 <= header <= len(rows))):
            raise ValueError('Invalid worksheet pane or filter.')
        root = ET.Element('worksheet', xmlns=S)
        views = _element(root, 'sheetViews'); view = _element(views, 'sheetView', showGridLines=0, workbookViewId=0)
        if freeze: _element(view, 'pane', ySplit=freeze, topLeftCell='A'+str(freeze+1), activePane='bottomLeft', state='frozen')
        _element(root, 'sheetFormatPr', defaultRowHeight=30)
        if widths:
            cols = _element(root, 'cols')
            for i, width in enumerate(widths, 1): _element(cols, 'col', min=i, max=i, width=width, customWidth=1)
        data = _element(root, 'sheetData'); links = []; maxcol = 1
        for rownum, values in enumerate(rows, 1):
            if not isinstance(values, list) or len(values) > 256: raise ValueError('Invalid worksheet row.')
            count += len(values)
            if count > MAX_CELLS: raise ValueError('Workbook exceeds cell limit.')
            maxcol = max(maxcol, len(values)); row = _element(data, 'row', r=rownum,
                ht=_row_height(values, widths), customHeight=1)
            for colnum, value in enumerate(values, 1):
                if value is None: continue
                if isinstance(value, str): value = {'text': value}
                elif type(value) is int: value = {'number': value}
                if not isinstance(value, dict): raise ValueError('Invalid workbook cell.')
                kinds = set(value) & {'text', 'number', 'formula'}
                if len(kinds) != 1 or value.get('style', 'body') not in STYLES: raise ValueError('Invalid cell type or style.')
                kind = next(iter(kinds)); allowed = {kind, 'style'} | ({'location'} if kind == 'text' else {'cached', 'cache_type'} if kind == 'formula' else set())
                if set(value) - allowed: raise ValueError('Unsupported workbook cell field.')
                ref = _column(colnum)+str(rownum); cell = _element(row, 'c', r=ref, s=STYLES.index(value.get('style','body')))
                if kind == 'text':
                    cell.set('t', 'inlineStr'); inline = _element(cell, 'is'); t = _element(inline, 't', **{'xml:space':'preserve'}); t.text = _text(value[kind])
                    if 'location' in value: links.append((ref, _location(value['location'], names)))
                elif kind == 'number': _element(cell, 'v').text = _number(value[kind])
                else:
                    _element(cell, 'f').text = _formula(value[kind])
                    cache_type = value.get('cache_type')
                    if cache_type not in ('number', 'string'): raise ValueError('Formula requires a typed cache.')
                    if cache_type == 'string': cell.set('t', 'str')
                    _element(cell, 'v').text = (_number if cache_type == 'number' else _text)(value.get('cached'))
        if header is not None: _element(root, 'autoFilter', ref='A'+str(header)+':'+_column(maxcol)+str(len(rows)))
        if links:
            parent = _element(root, 'hyperlinks')
            for ref, location in links: _element(parent, 'hyperlink', ref=ref, location=location)
        part = 'xl/worksheets/sheet'+str(index)+'.xml'; parts[part] = _xml(root)
        _element(sheetlist, 'sheet', name=sheet['name'], sheetId=index, **{'r:id':'rId'+str(index)})
        _element(relationships, 'Relationship', Id='rId'+str(index), Type=R+'/worksheet', Target='worksheets/sheet'+str(index)+'.xml')
        _element(types, 'Override', PartName='/'+part, ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml')
    _element(workbook, 'calcPr', calcId=191029, calcMode='auto', fullCalcOnLoad=1, forceFullCalc=1)
    _element(relationships, 'Relationship', Id='rIdStyles', Type=R+'/styles', Target='styles.xml')
    for part, content in (('xl/workbook.xml','sheet.main'), ('xl/styles.xml','styles')):
        _element(types, 'Override', PartName='/'+part, ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.'+content+'+xml')
    package = ET.Element('Relationships', xmlns=P)
    _element(package, 'Relationship', Id='rId1', Type=R+'/officeDocument', Target='xl/workbook.xml')
    parts.update({'[Content_Types].xml':_xml(types),'_rels/.rels':_xml(package),
                  'xl/workbook.xml':_xml(workbook),'xl/_rels/workbook.xml.rels':_xml(relationships)})
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(parts):
            info = zipfile.ZipInfo(name, (1980,1,1,0,0,0)); info.create_system=0; info.external_attr=0o600<<16
            archive.writestr(info, parts[name])
    return output.getvalue()
