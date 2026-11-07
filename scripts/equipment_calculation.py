"""Source-bound physical equipment review calculation, Python 3.9+ stdlib.

Only explicit observations and supported relationships contribute. Tags, schedule
declarations and model totals are never promoted into physical assemblies.
"""
import copy
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re

_spec=importlib.util.spec_from_file_location('equipment_count_rules',Path(__file__).with_name('equipment_count_rules.py'))
_rules=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_rules)
VERSION='equipment-calculation-1'
REQUEST_SCHEMA='equipment-calculation-request-1'
STATUSES={'new','existing','demolition','relocation','spare','unknown'}
ROLES={'plan','enlarged_plan','detail','riser','schedule','specification','legend','addendum','other'}
PHYSICAL_ROLES={'plan','enlarged_plan','detail','riser'}
CHANNEL_ONLY={'procurement_parent_unresolved','relocation_procurement_conflict','procurement_relationship_unresolved','system_relationship_unresolved'}
OBS_KEYS={'id','source','bbox','kind','family','tag','work_status','disposition','identity','procurement','installation','parent_id','system_id','attributes','evidence_ids','issues'}
REQ_KEYS={'schema','binding','sources','evidence','observations','relations','schedules','scope','coverage'}
MAX_QUANTITY=1000000


class EquipmentCalculationError(ValueError):
    def __init__(self,code,message):
        super().__init__(message);self.code=code;self.message=message


def fail(message,code='equipment_count_invalid'):
    raise EquipmentCalculationError(code,message)


def fingerprint(value):
    try:
        raw=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
    except (ValueError,TypeError,UnicodeError,RecursionError):fail('Equipment records must be finite bounded JSON.')
    if len(raw)>8*1024*1024:fail('Equipment request exceeds its size limit.')
    return hashlib.sha256(raw).hexdigest()


def rule_binding():
    try:return _rules.binding()
    except (OSError,ValueError) as error:fail(str(error),'equipment_count_rules_changed')


def empty_request():
    return dict(schema=REQUEST_SCHEMA,binding=rule_binding(),sources=[],evidence=[],observations=[],relations=[],schedules=[],
        scope=dict(source_keys=[],work_statuses=sorted(STATUSES),required_attributes=[]),
        coverage=dict(state='unknown',evidence_ids=[],unresolved_requirements=[]))


def exact(value,keys,label):
    if not isinstance(value,dict) or set(value)!=set(keys):fail(label+' has missing or unexpected fields.')


def text(value,label,nullable=False):
    if nullable and value is None:return
    if not isinstance(value,str) or not value.strip() or len(value)>2000 or any(ord(c)<32 or 127<=ord(c)<160 or 0xD800<=ord(c)<=0xDFFF for c in value):
        fail(label+' must be nonempty bounded plain text.')


def sequence(value,label):
    if not isinstance(value,list) or len(value)>2000:fail(label+' must be a bounded list.')


def refs(value,label):
    sequence(value,label)
    for v in value:text(v,label)
    if len(set(value))!=len(value):fail(label+' contains duplicate identities.')


def enum(value,allowed,label):
    if not isinstance(value,str) or value not in allowed:fail('Unsupported '+label+'.')


def source(value):
    exact(value,{'revision_id','index','sheet_id','geometry_fingerprint'},'Source')
    for name in ('revision_id','sheet_id','geometry_fingerprint'):
        if not isinstance(value[name],str) or not re.fullmatch('[0-9a-f]{64}',value[name]):fail('Source '+name+' must be SHA-256.')
    if type(value['index']) is not int or not 0<=value['index']<2**32:fail('Source index is invalid.')


def bbox(value):
    if not isinstance(value,list) or len(value)!=4 or any(type(v) not in (float,int) or not math.isfinite(v) or not 0<=v<=1 for v in value):fail('Source region must use finite normalized coordinates.')
    if value[0]>=value[2] or value[1]>=value[3]:fail('Source region must have positive area.')


