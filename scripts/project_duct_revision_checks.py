"""Source-bound retained check transitions and sealed introduction/event lineage."""
import copy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import uuid

_spec = importlib.util.spec_from_file_location('revision_checks_project', Path(__file__).with_name('project_duct_takeoff.py'))
duct = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(duct)
SCHEMA = 'duct-arc-revision-check-1'


def nodes(generation):
    return {o['id']: o for o in generation['observations'] + [r['parent'] for r in generation.get('planar_splits', [])]}


def dependent_ids(generation, check):
    targets = check.get('affected_observation_ids', [check['family_root_id']])
    return duct._closure(targets, generation, generation)


def index(generation):
    return [{'id': c['id'], 'fingerprint': duct._digest(c), 'record': copy.deepcopy(c),
        'family_root_id': c['family_root_id'], 'current_ids': dependent_ids(generation,c),
        'required_current_ids': dependent_ids(generation,c),
        'current_source': copy.deepcopy(c.get('current_source', c['origin']['observation']['source']))}
        for c in generation.get('arc_obligations', [])]


def affected(generation, group):
    selected = set(group['current_ids'])
    return [entry for entry in index(generation) if selected.intersection(entry['current_ids'])]


def validate_group(stage, generation, group):
    entries = affected(generation, group) if group['disposition'] == 'replace_current' else []
    decisions = duct._core()._array(group.get('check_dispositions', []), 2000)
    if entries:
        if stage.get('arc_check_index') != index(generation):
            duct._fail('duct_revision_arc_checks', 'Restage this revision with exact saved check pins.')
        if any(not set(entry['required_current_ids']) <= set(group['current_ids']) for entry in entries):
            duct._fail('duct_revision_family', 'Replace every current dependent of the retained check together.')
    if (len(decisions) != len(entries) or any(not isinstance(d,dict) for d in decisions) or
            {d.get('id') for d in decisions} != {e['id'] for e in entries}):
        duct._fail('duct_revision_arc_checks', 'Explicitly decide every affected saved check exactly once.')
    by_id = {e['id']:e for e in entries}
    for decision in decisions:
        duct._exact(decision, {'id','fingerprint','state','incoming_ids','scope','reading','evidence_ids','relationships','reason'})
        if decision['fingerprint'] != by_id[decision['id']]['fingerprint']:
            duct._fail('duct_revision_check_stale', 'The staged check fingerprint changed.')
        duct._core()._ids(decision['incoming_ids'],minimum=1)
        if not set(decision['incoming_ids']) <= set(group['incoming_ids']):
            duct._fail('duct_revision_check_target', 'Select only incoming targets in this replacement group.')
        if decision['state'] == 'not_applicable' and set(decision['incoming_ids']) != set(group['incoming_ids']):
            duct._fail('duct_revision_check_target', 'Retirement must cover the complete replacement group.')
    return by_id


def source_set(generation, check):
    current = nodes(generation)
    result = []
    for key in dependent_ids(generation,check):
        if current[key]['source'] not in result:
            result.append(copy.deepcopy(current[key]['source']))
    return result


def decision_evidence(workspace,data,sheets,generation,candidate,identifiers,sources):
    core = duct._core()
    core._ids(identifiers,minimum=1)
    known = duct._known_evidence(candidate)
    revision = duct._module('project_duct_revision')
    result=[]
    for key in identifiers:
        if key in duct._known_evidence(generation):
            item=revision._old_evidence(workspace,data,sheets,generation,key)
        elif key in known:
            item=known[key]
            duct._source_context(data,sheets,item['source'])
        else:
            item=duct._attach_evidence(workspace,data,sheets,candidate,{key})[key]
        if item['source'] not in sources:
            duct._fail('duct_revision_check_evidence','Check decisions cite only prior-current and selected target pages.')
        result.append(copy.deepcopy(item))
    if any(not any(e['source']==source for e in result) for source in sources):
        duct._fail('duct_revision_check_evidence','Cite every prior-current and selected target page for this check decision.')
    return result


