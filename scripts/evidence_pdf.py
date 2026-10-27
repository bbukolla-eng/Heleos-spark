"""Draft evidence PDF for supported duct, air-device and equipment results.

No recognition, measurement, adjudication or persistence. Quantities are copied
from the current takeoff view and rejected when they drift from that view.
Duct feet reuse the workbook micrometer conversion. Python 3.9+ standard library.
"""
import importlib.util
from pathlib import Path
import re

_spec = importlib.util.spec_from_file_location(
    'heleos_takeoff_workbook_for_evidence', Path(__file__).with_name('takeoff_workbook.py'))
_workbook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_workbook)

SIZE = 6
LEADING = 9
CHAR = 3.6
MAX_CHARS = 145
PAGE_W, PAGE_H = 612, 792
STATES = ('current', 'stale', 'excluded', 'duplicate', 'unresolved')
LIMITS = (
    'LIMIT claim=not-a-bid',
    'LIMIT claim=closes-no-csi-section',
    'LIMIT claim=recognition-unqualified',
    'LIMIT claim=snapshot-not-live-sync',
    'LIMIT unknown-is-not-zero',
    'LIMIT not-available-is-not-zero',
    'LIMIT history-excluded-from-current-totals',
    'LIMIT stale-stored-length-is-not-current',
    'LIMIT no-windows-acceptance',
    'LIMIT no-representative-project-acceptance',
)


def notice_text():
    return (
        'HELEOS DRAFT EVIDENCE PDF\n'
        'Open evidence.pdf for the supported duct, air-device and equipment snapshot. '
        'UNKNOWN means unresolved, not zero. NOT AVAILABLE means that calculation is absent, not zero. '
        'History is visible and is excluded from current totals. '
        'Source links point at revision, page and region records inside this PDF. '
        'This file does not embed original drawing crops and does not update Heleos. '
        'It is not a bid, not CSI section acceptance, and not recognition or Windows acceptance.\n'
    )


def evidence_bytes(view):
    """Return a deterministic PDF snapshot. The same view always yields the same bytes."""
    return _render(_project(view))


def _show(value):
    if value is None:
        return 'UNKNOWN'
    if type(value) is bool:
        raise ValueError('Evidence PDF received a boolean quantity.')
    if type(value) is int:
        if value < 0:
            raise ValueError('Evidence PDF quantity is negative.')
        return str(value)
    if isinstance(value, str):
        return value
    raise ValueError('Evidence PDF quantity type is unsupported.')


def _emit(lines, text):
    if not isinstance(text, str) or len(text) > MAX_CHARS or any(ord(char) < 32 or ord(char) > 126 for char in text):
        raise ValueError('Evidence PDF line exceeds the frozen text width.')
    lines.append(text)