def unique(records,label):
    sequence(records,label);out={}
    for r in records:
        if not isinstance(r,dict):fail(label+' records must be objects.')
        text(r.get('id'),label+' ID')
        if r['id'] in out:fail(label+' IDs must be unique.')
        out[r['id']]=r
    return out


def validate_request(request):
    fingerprint(request);exact(request,REQ_KEYS,'Request')
    if request['schema']!=REQUEST_SCHEMA or request['binding']!=rule_binding():fail('Equipment schema or approved rule binding changed.','equipment_count_binding')
    contexts={};sequence(request['sources'],'Sources')
    for context in request['sources']:
        exact(context,{'source','role','artifact_sha256'},'Source context');source(context['source']);enum(context['role'],ROLES,'source role')
        if context['artifact_sha256']!=context['source']['revision_id']:fail('Reviewed source must bind the original PDF digest.')
        key=fingerprint(context['source'])
        if key in contexts:fail('Repeated source context.')
        contexts[key]=context
    evidence=unique(request['evidence'],'Evidence')
    for e in evidence.values():
        exact(e,{'id','source','bbox','kind','text','artifact_sha256'},'Evidence');source(e['source']);bbox(e['bbox']);text(e['text'],'Evidence text')
        enum(e['kind'],{'graphic','requirement','schedule','relationship','coverage','tag'},'evidence kind')
        if fingerprint(e['source']) not in contexts or e['artifact_sha256']!=e['source']['revision_id']:fail('Evidence is not bound to a declared original source.')
    observations=unique(request['observations'],'Observations')
    for o in observations.values():
        exact(o,OBS_KEYS,'Observation');source(o['source']);bbox(o['bbox'])
        if fingerprint(o['source']) not in contexts:fail('Observation source context is missing.')
        for field in ('family',):text(o[field],field)
        for field in ('tag','parent_id','system_id'):text(o[field],field,True)
        for field,allowed in [('kind',{'assembly','component','shipping','accessory','spare'}),('work_status',STATUSES),('disposition',{'include','exclude','unresolved'}),('identity',{'established','unresolved'}),('procurement',{'separate','included','none','unknown'}),('installation',{'field','factory','none','unknown'})]:enum(o[field],allowed,field)
        if not isinstance(o['attributes'],dict) or len(o['attributes'])>100:fail('Attributes must be a bounded text map.')
        for k,v in o['attributes'].items():text(k,'Attribute name');text(v,'Attribute value')
        refs(o['evidence_ids'],'Observation evidence');refs(o['issues'],'Observation issues')
        if not set(o['evidence_ids'])<=set(evidence):fail('Observation evidence is missing.')
        if o['parent_id'] is not None and o['parent_id'] not in observations:fail('Included parent is missing.')
    for o in observations.values():
        seen={o['id']};current=o
        while current['parent_id'] is not None:
            if current['parent_id'] in seen:fail('Equipment parent graph contains a cycle.')
            seen.add(current['parent_id']);current=observations[current['parent_id']]
    relations=unique(request['relations'],'Relationships');multiplicity=set();package_members=set()
    for r in relations.values():
        exact(r,{'id','kind','member_ids','each','evidence_ids','scope_text'},'Relationship')
        enum(r['kind'],{'same','distinct','multiplicity','relocation','system','package','tag_reuse'},'relationship kind')
        refs(r['member_ids'],'Relationship members');refs(r['evidence_ids'],'Relationship evidence')
        if not r['member_ids'] or not set(r['member_ids'])<=set(observations) or not set(r['evidence_ids'])<=set(evidence):fail('Relationship members or evidence are missing.')
        if r['kind'] in {'same','distinct','relocation','tag_reuse'} and len(r['member_ids'])<2:fail('Relationship needs at least two members.')
        if r['each'] is not None and (type(r['each']) is not int or not 1<=r['each']<=MAX_QUANTITY):fail('Explicit multiplicity must be a bounded positive integer.')
        if r['kind'] not in {'multiplicity','package'} and r['each'] is not None:fail('This relationship does not accept a quantity.')
        if not isinstance(r['scope_text'],str) or len(r['scope_text'])>2000:fail('Relationship scope is invalid.')
        if r['kind']=='multiplicity':
            if multiplicity & set(r['member_ids']):fail('Overlapping multiplicities cannot be added.')
            multiplicity.update(r['member_ids'])
        if r['kind']=='package':
            if package_members & set(r['member_ids']):fail('Overlapping procurement packages require explicit reconciliation.')
            package_members.update(r['member_ids'])
    schedules=unique(request['schedules'],'Schedule declarations')
    for s in schedules.values():
        exact(s,{'id','family','work_status','member_ids','declared_each','evidence_ids'},'Schedule declaration')
        text(s['family'],'Schedule family');enum(s['work_status'],STATUSES,'schedule work status');refs(s['member_ids'],'Schedule members');refs(s['evidence_ids'],'Schedule evidence')
        if type(s['declared_each']) is not int or not 0<=s['declared_each']<=MAX_QUANTITY:fail('Schedule declaration must be a bounded nonnegative integer.')
        if not set(s['member_ids'])<=set(observations) or not set(s['evidence_ids'])<=set(evidence):fail('Schedule members or evidence are missing.')
    exact(request['scope'],{'source_keys','work_statuses','required_attributes'},'Scope')
    for k in request['scope']:refs(request['scope'][k],'Scope '+k)
    if not set(request['scope']['source_keys'])<=set(contexts) or not set(request['scope']['work_statuses'])<=STATUSES:fail('Selected scope references unknown sources or statuses.')
    exact(request['coverage'],{'state','evidence_ids','unresolved_requirements'},'Coverage')
    enum(request['coverage']['state'],{'complete','unknown'},'coverage state')
    refs(request['coverage']['evidence_ids'],'Coverage evidence');refs(request['coverage']['unresolved_requirements'],'Coverage unresolved requirements')
    if not set(request['coverage']['evidence_ids'])<=set(evidence):fail('Coverage evidence is missing.')
    return contexts,evidence,observations,relations,schedules