def compile_check(workspace,data,sheets,generation,candidate,old,proposal,targets,binding,actor,reason):
    core=duct._core()
    core._enum(proposal['state'], {'active','unresolved','not_applicable'})
    core._plain(proposal['reason'],2000)
    core._array(proposal['relationships'],128)
    sources=source_set(generation,old)
    selected=[nodes(candidate)[key] for key in targets]
    for target in selected:
        if target['source'] not in sources:sources.append(target['source'])
        duct._source_context(data,sheets,target['source'])
    evidence=decision_evidence(workspace,data,sheets,generation,candidate,proposal['evidence_ids'],sources)
    if proposal['state'] != 'active':
        if proposal['scope'] is not None or proposal['reading'] is not None or proposal['relationships'] != []:
            duct._fail('duct_revision_check_state','Pending and retired checks have no active scope, reading or relationships.')
        if proposal['state']=='not_applicable':return None,evidence,[]
    current_sources={duct._digest(t['source']):t['source'] for t in selected}
    current_source=copy.deepcopy(next(iter(current_sources.values()))) if len(current_sources)==1 else None
    result={'schema':SCHEMA,'id':old['id'],'family_root_id':None,'origin':copy.deepcopy(old['origin']),
        'state':proposal['state'],'observation':None,
        'retained_observation':copy.deepcopy(old['observation'] or old['retained_observation']),
        'evidence_ids':[e['id'] for e in evidence if e['source'] in current_sources.values()],
        'reason':proposal['reason'],'current_source':current_source,
        'affected_observation_ids':list(targets),'revision_binding':copy.deepcopy(binding)}
    events=[]
    if proposal['state']=='active':
        scope=proposal['scope']
        if len(selected)!=1 or selected[0]['geometry']['kind']!='circular_arc' or not isinstance(scope,dict):
            duct._fail('duct_revision_check_scope','Map an active check to exactly one selected circular root.')
        root=selected[0]
        keys={'kind','observation_id'} | ({'start_point','end_point'} if scope.get('kind')=='interval' else set())
        duct._exact(scope,keys)
        if scope.get('kind') not in ('root','interval') or scope['observation_id']!=root['id']:
            duct._fail('duct_revision_check_scope','The scope must use the explicitly selected circular root.')
        local_proposal=dict(proposal,scope={k:v for k,v in scope.items() if k!='observation_id'},
            evidence_ids=result['evidence_ids'])
        geometry,_=duct._source_context(data,sheets,root['source'])
        local_evidence=duct._attach_evidence(workspace,data,sheets,candidate,set(result['evidence_ids']))
        shape=duct._module('project_duct_arc_repartition')._scope(core,old,root,[],local_proposal,geometry,local_evidence)
        shape.update(dimension_check_ids=[],radius_check_ids=[])
        shape[old['origin']['role']]=[old['origin']['reading_id']]
        observation=copy.deepcopy(root)
        observation.update(id=old['id'],schema='duct-observation-3' if shape['kind']=='circular_arc_span' else 'duct-observation-2',
            geometry=shape,readings=[copy.deepcopy(proposal['reading'])])
        if not isinstance(proposal['reading'],dict) or proposal['reading'].get('id')!=old['origin']['reading_id']:
            duct._fail('duct_revision_check_reading','Retain the original reading identity and semantic role.')
        observation['evidence_ids']=sorted(set(root['evidence_ids']) | set(result['evidence_ids']) | set(proposal['reading'].get('evidence_ids',[])))
        core.validate_observation(observation)
        result.update(family_root_id=root['id'],observation=observation,retained_observation=copy.deepcopy(observation))
        candidate['arc_obligations']=[c for c in candidate.get('arc_obligations',[]) if c['id']!=old['id']]+[result]
        events,_=duct._module('project_duct_reading_links')._review_relationships(duct,core,workspace,data,sheets,generation,
            candidate,observation,{'id':old['id'],'kind':'arc_check'},proposal['relationships'],actor,reason)
        current_evidence=duct._register_evidence(workspace,data,sheets,candidate,observation)
        core._require_graphic_support(observation,None,geometry,current_evidence,span=core.arc_span(shape,geometry))
    duct._module('duct_arc_obligations').validate(core,[result])
    return result,evidence,events


