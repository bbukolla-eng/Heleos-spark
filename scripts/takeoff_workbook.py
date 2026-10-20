"""Source-linked draft workbook projection of verified current takeoff views.

No recognition, measurement, quantity adjudication or persistence occurs here.
Cached quantities are reconciled to the deterministic core before serialization.
"""
from decimal import Decimal, ROUND_HALF_EVEN
import importlib.util
import json
from pathlib import Path
import re

_spec = importlib.util.spec_from_file_location('heleos_xlsx_workbook', Path(__file__).with_name('xlsx_workbook.py'))
_writer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_writer)
LIMIT = 10 ** 12
UNKNOWN = 'UNKNOWN'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _text(value, style='body', location=None):
    result = {'text': str(value), 'style': style}
    if location: result['location'] = location
    return result


def _number(value, style='integer'):
    return _text(UNKNOWN, 'warning') if value is None else {'number': value, 'style': style}


def _formula(expression, cached, quantity=False):
    return {'formula': expression, 'cached': UNKNOWN if cached is None else cached,
            'cache_type': 'string' if cached is None else 'number', 'style': 'quantity' if quantity else 'integer'}


def _sheet(name, note, headers, widths):
    return {'name':name, 'rows':[[],[_text(name, 'title')],[_text('Draft snapshot', 'note'),_text(note, 'note')],[],
            [_text(h, 'header') for h in headers]], 'widths':widths, 'freeze_rows':5, 'filter_row':5}


def _micrometers(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:0|[1-9][0-9]*)\.[0-9]{6}', value):
        raise ValueError('Workbook requires exact six-decimal meter quantities.')
    number = int(value.replace('.', ''))
    if not 0 < number < LIMIT:
        raise ValueError('Workbook length input is outside its verified precision range.')
    return number


def _feet(number):
    if type(number) is not int or not 0 <= number < LIMIT:
        raise ValueError('Workbook aggregate exceeds its verified precision range.')
    return format((Decimal(number) / Decimal(304800)).quantize(Decimal('0.01'), rounding=ROUND_HALF_EVEN), '.2f')


def _feet_formula(value):
    quotient = 'INT(('+value+')/3048)'; remainder = 'MOD(('+value+'),3048)'
    return '('+quotient+'+IF(OR('+remainder+'>1524,AND('+remainder+'=1524,MOD('+quotient+',2)=1)),1,0))/100'


def _same(actual, expected, label):
    if (actual is None) != (expected is None) or (actual is not None and Decimal(str(actual)) != Decimal(str(expected))):
        raise ValueError('Workbook projection differs from the current calculation: '+label)


def _unique(records, key):
    indexed = {}
    for value in records:
        identifier = value[key]
        if not isinstance(identifier, str) or identifier in indexed:
            raise ValueError('Workbook record identities must be unique.')
        indexed[identifier] = value
    return indexed


def _group_label(value):
    if isinstance(value, list):
        return '; '.join(str(v['field'])+': '+(str(v['value']) if v['state']=='known' else v['state']) for v in value)
    return '; '.join(k+': '+(_json(v) if isinstance(v, (list,dict)) else str(v) if v is not None else UNKNOWN)
                     for k,v in sorted(value.items()))