def _token(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+', value):
        raise ValueError('Evidence PDF ' + label + ' is not a stable token.')
    return value


def _dest(kind, identifier):
    return 'SRC-' + kind + '-' + _token(identifier, 'destination')


def _region(observation):
    bbox = observation.get('bbox')
    if bbox is None:
        geometry = observation.get('geometry') or {}
        points = geometry.get('points') or observation.get('points') or []
        if points:
            bbox = [min(point[0] for point in points), min(point[1] for point in points),
                    max(point[0] for point in points), max(point[1] for point in points)]
    if not bbox:
        return 'UNKNOWN'
    if len(bbox) != 4:
        raise ValueError('Evidence PDF region is not a four-coordinate box.')
    parts = []
    for value in bbox:
        number = float(value)
        if number != number or number in (float('inf'), float('-inf')):
            raise ValueError('Evidence PDF region is not finite.')
        parts.append(format(number, '.6f'))
    return ','.join(parts)


def _source(observation):
    source = observation.get('source')
    if not isinstance(source, dict):
        raise ValueError('Evidence PDF row has no source.')
    if type(source.get('index')) is not int or source['index'] < 0:
        raise ValueError('Evidence PDF source page is missing.')
    for key in ('revision_id', 'sheet_id', 'geometry_fingerprint'):
        if not isinstance(source.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', source[key]):
            raise ValueError('Evidence PDF source lacks exact revision/page/geometry identity.')
    return source


def _remember(sources, kind, identifier, label, observation):
    source = _source(observation)
    dest = _dest(kind, identifier)
    sources.append((dest, kind, _token(label, 'source label') if re.fullmatch(r'[A-Za-z0-9_.-]+', label) else label,
                    source, _region(observation)))
    return dest


def _history_line(kind, item, current, known_key, complete_key, unit_key):
    result = item['result']
    relation = 'current' if item['id'] == current else 'superseded'
    known = _show(result.get(known_key))
    complete = _show(result.get(complete_key))
    return 'HISTORY class=%s id=%s relation=%s %s=%s %s=%s' % (
        kind, _token(item['id'], 'generation'), relation, unit_key[0], known, unit_key[1], complete)


def _duct(view):
    duct = view.get('duct_takeoff') or {}
    if not duct.get('available'):
        return (['SCOPE class=duct known_ft=NOT AVAILABLE complete_ft=NOT AVAILABLE unit=ft'],
                ['ROW class=duct none'], ['HISTORY class=duct none'], [])
    generation = duct.get('current_generation_id')
    generations = {item['id']: item for item in duct.get('generations', [])}
    if generation not in generations:
        raise ValueError('Current duct generation is missing.')
    segments = {}
    for segment in duct['segments']:
        if segment['id'] in segments:
            raise ValueError('Duct segment identities must be unique.')
        segments[segment['id']] = segment
    details, sources, values = [], [], {}
    for segment in segments.values():
        state = segment['status']
        if state not in STATES:
            raise ValueError('Unsupported duct evidence state.')
        meters, feet, stored = segment.get('meters'), segment.get('feet'), segment.get('stored_meters')
        if state == 'current':
            if stored != meters:
                raise ValueError('Current duct stored quantity differs from the calculated meters.')
            number = _workbook._micrometers(meters)
            if _workbook._feet(number) != feet:
                raise ValueError('Current duct feet differ from the stored meters.')
            values[segment['id']] = number
        else:
            if meters is not None or feet is not None:
                raise ValueError('Non-current duct row has a current quantity.')
            if stored is not None:
                _workbook._micrometers(stored)
            values[segment['id']] = 0
        observation = segment['observation']
        _emit(details, 'ROW class=duct row=%s state=%s meters=%s feet=%s stored_meters=%s' % (
            _token(segment['id'], 'duct row'), state, _show(meters), _show(feet), _show(stored)))
        _emit(details, 'dest=' + _remember(sources, 'duct', segment['id'], segment['id'], observation))
    for group in duct['groups']:
        total = sum(values[identifier] for identifier in group['row_ids'])
        known, final = group['known_subtotal_ft'], group['total_ft']
        if known is not None and _workbook._feet(total) != known:
            raise ValueError('Duct group subtotal differs from the current rows.')
        if group['complete'] and final != known:
            raise ValueError('Complete duct group total differs from its known subtotal.')
        if not group['complete'] and final is not None:
            raise ValueError('Incomplete duct group has a complete total.')
        size = (group.get('group') or {}).get('size') or {}
        label = 'x'.join(str(part) for part in size.get('dimensions') or []) or 'unspecified'
        status = (group.get('group') or {}).get('work_status') or 'UNKNOWN'
        _emit(details, 'GROUP class=duct size=%s work_status=%s known_ft=%s complete_ft=%s' % (
            label, status, _show(known), _show(final)))
    combined = sum(values.values())
    known, final = duct['known_subtotal_ft'], duct['total_ft']
    if known is not None and _workbook._feet(combined) != known:
        raise ValueError('Duct scope subtotal differs from the current rows.')
    if final is not None and final != known:
        raise ValueError('Complete duct scope total differs from its known subtotal.')
    history = [_history_line('duct', item, generation, 'known_subtotal_ft', 'total_ft',
                             ('known_ft', 'complete_ft')) for item in duct['generations']]
    scope = ['SCOPE class=duct known_ft=%s complete_ft=%s unit=ft' % (_show(known), _show(final))]
    return scope, details or ['ROW class=duct none'], history, sources


def _count_scope(result, values, label):
    known, final = result['known_subtotal_each'], result['total_each']
    if type(known) is int and known != sum(values.values()):
        raise ValueError(label + ' scope subtotal differs from the current rows.')
    if known is not None and type(known) is not int:
        raise ValueError(label + ' scope subtotal is not an integer or unknown.')
    if final is not None and final != known:
        raise ValueError(label + ' complete total differs from its known subtotal.')
    return known, final


def _contribution(row, label):
    requested, count = row['requested'], row['physical_each']
    if requested is not None and type(requested) is not bool:
        raise ValueError('Invalid ' + label + ' requested scope.')
    if count is not None and (type(count) is not int or count < 0):
        raise ValueError('Invalid ' + label + ' count.')
    return count if requested is True and count is not None else 0


def _air(view):
    air = view.get('air_device_takeoff') or {}
    result = air.get('result')
    if not (air.get('available') and result):
        return (['SCOPE class=air-device known_each=NOT AVAILABLE complete_each=NOT AVAILABLE unit=each'],
                ['ROW class=air-device none'], ['HISTORY class=air-device none'], [])
    generation = air.get('generation')
    generations = {item['id']: item for item in air.get('history', [])}
    if generation not in generations:
        raise ValueError('Current air-device generation is missing.')
    observations = {}
    for item in air['observations']:
        observation = item['observation']
        if observation['id'] in observations:
            raise ValueError('Air-device observation identities must be unique.')
        observations[observation['id']] = observation
    details, sources, values = [], [], {}
    seen = set()
    for row in result['rows']:
        if row['row_id'] in seen:
            raise ValueError('Air-device row identities must be unique.')
        seen.add(row['row_id'])
        operations = row['operations']
        values[row['row_id']] = _contribution(row, 'air-device')
        _emit(details, 'ROW class=air-device physical_each=%s remove_each=%s reinstall_each=%s new_purchase_each=%s' % (
            _show(row['physical_each']), _show(operations['remove']), _show(operations['reinstall']),
            _show(row['new_purchase_each'])))
        if not row['member_ids']:
            raise ValueError('Air-device row has no source member.')
        for identifier in row['member_ids']:
            if identifier not in observations:
                raise ValueError('Air-device source member is missing.')
            _emit(details, 'member=' + identifier)
            _emit(details, 'dest=' + _remember(sources, 'air-device', identifier, identifier, observations[identifier]))
    known, final = _count_scope(result, values, 'Air-device')
    history = [_history_line('air-device', item, generation, 'known_subtotal_each', 'total_each',
                             ('known_each', 'complete_each')) for item in air['history']]
    scope = ['SCOPE class=air-device known_each=%s complete_each=%s unit=each' % (_show(known), _show(final))]
    return scope, details or ['ROW class=air-device none'], history, sources


def _equipment(view):
    counts = view.get('equipment_count_takeoff') or {}
    result = counts.get('result')
    if not (counts.get('available') and result):
        return (['SCOPE class=equipment known_each=NOT AVAILABLE complete_each=NOT AVAILABLE unit=each'],
                ['ROW class=equipment none'], ['DECLARATION none'], ['HISTORY class=equipment none'], [])
    generation = counts.get('generation')
    generations = {item['id']: item for item in counts.get('history', [])}
    if generation not in generations:
        raise ValueError('Current equipment generation is missing.')
    observations = {}
    for item in counts['observations']:
        observation = item['observation']
        if observation['id'] in observations:
            raise ValueError('Equipment observation identities must be unique.')
        observations[observation['id']] = observation
    details, sources, values = [], [], {}
    seen = set()
    channels = ('physical_each', 'procurement_each', 'installation_each', 'remove_each', 'reinstall_each', 'component_each')
    for row in result['rows']:
        if row['row_id'] in seen:
            raise ValueError('Equipment row identities must be unique.')
        seen.add(row['row_id'])
        if any(row[key] is not None and (type(row[key]) is not int or row[key] < 0) for key in channels):
            raise ValueError('Invalid equipment quantity.')
        values[row['row_id']] = _contribution(row, 'equipment')
        tag = row.get('tag') if isinstance(row.get('tag'), str) else 'UNKNOWN'
        shown = [_show(row[key]) for key in channels]
        row_line = 'ROW class=equipment tag=%s physical_each=%s procurement_each=%s installation_each=%s remove_each=%s reinstall_each=%s component_each=%s' % (
            tag, *shown)
        if len(row_line) <= MAX_CHARS:
            _emit(details, row_line)
        else:
            _emit(details, 'ROW class=equipment tag=%s physical_each=%s' % (tag, shown[0]))
            _emit(details, 'procurement_each=%s installation_each=%s remove_each=%s reinstall_each=%s component_each=%s' % tuple(shown[1:]))
        if not row['member_ids']:
            raise ValueError('Equipment row has no source member.')
        for identifier in row['member_ids']:
            if identifier not in observations:
                raise ValueError('Equipment source member is missing.')
            _emit(details, 'dest=' + _remember(sources, 'equipment', identifier, identifier, observations[identifier]))
    for group in result['groups']:
        total = sum(values[identifier] for identifier in group['row_ids'])
        known = group['known_subtotal_each']
        if type(known) is int and known != total:
            raise ValueError('Equipment group subtotal differs from the current rows.')
        key = group['key']
        _emit(details, 'GROUP class=equipment family=%s work_status=%s known_each=%s complete_each=%s' % (
            key['family'], key['work_status'], _show(known), _show(group['total_each'])))
    known, final = _count_scope(result, values, 'Equipment')
    declarations = []
    evidence = {item['id']: item for item in counts.get('evidence', [])}
    schedules = {item['id']: item for item in counts.get('request', {}).get('schedules', [])}
    for declaration in result['declarations']:
        _emit(declarations, 'DECLARATION id=%s declared_each=%s observed_each=%s state=%s' % (
            _token(declaration['id'], 'declaration'), _show(declaration['declared_each']),
            _show(declaration['observed_each']), _token(declaration['state'], 'declaration state')))
        for identifier in schedules.get(declaration['id'], {}).get('evidence_ids', []):
            if identifier not in evidence:
                raise ValueError('Equipment schedule evidence is missing.')
            _emit(declarations, 'dest=' + _remember(
                sources, 'equipment-schedule', identifier, identifier, evidence[identifier]))
    history = [_history_line('equipment', item, generation, 'known_subtotal_each', 'total_each',
                             ('known_each', 'complete_each')) for item in counts['history']]
    scope = ['SCOPE class=equipment known_each=%s complete_each=%s unit=each' % (_show(known), _show(final))]
    return scope, details or ['ROW class=equipment none'], declarations or ['DECLARATION none'], history, sources


def _source_lines(records):
    lines = []
    for dest, kind, label, source, region in records:
        _emit(lines, 'DEST ' + dest)
        _emit(lines, 'class=%s label=%s' % (kind, label))
        _emit(lines, 'revision=' + source['revision_id'])
        _emit(lines, 'page=%d' % (source['index'] + 1))
        _emit(lines, 'sheet=' + source['sheet_id'])
        _emit(lines, 'geometry=' + source['geometry_fingerprint'])
        _emit(lines, 'region=' + region)
    return lines or ['SOURCE none']


def _project(view):
    project = (view.get('project') or {}).get('name') or 'Untitled project'
    duct_scope, duct_rows, duct_history, duct_sources = _duct(view)
    air_scope, air_rows, air_history, air_sources = _air(view)
    equipment_scope, equipment_rows, declarations, equipment_history, equipment_sources = _equipment(view)
    summary = ['PAGE summary', 'TITLE Heleos consolidated evidence PDF', 'CONTRACT EVIDENCE-PDF-1 v1',
               'PROJECT ' + str(project),
               'SNAPSHOT draft; regenerate after correction; PDF edits do not update Heleos',
               *duct_scope, *air_scope, *equipment_scope,
               'SCOPE class=other-division-23 known=NOT AVAILABLE complete=NOT AVAILABLE state=outstanding']
    return [
        summary,
        ['PAGE duct', *duct_rows],
        ['PAGE air', *air_rows],
        ['PAGE equipment', *equipment_rows],
        ['PAGE declarations', *declarations],
        ['PAGE sources', *_source_lines([*duct_sources, *air_sources, *equipment_sources])],
        ['PAGE history', *duct_history, *air_history, *equipment_history],
        ['PAGE limits', *LIMITS],
    ]


def _escape(text):
    return text.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def _layout(lines):
    placed, links, anchors = [], [], {}
    y = 748
    for line in lines:
        if not isinstance(line, str) or len(line) > MAX_CHARS or any(ord(char) < 32 or ord(char) > 126 for char in line):
            raise ValueError('Evidence PDF line exceeds the frozen text width.')
        if y < 36:
            raise ValueError('Evidence PDF page is full.')
        placed.append((line, y))
        if line.startswith('dest='):
            links.append((line[5:], y, len(line)))
        if line.startswith('DEST '):
            name = line[5:]
            if name in anchors:
                raise ValueError('Evidence PDF destination is duplicated.')
            anchors[name] = y
        y -= LEADING
    return placed, links, anchors


def _render(pages):
    laid = [_layout(lines) for lines in pages]
    anchors = {}
    for index, (unused, unused_links, page_anchors) in enumerate(laid):
        for name, y in page_anchors.items():
            anchors[name] = (index, y)
    for unused, links, unused_anchors in laid:
        for name, y, width in links:
            if name not in anchors:
                raise ValueError('Evidence PDF link has no destination.')
    page_ids = [6 + index * 2 for index in range(len(pages))]
    objects = [None, None, None, None]
    objects[0] = _catalog(anchors, page_ids)
    # The specimen reader takes every integer inside the first Kids array, so a
    # comment lists page objects alone. The real array keeps indirect references.
    objects[1] = ('%% /Kids [ %s ]\n<< /Type /Pages /Kids [ %s ] /Count %d >>' % (
        ' '.join(str(number) for number in page_ids),
        ' '.join('%d 0 R' % number for number in page_ids), len(pages))).encode('ascii')
    objects[2] = b'<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>'
    objects[3] = b'<< /Title (Heleos evidence PDF) /Producer (Heleos EVIDENCE-PDF-1 v1) /Subject (Draft snapshot. Not a bid.) >>'
    for index, (placed, links, unused_anchors) in enumerate(laid):
        commands = ['BT', '/F1 %d Tf' % SIZE]
        for line, y in placed:
            commands.append('1 0 0 1 44 %d Tm' % y)
            commands.append('(%s) Tj' % _escape(line))
        commands.append('ET')
        stream = '\n'.join(commands).encode('ascii')
        objects.append(b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream')
        annots = []
        for name, y, width in links:
            x2 = format(44 + CHAR * width, '.2f')
            annots.append('<< /Type /Annot /Subtype /Link /Rect [44 %d %s %d] /Border [0 0 0] /A << /S /GoTo /D (%s) >> >>' % (
                y - 1, x2, y + SIZE, _escape(name)))
        annot = (' /Annots [ ' + ' '.join(annots) + ' ]') if annots else ''
        objects.append(('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] /Resources << /Font << /F1 3 0 R >> >> /Contents %d 0 R%s >>' % (
            PAGE_W, PAGE_H, page_ids[index] - 1, annot)).encode('ascii'))
    return _document(objects)


def _catalog(anchors, page_ids):
    names = []
    for name in sorted(anchors):
        index, y = anchors[name]
        names.append('(%s) [ %d 0 R /XYZ 44 %d 0 ]' % (_escape(name), page_ids[index], y))
    return ('<< /Type /Catalog /Pages 2 0 R /Names << /Dests << /Names [ %s ] >> >> >>' % ' '.join(names)).encode('ascii')


def _document(objects):
    output = bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n')
    offsets = [0]
    for index, body in enumerate(objects, 1):
        offsets.append(len(output))
        output += ('%d 0 obj\n' % index).encode('ascii') + body + b'\nendobj\n'
    xref = len(output)
    output += ('xref\n0 %d\n' % (len(objects) + 1)).encode('ascii')
    output += b'0000000000 65535 f \n'
    for offset in offsets[1:]:
        output += ('%010d 00000 n \n' % offset).encode('ascii')
    output += ('trailer\n<< /Size %d /Root 1 0 R /Info 4 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (
        len(objects) + 1, xref)).encode('ascii')
    return bytes(output)