def transition_guard(data,previous,candidate,transition):
    """Authorize only complete server-compiled changes backed by the pending seal."""
    revision=duct._module('project_duct_revision')
    revision._verify_seal(transition)
    if transition['base_generation_id']!=previous['id'] or transition['before_fingerprint']!=duct._digest(previous):
        duct._fail('duct_revision_check_transition','The check transition has a different saved base.')
    if transition['candidate_fingerprint']!=duct._digest(candidate):
        duct._fail('duct_revision_check_transition','Candidate bytes differ from the reviewed transition.')
    before={c['id']:c for c in previous.get('arc_obligations',[])}
    after={c['id']:c for c in candidate.get('arc_obligations',[])}
    covered=set()
    expected_before=before
    if transition['kind']=='family':
        repartition=duct._module('project_duct_arc_repartition')
        family=next((f for f in repartition._families(previous) if f['root_id']==transition['root_id']),None)
        if family is None:duct._fail('duct_revision_check_transition','The correction lost its saved physical family.')
        expected_before={c['id']:{k:v for k,v in c.items() if k!='fingerprint'} for c in repartition._checks(previous,family)}
    for entry in transition['changes']:
        key=entry['before']['id']
        if key in covered or expected_before.get(key)!=entry['before'] or after.get(key)!=entry['after']:
            duct._fail('duct_revision_check_transition','Each old check requires one exact before/after transition.')
        covered.add(key)
        if entry['after'] is not None and entry['after']['origin']!=entry['before']['origin']:
            duct._fail('duct_revision_check_transition','Immutable check origin changed.')
    if (set(after)-set(before))-covered or any(before[k]!=after.get(k) for k in set(before)-covered):
        duct._fail('duct_revision_check_transition','The transition does not cover every changed check.')
    # Unaffected obligated roots still receive the strict ordinary edit guard.
    unaffected=copy.deepcopy(previous)
    unaffected['arc_obligations']=[c for c in before.values() if c['id'] not in covered]
    duct._module('project_duct_arc_repartition').preserve_obligated_roots(unaffected,candidate)
    if transition['kind']=='revision':
        stage=next((s for s in data['duct_revisions'] if s['id']==transition['revision_id']),None)
        if stage is None:duct._fail('duct_revision_check_transition','The transition has no saved stage.')
        revision._verify_seal(stage)
        if stage['arc_check_index']!=index(previous) or stage['fingerprint']!=transition['revision_fingerprint']:
            duct._fail('duct_revision_check_transition','The transition differs from the exact staged checks.')
        expected={e['id'] for group in transition['groups'] if group['disposition']=='replace_current' for e in affected(previous,group)}
        if expected!=covered:duct._fail('duct_revision_check_transition','The transition does not cover the selected replacement scope.')
        for entry in transition['changes']:
            new=entry['after']
            if new is not None and new['revision_binding']!={'revision_id':stage['id'],'revision_fingerprint':stage['fingerprint'],
                'base_generation_id':previous['id'],'previous_check_fingerprint':duct._digest(entry['before']),'decision_id':transition['decision_id']}:
                duct._fail('duct_revision_check_transition','The introduced check has a different decision binding.')
    elif transition['kind']=='family':
        if covered!=set(expected_before):
            duct._fail('duct_revision_check_transition','The family correction omits an exact retained reading.')
    elif transition['kind']=='resolution':
        if len(covered)!=1:duct._fail('duct_revision_check_transition','Resolve one exact current check per event.')
    else:duct._fail('duct_revision_check_transition','Unknown check transition authority.')


def transition(previous,candidate,changes,**context):
    return duct._module('project_duct_revision')._seal(dict(context,base_generation_id=previous['id'],
        before_fingerprint=duct._digest(previous),candidate_fingerprint=duct._digest(candidate),changes=copy.deepcopy(changes)))


def verify_lineage(data):
    revision=duct._module('project_duct_revision')
    generations={g['id']:g for g in data['duct_generations']}
    decisions={d['id']:d for d in data['duct_revision_decisions']}
    stages={s['id']:s for s in data['duct_revisions']}
    events=data.get('duct_revision_check_events',[])
    for event in events:revision._verify_seal(event)
    for generation in data['duct_generations']:
        prior=generations.get(generation.get('previous_generation_id'))
        old={c['id']:c for c in (prior or {}).get('arc_obligations',[])}
        current={c['id']:c for c in generation.get('arc_obligations',[])}
        for check in current.values():
            if check.get('schema')!=SCHEMA:continue
            binding=check['revision_binding']
            decision=decisions.get(binding['decision_id']); stage=stages.get(binding['revision_id'])
            if decision is None or stage is None:duct._fail('duct_revision_check_history','A check lost its sealed introduction.')
            revision._verify_seal(decision);revision._verify_seal(stage)
            entries=[t for g in decision['groups'] for t in g.get('arc_check_transitions',[]) if t['before']['id']==check['id']]
            if (len(entries)!=1 or entries[0]['after'] is None or entries[0]['after']['revision_binding']!=binding or
                entries[0]['before']['origin']!=check['origin'] or duct._digest(entries[0]['before'])!=binding['previous_check_fingerprint'] or
                decision['revision_fingerprint']!=binding['revision_fingerprint'] or stage['fingerprint']!=binding['revision_fingerprint'] or
                decision['base_generation_id']!=binding['base_generation_id']):
                duct._fail('duct_revision_check_history','The check differs from its sealed introduction lineage.')
        changed={key for key in set(old)|set(current) if old.get(key)!=current.get(key) and
            (old.get(key,{}).get('schema')==SCHEMA or current.get(key,{}).get('schema')==SCHEMA)}
        for key in changed:
            matches=[]
            for decision in decisions.values():
                if decision.get('replacement_generation_id')==generation['id']:
                    revision._verify_seal(decision)
                    matches.extend(t for g in decision['groups'] for t in g.get('arc_check_transitions',[]) if t['before']['id']==key)
            for event in events+data.get('duct_arc_repartition_events',[]):
                if event.get('replacement_generation_id')==generation['id']:
                    revision._verify_seal(event)
                    matches.extend(t for t in event.get('arc_check_transitions',[]) if t['before']['id']==key)
            if len(matches)!=1 or matches[0]['before']!=old.get(key) or matches[0]['after']!=current.get(key):
                duct._fail('duct_revision_check_history','A changed current check has no exact sealed before/after event.')