def calculate(request, unavailable_source_keys=()):
    """Pure review projection; unavailable keys come only from the project adapter."""
    contexts,evidence,observations,relations,schedules=validate_request(request)
    unavailable=set(unavailable_source_keys)
    if not unavailable<=set(contexts):fail('Unavailable source identity is not in this request.')
    selected=set(request['scope']['source_keys']);work=set(request['scope']['work_statuses'])
    def support(ids,kinds):
        return bool(ids) and all(fingerprint(evidence[i]['source']) not in unavailable for i in ids) and any(evidence[i]['kind'] in kinds for i in ids)
    def related(o,r):return o['id'] in r['member_ids']
    parent={i:i for i in observations}
    def root(i):
        while parent[i]!=i:i=parent[i]
        return i
    issues={i:set(o['issues']) for i,o in observations.items()}
    relation_valid={}
    for r in relations.values():
        valid=support(r['evidence_ids'],{'relationship','requirement','graphic'})
        relation_valid[r['id']]=valid
        if not valid:
            issue={'package':'procurement_relationship_unresolved','system':'system_relationship_unresolved'}.get(r['kind'],'relationship_evidence_unresolved')
            for i in r['member_ids']:issues[i].add(issue)
        if r['kind'] in {'same','relocation'} and valid:
            members=[observations[i] for i in r['member_ids']]
            fields=('family','kind','work_status','parent_id','system_id','procurement','installation')
            if any(len({m[field] for m in members})>1 for field in fields):
                for i in r['member_ids']:issues[i].add('correspondence_conflict')
                relation_valid[r['id']]=False
            else:
                anchor=min(root(i) for i in r['member_ids'])
                for i in r['member_ids']:parent[root(i)]=anchor
        if r['kind']=='relocation' and any(observations[i]['work_status']!='relocation' for i in r['member_ids']):fail('Relocation must bind relocation observations.')
        if r['kind']=='relocation' and len({fingerprint([observations[i]['source'],observations[i]['bbox']]) for i in r['member_ids']})<2:
            for i in r['member_ids']:issues[i].add('relocation_locations_unresolved')
            relation_valid[r['id']]=False
    components={}
    for i in sorted(observations):components.setdefault(root(i),[]).append(i)
    for r in relations.values():
        if r['kind']=='distinct' and relation_valid[r['id']] and len({root(i) for i in r['member_ids']})<len(r['member_ids']):
            for i in r['member_ids']:issues[i].add('correspondence_contradictory')
    # Repeated tags never deduplicate and distinct identities do not erase tag conflict.
    tags={}
    for o in observations.values():
        if o['tag'] and o['disposition']!='exclude':tags.setdefault(o['tag'],[]).append(o['id'])
    for tag,members in tags.items():
        if len({root(i) for i in members})>1 and not any(r['kind']=='tag_reuse' and relation_valid[r['id']] and set(members)<=set(r['member_ids']) for r in relations.values()):
            for i in members:issues[i].add('tag_conflict')
    # Merge multiplicity represented instances into one total rather than plus representative.
    multipliers={};absorbed={}
    for r in relations.values():
        if r['kind']!='multiplicity':continue
        roots=sorted({root(i) for i in r['member_ids']});anchor=roots[0]
        if any(i in absorbed or i in multipliers for i in roots):fail('Multiplicities overlap the same physical assembly.')
        members=[observations[i] for k in roots for i in components[k]]
        fields=('family','kind','work_status','parent_id','system_id','procurement','installation')
        valid=relation_valid[r['id']] and r['each'] is not None and r['each']>=len(roots) and bool(r['scope_text'].strip()) and all(len({m[f] for m in members})==1 for f in fields)
        if not valid:
            for o in members:issues[o['id']].add('multiplicity_unresolved')
        else:
            multipliers[anchor]=r['each']
            for k in roots[1:]:components[anchor]+=components[k];absorbed[k]=anchor
    rows=[];member_rows={}
    for rid,members in sorted(components.items()):
        if rid in absorbed:continue
        members=sorted(members);obs=[observations[i] for i in members];o=obs[0]
        rowissues=set().union(*(issues[i] for i in members));keys=sorted({fingerprint(x['source']) for x in obs})
        in_scope=any(k in selected for k in keys);requested=in_scope and o['work_status'] in work
        excluded=all(x['disposition']=='exclude' for x in obs)
        if any(x['disposition']=='unresolved' or x['identity']=='unresolved' for x in obs):rowissues.add('physical_identity_unresolved')
        if len({x['disposition'] for x in obs})>1:rowissues.add('admission_conflict')
        if o['family'].casefold().replace(' ','_') in {'air_device','diffuser','grille','register','air_outlet','linear_diffuser','mechanical_louver'}:rowissues.add('category_overlap')
        if set(keys)&unavailable:rowissues.add('source_stale')
        for x in obs:
            if x['kind']=='assembly' and x['work_status']!='spare':
                valid=any(evidence[e]['kind']=='graphic' and evidence[e]['source']==x['source'] and contexts[fingerprint(x['source'])]['role'] in PHYSICAL_ROLES for e in x['evidence_ids'])
                if not valid:rowissues.add('physical_graphic_evidence_missing')
            elif not support(x['evidence_ids'],{'graphic','requirement'}):rowissues.add('component_evidence_missing')
            if any(fingerprint(evidence[e]['source']) in unavailable for e in x['evidence_ids']):rowissues.add('source_stale')
        hard=rowissues-{'tag_conflict'}-CHANNEL_ONLY
        count=0 if excluded else None if hard else multipliers.get(rid,1)
        top=o['kind']=='assembly' and o['work_status']!='spare' and o['parent_id'] is None
        if o['work_status']=='unknown' and not excluded:rowissues.add('work_status_unresolved')
        attrs=copy.deepcopy(o['attributes'])
        for field in request['scope']['required_attributes']:
            if not all(x['attributes'].get(field) for x in obs):rowissues.add('required_attribute_missing:'+field)
            elif len({x['attributes'][field] for x in obs})>1:rowissues.add('attribute_conflict:'+field)
        procurement=None if count is None or o['procurement']=='unknown' else count if o['procurement']=='separate' else 0
        if any(r['kind']=='package' and set(r['member_ids'])&set(members) and (not relation_valid[r['id']] or r['each'] is None) for r in relations.values()):
            procurement=None;rowissues.add('procurement_relationship_unresolved')
        if o['procurement']=='included' and o['parent_id'] is None and not any(r['kind']=='package' and related(o,r) and relation_valid[r['id']] for r in relations.values()):procurement=None;rowissues.add('procurement_parent_unresolved')
        if o['procurement']=='included' and o['parent_id'] is not None and (not support(o['evidence_ids'],{'relationship','requirement'}) or observations[o['parent_id']]['disposition']!='include'):
            procurement=None;rowissues.add('procurement_parent_unresolved')
        install=None if count is None or o['installation']=='unknown' else count if o['installation']=='field' else 0
        relocation=any(r['kind']=='relocation' and relation_valid[r['id']] and set(r['member_ids'])&set(members) for r in relations.values())
        if o['work_status']=='relocation' and not relocation and not excluded:rowissues.add('relocation_unresolved');count=None;procurement=None;install=None
        if o['work_status']=='relocation' and relocation:
            if o['procurement']!='none':rowissues.add('relocation_procurement_conflict');procurement=None
            else:procurement=0
        if excluded:rowissues=set();procurement=0;install=0
        row=dict(row_id=rid,member_ids=members,family=o['family'],tag=o['tag'],kind=o['kind'],work_status=o['work_status'],system_id=o['system_id'],attributes=attrs,
                 physical_each=count if top else 0,requested=requested,procurement_each=procurement,installation_each=install,
                 remove_each=count if o['work_status'] in {'demolition','relocation'} else 0,reinstall_each=count if o['work_status']=='relocation' else 0,
                 component_each=count if not top else 0,issues=sorted(rowissues),source_keys=keys)
        dependent_relations=[r for r in relations.values() if set(r['member_ids'])&set(members)]
        dependent_evidence={e for x in obs for e in x['evidence_ids']}|{e for r in dependent_relations for e in r['evidence_ids']}
        dependent_keys=set(keys)|{fingerprint(evidence[e]['source']) for e in dependent_evidence}
        dependencies=dict(observations=obs,evidence=[evidence[i] for i in sorted(dependent_evidence)],
            relations=dependent_relations,sources=[contexts[k] for k in sorted(dependent_keys)],parent=observations.get(o['parent_id']),
            unavailable=sorted(dependent_keys&unavailable),required_attributes=request['scope']['required_attributes'],binding=request['binding'])
        row['dependency_sha256']=fingerprint(dependencies)
        rows.append(row)
        for i in members:member_rows[i]=row
    declarations=[]
    for s in schedules.values():
        relevant=[r for r in rows if r['requested'] and (bool(set(r['member_ids'])&set(s['member_ids'])) if s['member_ids'] else (r['family']==s['family'] and r['work_status']==s['work_status']))]
        valid=support(s['evidence_ids'],{'schedule'});sis=[]
        if not valid:sis.append('schedule_evidence_unresolved')
        if any(r['family']!=s['family'] or r['work_status']!=s['work_status'] for r in relevant):sis.append('schedule_membership_conflict')
        observed=sum(r['physical_each'] or 0 for r in relevant)
        if observed!=s['declared_each'] or any(r['physical_each'] is None for r in relevant):sis.append('schedule_quantity_mismatch')
        for r in relevant:r['issues']=sorted(set(r['issues'])|set(sis))
        declarations.append(dict(id=s['id'],family=s['family'],work_status=s['work_status'],declared_each=s['declared_each'],observed_each=observed,state='unresolved' if sis else 'matched',evidence_ids=copy.deepcopy(s['evidence_ids']),
            outstanding_each=max(s['declared_each']-observed,0),row_ids=[r['row_id'] for r in relevant],issues=sis))
    coverage=request['coverage'];coverage_sources={fingerprint(evidence[e]['source']) for e in coverage['evidence_ids'] if evidence[e]['kind']=='coverage'}
    covered=selected<=coverage_sources and bool(selected) and coverage['state']=='complete' and not coverage['unresolved_requirements']
    groups={}
    for r in rows:
        if not r['requested']:continue
        grouping=set(request['scope']['required_attributes'])|{k for k in ('phase','alternate') if k in r['attributes']}
        key=dict(family=r['family'],work_status=r['work_status'],system_id=r['system_id'],attributes={k:r['attributes'].get(k) for k in sorted(grouping)})
        gid=fingerprint(key);g=groups.setdefault(gid,dict(group_id=gid,key=key,row_ids=[],known_subtotal_each=0,total_each=None,complete=True,issues=[]))
        g['row_ids'].append(r['row_id']);g['known_subtotal_each']+=r['physical_each'] or 0
        blocking=set(r['issues'])-CHANNEL_ONLY
        g['issues']=sorted(set(g['issues'])|blocking)
        g['complete']=g['complete'] and r['physical_each'] is not None and not blocking and coverage['state']=='complete' and not coverage['unresolved_requirements'] and set(r['source_keys'])<=coverage_sources
    for g in groups.values():g['total_each']=g['known_subtotal_each'] if g['complete'] else None
    complete=covered and not (unavailable&selected) and all(g['complete'] for g in groups.values()) and not any(d['issues'] for d in declarations)
    known=sum(r['physical_each'] or 0 for r in rows if r['requested'])
    allissues=sorted({i for r in rows if r['requested'] for i in r['issues']}|{i for d in declarations for i in d['issues']}|({'coverage_incomplete'} if not covered else set()))
    alternates={r['attributes'].get('alternate') for r in rows if r['requested'] and r['physical_each'] not in (0,None)}-{None}
    if len(alternates)>1:
        known=None;complete=False;allissues=sorted(set(allissues)|{'alternate_selection_unresolved'})
    systems=[]
    for r in relations.values():
        if r['kind']!='system' or not relation_valid[r['id']]:continue
        members=list({member_rows[i]['row_id']:member_rows[i] for i in r['member_ids']}.values())
        if not any(v['requested'] for v in members):continue
        total=sum(v['physical_each'] or 0 for v in members) if all(v['requested'] and v['physical_each'] is not None for v in members) else None
        systems.append(dict(id=r['id'],member_ids=sorted(r['member_ids']),physical_each=total))
    packages=[r for r in relations.values() if r['kind']=='package' and any(member_rows[i]['requested'] for i in r['member_ids'])]
    package_values=[];physical_package_members=set()
    for r in packages:
        physical_members={member_rows[i]['row_id'] for i in r['member_ids']}
        if physical_package_members&physical_members:fail('Procurement packages overlap the same established physical assembly.')
        physical_package_members.update(physical_members)
        excluded=[observations[i]['disposition']=='exclude' for i in r['member_ids']]
        if all(excluded):package_values.append(0);continue
        if any(excluded) or not relation_valid[r['id']] or r['each'] is None or any(not member_rows[i]['requested'] or member_rows[i]['physical_each'] is None or observations[i]['procurement'] not in {'included','separate'} or observations[i]['work_status'] not in {'new','spare'} for i in r['member_ids']):
            package_values.append(None)
        else:package_values.append(r['each'])
    package_count=sum(package_values) if packages and all(v is not None for v in package_values) else None
    return dict(schema='equipment-calculation-result-1',version=VERSION,binding=copy.deepcopy(request['binding']),request_sha256=fingerprint(request),
        rows=rows,groups=list(groups.values()),declarations=declarations,issues=allissues,known_subtotal_each=known,total_each=known if complete else None,complete=complete,
        systems=systems,procurement_packages=package_count)