def workbook_sheets(view):
    """Return an inspectable workbook model; no source or project writes."""
    project = view.get('project') or {}
    takeoff = _sheet('Takeoff', str(project.get('name') or 'Untitled project')+' | DRAFT snapshot. Correct in Heleos and regenerate. Workbook edits do not update the project.',
        ['Class / scope','Group / work status','Unit','Known subtotal','Complete total','Review state','Details','Complete at export'],[22,66,10,19,19,24,25,20])
    measurements = _sheet('Measurements', 'Current source-bound lengths. Sum exact micrometers before half-even conversion to feet. Historical quantities do not contribute.',
        ['Segment','Group ID','State','Current micrometers','Stored meters','Length (ft)','Contribution micrometers','Source','Issues','Generation'],[35,35,17,22,20,17,25,20,45,35])
    devices = _sheet('Air devices', 'Physical assemblies and requested scope are separate. UNKNOWN purchase quantities are not inferred from physical counts.',
        ['Assembly row','Group ID','Requested scope','Physical each','Contribution each','Remove each','Reinstall each','New purchase each','Source','Attributes','Issues','Generation'],[35,35,20,17,21,18,18,22,20,60,45,35])
    history = _sheet('History', 'Saved generation references only. These values never feed the current Takeoff formulas. Full records and decisions remain in the JSON package.',
        ['Class','Generation','Relation to current','Created','Action','Known subtotal','Saved complete total','Unit','Actor','Reason'],[20,35,22,29,24,20,24,10,25,70])
    sources = _sheet('Sources', 'Exact original identities and geometry; page numbers below are one-based. Source JSON paths are relative to the extracted draft package.',
        ['Observation','Class','Revision SHA-256','Page','Sheet ID','Geometry SHA-256','Graphic geometry','Evidence IDs','Record locator','Source evidence'],[35,20,68,10,68,68,65,65,55,65])
    summary = takeoff['rows']; mrows = measurements['rows']; arows = devices['rows']; hrows = history['rows']; srows = sources['rows']

    def source_link(observation, category, locator, evidence):
        source = observation['source']
        if (type(source.get('index')) is not int or source['index'] < 0 or any(
                not isinstance(source.get(k), str) or not re.fullmatch('[0-9a-f]{64}', source[k])
                for k in ('revision_id','sheet_id','geometry_fingerprint'))):
            raise ValueError('Workbook source lacks exact revision/page/geometry identity.')
        srows.append([observation['id'],category,source['revision_id'],source['index']+1,
            source['sheet_id'],source['geometry_fingerprint'],_json(observation.get('geometry',observation.get('bbox'))),
            _json(observation.get('evidence_ids',[])),locator,_json(evidence)])
        return _text('Page '+str(source['index']+1), 'link', "'Sources'!A"+str(len(srows)))

    def add_summary(category, label, unit, input_sheet, group, start, end, known, final, complete, total_input, member_count=None):
        line = len(summary)+1
        column = 'G' if unit == 'ft' else 'E'
        rng = "'"+input_sheet+"'!"+column+str(start)+':'+column+str(end)
        grng = "'"+input_sheet+"'!B"+str(start)+':B'+str(end)
        if group is None:
            sum_expr = 'SUM('+rng+')'; invalid = 'COUNTIF('+rng+',"INVALID")'
        else:
            # Core group IDs are SHA-256, not workbook wildcard expressions.
            if not re.fullmatch('[0-9a-f]{64}', group): raise ValueError('Invalid workbook group identity.')
            sum_expr = 'SUMIF('+grng+',"'+group+'",'+rng+')'
            invalid = 'COUNTIFS('+grng+',"'+group+'",'+rng+',"INVALID")'
        membership_guard = (',COUNTIF('+grng+',"'+group+'")='+str(member_count)
                            if group is not None else '')
        if known is None:
            subtotal = _text(UNKNOWN, 'warning')
        else:
            _same(_feet(total_input) if unit=='ft' else total_input, known, category+' '+label)
            calculation = _feet_formula(sum_expr) if unit=='ft' else sum_expr
            subtotal = _formula('IF(AND('+invalid+'=0,'+sum_expr+'<'+str(LIMIT)+membership_guard+'),'+calculation+',"UNKNOWN")',known,unit=='ft')
        _same(known if complete else None,final,category+' complete '+label)
        final_cell = _formula('IF(AND(H'+str(line)+'=1,'+str(1 if complete else 0)+'=1,ISNUMBER(D'+str(line)+')),D'+str(line)+',"UNKNOWN")',final,unit=='ft')
        summary.append([category,label,unit,subtotal,final_cell,
            {'formula':'IF(ISNUMBER(E'+str(line)+'),"COMPLETE SNAPSHOT","INCOMPLETE")','cached':'COMPLETE SNAPSHOT' if final is not None else 'INCOMPLETE','cache_type':'string'},
            _text('Open '+input_sheet, 'link', "'"+input_sheet+"'!A5"),1 if complete else 0])

    duct = view.get('duct_takeoff') or {}
    if duct.get('available'):
        generation = duct.get('current_generation_id')
        generations = _unique(duct.get('generations',[]), 'id')
        if generation not in generations: raise ValueError('Current duct generation is missing.')
        segments = _unique(duct['segments'],'id'); groups = _unique(duct['groups'],'id')
        membership = {}; values = {}
        for group in groups.values():
            for identifier in group['row_ids']:
                if identifier in membership or identifier not in segments: raise ValueError('Duct group membership differs from current rows.')
                membership[identifier] = group['id']
        for segment in segments.values():
            state = segment['status']; raw = segment.get('meters'); stored = segment.get('stored_meters')
            if state not in ('current','stale','excluded','duplicate','unresolved'): raise ValueError('Unsupported duct workbook state.')
            if state == 'current':
                if stored != raw or segment['id'] not in membership: raise ValueError('Current duct stored quantity or group differs.')
                number = _micrometers(raw); _same(_feet(number),segment['feet'],'duct row')
            else:
                if raw is not None or segment['feet'] is not None: raise ValueError('Non-current duct row has a current quantity.')
                number = None
            if stored is not None: _micrometers(stored)
            values[segment['id']] = number or 0
            n = len(mrows)+1; cell = 'D'+str(n)
            group_guard = 'B'+str(n)+'="'+membership.get(segment['id'],'')+'"'
            if state == 'current':
                contribution = 'IFERROR(IF(AND(C'+str(n)+'="current",'+group_guard+',ISNUMBER('+cell+'),'+cell+'>0,'+cell+'<'+str(LIMIT)+',MOD('+cell+',1)=0),'+cell+',"INVALID"),"INVALID")'
                length = _formula('IF(ISNUMBER(G'+str(n)+'),'+_feet_formula('G'+str(n))+',"UNKNOWN")',segment['feet'],True)
            else:
                contribution = 'IF(AND(C'+str(n)+'="'+state+'",'+group_guard+'),0,"INVALID")'
                length = _text(UNKNOWN if state in ('stale','unresolved') else state.upper(), 'warning' if state in ('stale','unresolved') else 'note')
            link = source_link(segment['observation'],'Duct','duct-takeoff.json#/segments/'+str(len(mrows)-5),segment.get('evidence',[]))
            mrows.append([segment['id'],membership.get(segment['id'],''),state,_number(number,'input'),
                          stored if stored is not None else UNKNOWN,length,
                          _formula(contribution,number or 0),link,'; '.join(segment['issues']),generation])
        # A blank sentinel gives formulas a valid empty range for zero observations.
        if len(mrows)==5: mrows.append([])
        for group in groups.values():
            total = sum(values[i] for i in group['row_ids'])
            add_summary('Duct group',_group_label(group['group']),'ft','Measurements',group['id'],6,len(mrows),
                        group['known_subtotal_ft'],group['total_ft'],group['complete'],total,len(group['row_ids']))
        combined = sum(values.values())
        add_summary('Duct scope','Combined only for one known work status','ft','Measurements',None,6,len(mrows),
                    duct['known_subtotal_ft'],duct['total_ft'],duct['total_ft'] is not None,combined)
        for item in generations.values():
            result=item['result']
            hrows.append(['Duct',item['id'],'current reference' if item['id']==generation else 'superseded',
                item.get('at',''),item.get('action',item.get('kind','')), _number(result.get('known_subtotal_ft'),'quantity'),
                _number(result.get('total_ft'),'quantity'),'ft',item.get('actor',''),item.get('reason','')])
    else:
        summary.append(['Duct scope','No current duct calculation','ft','NOT AVAILABLE','NOT AVAILABLE','NOT AVAILABLE'])

    air = view.get('air_device_takeoff') or {}; result=air.get('result')
    if air.get('available') and result:
        generation=air.get('generation'); generations=_unique(air.get('history',[]),'id')
        if generation not in generations: raise ValueError('Current air-device generation is missing.')
        observations=_unique([v['observation'] for v in air['observations']],'id')
        rows=_unique(result['rows'],'row_id'); groups=_unique(result['groups'],'group_id'); membership={}; values={}
        for group in groups.values():
            for identifier in group['row_ids']:
                if identifier in membership or identifier not in rows: raise ValueError('Air-device group membership differs.')
                membership[identifier]=group['group_id']
        for row in rows.values():
            n=len(arows)+1; count=row['physical_each']; requested=row['requested']
            if count is not None and (type(count) is not int or not 0 <= count < LIMIT): raise ValueError('Invalid air-device count.')
            if requested is not None and type(requested) is not bool: raise ValueError('Invalid requested scope.')
            requested_text='YES' if requested is True else 'NO' if requested is False else UNKNOWN
            value=count if requested is True and count is not None else 0
            values[row['row_id']]=value
            if requested is True and row['row_id'] not in membership: raise ValueError('Requested air-device row is missing its group.')
            count_guard=('AND(ISNUMBER(D'+str(n)+'),D'+str(n)+'>=0,D'+str(n)+'<'+str(LIMIT)+',MOD(D'+str(n)+',1)=0)' if count is not None else 'D'+str(n)+'="UNKNOWN"')
            formula='IFERROR(IF(AND(C'+str(n)+'="'+requested_text+'",B'+str(n)+'="'+membership.get(row['row_id'],'')+'",'+count_guard+'),'+('D'+str(n) if value or (requested is True and count is not None) else '0')+',"INVALID"),"INVALID")'
            links=[]
            for identifier in row['member_ids']:
                if identifier not in observations: raise ValueError('Air-device source member is missing.')
                obs=observations[identifier]
                evidence=[e for e in air['evidence'] if e['id'] in obs['evidence_ids']]
                links.append(source_link(obs,'Air device','air-device-counts.json#/observations/'+str(list(observations).index(identifier)),evidence))
            arows.append([row['row_id'],membership.get(row['row_id'],''),requested_text,_number(count,'input'),
                _formula(formula,value),_number(row['operations']['remove']),_number(row['operations']['reinstall']),
                _number(row['new_purchase_each']),links[0] if links else 'No source',_group_label([
                    dict(field=k,state=v['state'],value=v['value']) for k,v in row['attributes'].items()]),
                '; '.join(row['issues']),generation])
        if len(arows)==5: arows.append([])
        for group in groups.values():
            add_summary('Air-device group',_group_label(group['key']),'each','Air devices',group['group_id'],6,len(arows),
                group['known_subtotal_each'],group['total_each'],group['complete'],sum(values[i] for i in group['row_ids']),len(group['row_ids']))
        add_summary('Air-device scope','Requested work statuses: '+', '.join(result['scope']['work_statuses']),'each','Air devices',None,6,len(arows),
            result['known_subtotal_each'],result['total_each'],result['complete'],sum(values.values()))
        for item in generations.values():
            saved=item['result'];hrows.append(['Air device',item['id'],'current reference' if item['id']==generation else 'superseded',
                item.get('at',''),item.get('action',''),_number(saved['known_subtotal_each']),_number(saved['total_each']),
                'each',item.get('actor',''),item.get('reason','')])
    else:
        summary.append(['Air-device scope','No current air-device calculation','each','NOT AVAILABLE','NOT AVAILABLE','NOT AVAILABLE'])
    equipment = _sheet('Equipment', 'Reviewed physical assemblies; components, procurement and installation remain separate. History never feeds current totals.',
        ['Assembly row','Group ID','Requested scope','Physical each','Contribution each','Procurement each','Install each','Remove each','Reinstall each','Component each','Source','Family / status','Issues','Generation'],
        [35,35,20,17,21,22,18,18,18,20,20,50,50,35])
    declarations = _sheet('Equipment declarations', 'Schedule quantities are requirements, not observed physical instances. Conflicts remain unresolved.',
        ['Declaration','Family','Work status','Declared each','Observed each','State','Issues','Source'], [35,25,22,20,20,22,55,20])
    counts = view.get('equipment_count_takeoff') or {}
    result = counts.get('result')
    if counts.get('available') and result:
        generation = counts['generation']; generations = _unique(counts['history'], 'id')
        if generation not in generations: raise ValueError('Current equipment generation is missing.')
        observations = _unique([o['observation'] for o in counts['observations']], 'id')
        rows = _unique(result['rows'], 'row_id'); groups = _unique(result['groups'], 'group_id')
        membership = {}; values = {}; erows = equipment['rows']
        for group in groups.values():
            for identifier in group['row_ids']:
                if identifier in membership or identifier not in rows:
                    raise ValueError('Equipment group membership differs from current rows.')
                membership[identifier] = group['group_id']
        for row in rows.values():
            quantities = [row[key] for key in ('physical_each','procurement_each','installation_each','remove_each','reinstall_each','component_each')]
            if any(q is not None and (type(q) is not int or not 0 <= q < LIMIT) for q in quantities):
                raise ValueError('Invalid equipment quantity.')
            requested = row['requested']; count = row['physical_each']
            if requested is not None and type(requested) is not bool: raise ValueError('Invalid equipment requested scope.')
            if requested is True and row['row_id'] not in membership:
                raise ValueError('Requested equipment row is missing its group.')
            requested_text = 'YES' if requested is True else 'NO' if requested is False else UNKNOWN
            value = count if requested is True and count is not None else 0
            values[row['row_id']] = value
            n = len(erows) + 1
            guard = ('AND(ISNUMBER(D'+str(n)+'),D'+str(n)+'>=0,D'+str(n)+'<'+str(LIMIT)+',MOD(D'+str(n)+',1)=0)'
                     if count is not None else 'D'+str(n)+'="UNKNOWN"')
            formula = 'IFERROR(IF(AND(C'+str(n)+'="'+requested_text+'",B'+str(n)+'="'+membership.get(row['row_id'],'')+'",'+guard+'),'+('D'+str(n) if requested is True and count is not None else '0')+',"INVALID"),"INVALID")'
            links = []
            for identifier in row['member_ids']:
                if identifier not in observations: raise ValueError('Equipment source member is missing.')
                obs = observations[identifier]
                evidence = [e for e in counts['evidence'] if e['id'] in obs['evidence_ids']]
                links.append(source_link(obs,'Equipment','equipment-counts.json#/observations/'+str(list(observations).index(identifier)),evidence))
            erows.append([row['row_id'],membership.get(row['row_id'],''),requested_text,_number(count,'input'),
                _formula(formula,value),*[_number(q) for q in quantities[1:]],
                links[0] if links else 'No source',row['family']+' / '+row['work_status'],
                '; '.join(row['issues']),generation])
        if len(erows) == 5: erows.append([])
        for group in groups.values():
            add_summary('Equipment group',_group_label(group['key']),'each','Equipment',group['group_id'],6,len(erows),
                group['known_subtotal_each'],group['total_each'],group['complete'],sum(values[i] for i in group['row_ids']),len(group['row_ids']))
        add_summary('Equipment scope','Selected physical assemblies; purchase and installation shown separately','each','Equipment',None,6,len(erows),
                    result['known_subtotal_each'],result['total_each'],result['complete'],sum(values.values()))
        for item in generations.values():
            saved = item['result']
            hrows.append(['Equipment',item['id'],'current reference' if item['id']==generation else 'superseded',
                item.get('at',''),item.get('action',''),_number(saved['known_subtotal_each']),_number(saved['total_each']),
                'each',item.get('actor',''),item.get('reason','')])
        schedule_inputs = _unique(counts['request']['schedules'],'id')
        for declaration in result['declarations']:
            declared = schedule_inputs[declaration['id']]
            links = []
            for eid in declared['evidence_ids']:
                e = next((v for v in counts['evidence'] if v['id'] == eid), None)
                if e is None: raise ValueError('Equipment schedule evidence is missing.')
                links.append(source_link(dict(e, evidence_ids=[eid]), 'Equipment schedule', 'equipment-counts.json#/evidence', [e]))
            declarations['rows'].append([declaration['id'],declared['family'],declared['work_status'],
                _number(declaration['declared_each']),_number(declaration['observed_each']),declaration['state'],
                '; '.join(declaration['issues']),links[0] if links else 'No source'])
    else:
        summary.append(['Equipment scope','No current physical equipment calculation','each','NOT AVAILABLE','NOT AVAILABLE','NOT AVAILABLE'])
    # Remaining Division 23 categories are explicit outstanding scope, never zero.
    summary.append(['Other Division 23','Full section coverage, equipment recognition, piping, fittings, accessories, controls, insulation and demolition quantity coverage remains outstanding.',
                    '', 'NOT AVAILABLE','NOT AVAILABLE','OUTSTANDING'])
    for sheet in (measurements,devices,history,sources,equipment,declarations):
        if len(sheet['rows'])==5: sheet['rows'].append(['No records'])
    return [takeoff,measurements,devices,history,sources,equipment,declarations]


def workbook_bytes(view):
    return _writer.write_workbook(workbook_sheets(view))