def view(workspace,data,sheets,generation):
    verify_lineage(data)
    result=[]
    core=duct._core()
    available=duct._module('project_duct_reading_links').targets(generation,core)
    for check in generation.get('arc_obligations',[]):
        if check.get('schema')!=SCHEMA:continue
        closure=set(dependent_ids(generation,check))
        targets=[{k:copy.deepcopy(t[k]) for k in ('id','fingerprint','kind','observation')} for t in available
            if t['kind']!='arc_check' and set(duct._closure([t['id']],generation,generation)) <= closure]
        problems=[]
        for source in source_set(generation,check):
            try:duct._source_context(data,sheets,source)
            except ValueError as exc:problems.append(str(exc))
        evidence=sorted(duct._known_evidence(generation).values(),key=lambda e:e['id'])
        result.append({'id':check['id'],'fingerprint':duct._digest(check),'record':copy.deepcopy(check),'state':check['state'],
            'current_ids':sorted(closure),'targets':targets,'evidence':copy.deepcopy(evidence),
            'validity':'stale' if problems else 'current','issues':problems})
    return result


def review(workspace,data,sheets,values,actor,reason):
    duct._exact(values,{'generation_id','check_id','check_fingerprint','disposition'})
    verify_lineage(data)
    request=dict(copy.deepcopy(values),actor=actor,reason=reason)
    for event in data.get('duct_revision_check_events',[]):
        if event['request']==request:
            duct._module('project_duct_revision')._verify_seal(event)
            return event
    verify_lineage(data)
    generation=duct._generation(data,values['generation_id'])
    old=next((c for c in generation.get('arc_obligations',[]) if c['id']==values['check_id'] and c.get('schema')==SCHEMA),None)
    if old is None or duct._digest(old)!=values['check_fingerprint']:
        duct._fail('duct_revision_check_stale','Select the exact current revision check.')
    proposal=values['disposition']
    duct._exact(proposal,{'state','targets','scope','reading','evidence_ids','relationships','reason'})
    core=duct._core();core._array(proposal['targets'],2000,1)
    current=nodes(generation);selected=[]
    for target in proposal['targets']:
        duct._exact(target,{'id','fingerprint'})
        if target['id'] in selected or target['id'] not in current or core.fingerprint(current[target['id']])!=target['fingerprint']:
            duct._fail('duct_revision_check_target','Select each unchanged current target exactly once.')
        selected.append(target['id'])
    previous_scope=set(dependent_ids(generation,old))
    # A target cannot count itself as every old dependent through the check
    # being rewritten. Only actual split/correspondence topology establishes
    # physical coverage of the explicitly selected target set.
    physical=copy.deepcopy(generation)
    physical['arc_obligations']=[]
    selected_scope=set(duct._closure(selected,physical,physical))
    if not selected_scope or not selected_scope<=previous_scope or (proposal['state']!='active' and selected_scope!=previous_scope):
        duct._fail('duct_revision_check_target','Resolve only the existing dependent physical family; retirement covers all dependents.')
    candidate=copy.deepcopy(generation)
    new,evidence,events=compile_check(workspace,data,sheets,generation,candidate,old,proposal,selected,old['revision_binding'],actor,reason)
    candidate['arc_obligations']=[c for c in candidate['arc_obligations'] if c['id']!=old['id']]+([new] if new else [])
    candidate['coverage']=duct._coverage(candidate['observations'],candidate['current_sources'],['Check disposition needs renewed coverage review.'])
    change={'before':copy.deepcopy(old),'after':copy.deepcopy(new),'decision_evidence':evidence}
    token=transition(generation,candidate,[change],kind='resolution')
    result=duct._retain(workspace,data,sheets,candidate,'revision_check_review',actor,reason,
        list(previous_scope|selected_scope),check_transition=token)
    for event in events:
        event['replacement_generation_id']=result['id'];data['duct_reading_relationship_events'].append(event)
    data['duct_revision_check_events'].append(duct._module('project_duct_revision')._seal({
        'id':'duct_revision_check_event_'+uuid.uuid4().hex,'schema':'duct-revision-check-event-1',
        'base_generation_id':generation['id'],'replacement_generation_id':result['id'],
        'request':request,'arc_check_transitions':[change], 'at':datetime.now(timezone.utc).isoformat()}))
    verify_lineage(data)
    return result
