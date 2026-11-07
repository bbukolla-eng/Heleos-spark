"""Append-only physical-equipment source reviews inside the atomic workflow.

This adapter does not execute recognition or accept legacy tag counts. Source
regions are explicit reviewed admissions; historical requests remain immutable.
"""
import copy
import csv
from datetime import datetime,timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import uuid


def _load(name):
    path=Path(__file__).with_name(name+'.py')
    spec=importlib.util.spec_from_file_location('project_equipment_'+name,path)
    module=importlib.util.module_from_spec(spec)
    exec(compile(path.read_bytes(),str(path),'exec'),module.__dict__)
    return module


core=_load('equipment_calculation')
geometry=_load('sheet_geometry')
FIELDS=('equipment_count_generations','equipment_count_events','equipment_count_current')


class ProjectEquipmentError(ValueError):
    def __init__(self,code,message):
        super().__init__(message);self.code=code;self.message=message


def _fail(code,message):raise ProjectEquipmentError(code,message)


def _packed(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')


def _implementation():
    return {name:hashlib.sha256(Path(__file__).with_name(name+'.py').read_bytes()).hexdigest()
            for name in ('project_equipment_takeoff','equipment_calculation','equipment_count_rules')}


def initialize(data):
    data.setdefault('equipment_count_generations',[])
    data.setdefault('equipment_count_events',[])
    data.setdefault('equipment_count_current',None)


def _seal(value):
    value['sha256']=core.fingerprint(value);return value


def _verify(data):
    current=data.get('equipment_count_current');generations=data.get('equipment_count_generations',[])
    for field in FIELDS[:2]:
        previous=None;seen=set()
        for record in data.get(field,[]):
            if not isinstance(record,dict) or record.get('sha256')!=core.fingerprint({k:v for k,v in record.items() if k!='sha256'}) or record.get('previous_sha256')!=previous or record.get('id') in seen:
                _fail('equipment_count_integrity','Saved equipment review lineage changed.')
            previous=record['sha256'];seen.add(record['id'])
            if field=='equipment_count_generations' and record['implementation']==_implementation():
                if core.calculate(record['request'])!=record['result']:_fail('equipment_count_integrity','Saved equipment result does not replay its exact request.')
    if (generations and current!=generations[-1]['id']) or (not generations and current is not None):
        _fail('equipment_count_integrity','Current equipment generation is missing or changed.')
    ids={g['id'] for g in generations}
    if any(e['generation'] not in ids for e in data.get('equipment_count_events',[])):
        _fail('equipment_count_integrity','Equipment review event references a missing generation.')


def _geometry(sheet,revision):return geometry.sheet_geometry(sheet,revision)['fingerprint']


def _contexts(data,sheets):
    result=[]
    for key,sheet in sorted(sheets.items()):
        role=data.get('pages',{}).get(key,{}).get('role')
        if role not in core.ROLES:continue
        try:
            revision,index=key.rsplit(':',1)
            source=dict(revision_id=revision,index=int(index),sheet_id=sheet['sheet_id'],geometry_fingerprint=_geometry(sheet,revision))
            core.source(source)
        except (ValueError,TypeError,KeyError):continue
        result.append(dict(source=source,role=role,artifact_sha256=revision,source_key=core.fingerprint(source)))
    return result


def _unavailable(request,data,sheets):
    current={v['source_key']:v for v in _contexts(data,sheets)}
    return [core.fingerprint(c['source']) for c in request['sources']
        if core.fingerprint(c['source']) not in current or current[core.fingerprint(c['source'])]['role']!=c['role']]


def _originals(workspace,request):
    for revision in sorted({c['source']['revision_id'] for c in request['sources']}):
        try:
            snapshot,unused=workspace.verified_pdf(revision)
            snapshot.close()
        except Exception as error:
            if not isinstance(error,(OSError,ValueError,KeyError)) and not (hasattr(error,'code') and hasattr(error,'message')):raise
            _fail('equipment_count_source_changed','Original equipment source could not be verified: '+getattr(error,'message',str(error)))


def apply(workspace,data,sheets,action,values,actor,reason):
    """Validate completely before handing the atomic workflow changed fields."""
    try:
        core.text(actor,'Reviewer');core.text(reason,'Review reason')
        if action!='equipment_count_save':_fail('equipment_count_action','Unknown equipment count action.')
        core.exact(values,{'generation','request'},'Equipment save')
        if 'equipment' not in data.get('project',{}).get('scopes',[]):_fail('equipment_count_scope','Include equipment in project scope before reviewing quantities.')
        if _implementation()!=LOADED_IMPLEMENTATION:_fail('equipment_count_implementation_changed','Reload the application after the equipment implementation changes.')
        candidate=copy.deepcopy(data);initialize(candidate);_verify(candidate)
        intent=dict(action=action,values=values,actor=actor,reason=reason)
        intent_sha=core.fingerprint(intent)
        by_generation={g['id']:g for g in candidate['equipment_count_generations']}
        if any(e['intent_sha256']==intent_sha and by_generation[e['generation']]['implementation']==_implementation() for e in candidate['equipment_count_events']):return candidate['equipment_count_current']
        if values['generation']!=candidate['equipment_count_current']:_fail('equipment_count_stale_edit','The equipment draft changed. Reload before saving your correction.')
        request=copy.deepcopy(values['request']);result=core.calculate(request)
        if _unavailable(request,candidate,sheets):_fail('equipment_count_source_changed','Equipment source page, coordinates or role changed. Review current source regions.')
        _originals(workspace,request)
        generations=candidate['equipment_count_generations'];events=candidate['equipment_count_events']
        if generations and request==generations[-1]['request'] and generations[-1]['implementation']==_implementation():return candidate['equipment_count_current']
        identifier=uuid.uuid4().hex;at=datetime.now(timezone.utc).isoformat()
        generations.append(_seal(dict(id=identifier,at=at,action=action,actor=actor,reason=reason,request=request,result=result,
            implementation=_implementation(),previous_sha256=generations[-1]['sha256'] if generations else None)))
        events.append(_seal(dict(id=uuid.uuid4().hex,at=at,action=action,actor=actor,reason=reason,intent_sha256=intent_sha,
            generation=identifier,request_sha256=core.fingerprint(request),previous_sha256=events[-1]['sha256'] if events else None)))
        candidate['equipment_count_current']=identifier
        data.update({k:candidate[k] for k in FIELDS})
        return identifier
    except core.EquipmentCalculationError as error:_fail(error.code,error.message)


def view(workspace,data,sheets):
    _verify(data)
    generations=data.get('equipment_count_generations',[]);saved=generations[-1] if generations else None
    request=copy.deepcopy(saved['request']) if saved else core.empty_request()
    unavailable=_unavailable(request,data,sheets) if saved else []
    implementation_changed=bool(saved and saved['implementation']!=_implementation())
    if implementation_changed:unavailable=[core.fingerprint(c['source']) for c in request['sources']]
    result=core.calculate(request,unavailable_source_keys=unavailable) if saved else None
    issues=[dict(code='equipment_count_source_changed',message='A saved equipment source page, role or coordinate identity changed.',source=c['source'])
            for c in request['sources'] if core.fingerprint(c['source']) in unavailable]
    if implementation_changed:issues.append(dict(code='equipment_count_implementation_changed',message='Equipment calculation changed; review and save a current draft.',source=None))
    if result:
        issues.extend(dict(code=code,message=code.replace('_',' '),source=None) for code in result['issues'])
    value=dict(available=saved is not None,generation=data.get('equipment_count_current'),fingerprint=None,stale=bool(unavailable or implementation_changed),
        request=request,result=result,observations=[dict(observation=copy.deepcopy(o),observation_sha256=core.fingerprint(o)) for o in request['observations']],
        evidence=copy.deepcopy(request['evidence']),sources=copy.deepcopy(request['sources']),current_source_contexts=_contexts(data,sheets),
        history=copy.deepcopy(generations),decisions=copy.deepcopy(data.get('equipment_count_events',[])),issues=issues)
    value['fingerprint']=core.fingerprint({k:v for k,v in value.items() if k not in {'fingerprint','history','decisions','current_source_contexts'}})
    return value


def export_files(workspace,data,sheets):
    current=view(workspace,data,sheets)
    for generation in data.get('equipment_count_generations',[]):_originals(workspace,generation['request'])
    stream=io.StringIO(newline='');writer=csv.writer(stream,lineterminator='\n')
    writer.writerow(['generation','row_id','family','work_status','physical_each','procurement_each','installation_each','remove_each','reinstall_each','component_each','issues','source_evidence'])
    def cell(value):
        value='UNKNOWN' if value is None else str(value)
        return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
    by_id={o['id']:o for o in current['request']['observations']}
    for row in (current['result'] or {}).get('rows',[]):
        values=[current['generation'],row['row_id'],row['family'],row['work_status']]+[row[k] for k in ('physical_each','procurement_each','installation_each','remove_each','reinstall_each','component_each')]
        values += [json.dumps(row['issues']),json.dumps([by_id[i] for i in row['member_ids']],sort_keys=True)]
        writer.writerow([cell(v) for v in values])
    return {'equipment-counts.json':_packed(current),'equipment-count-history.json':_packed({k:data.get(k) for k in FIELDS}),
            'equipment-counts.csv':stream.getvalue().encode('utf-8-sig')}


LOADED_IMPLEMENTATION=_implementation()
