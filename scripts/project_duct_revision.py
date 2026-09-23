"""Explicit source-intent review; revisions never infer physical precedence."""
import copy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import uuid

_spec = importlib.util.spec_from_file_location('revision_project', Path(__file__).with_name('project_duct_takeoff.py'))
duct = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(duct)


def _seal(record):
    record['fingerprint'] = duct._digest(record)
    return record


def _verify_seal(record):
    if record.get('fingerprint') != duct._digest({k:v for k,v in record.items() if k != 'fingerprint'}):
        duct._fail('duct_revision_changed', 'The retained source review changed.')


def _decision(data, identifier):
    records = [r for r in data['duct_revision_decisions'] if r['revision_id'] == identifier]
    if len(records) > 1:
        duct._fail('duct_revision_changed', 'A source review has conflicting decisions.')
    if records:
        _verify_seal(records[0])
        return records[0]
    return None


def _verify(workspace, record):
    _verify_seal(record)
    producer = duct._producer(workspace, record['producer_record_id'])
    if duct._digest(producer) != record['producer_sha256'] or producer != record['producer']:
        duct._fail('duct_revision_changed', 'The retained source producer changed.')
    return producer


def consumed(data, producer):
    references = [r for g in data['duct_generations'] for r in g['source_records']]
    references += [{'id':r['producer_record_id'], 'sha256':r['producer_sha256']} for r in data['duct_revisions']]
    matches = [r for r in references if r['id'] == producer['id']]
    if any(r['sha256'] != duct._digest(producer) for r in matches):
        duct._fail('duct_revision_changed', 'A previously retained producer identity has changed bytes.')
    return bool(matches)


def _source(geometry):
    return dict({k:geometry[k] for k in ('revision_id','index','sheet_id')}, geometry_fingerprint=geometry['fingerprint'])


def _sources(observations):
    return list({duct._digest(o['source']):copy.deepcopy(o['source']) for o in observations}.values())


def _base_dependency(workspace, data, sheets, generation):
    fresh, problems = duct._fresh(workspace, data, sheets, generation)
    return duct._digest({'dependencies':fresh['dependencies'], 'problems':problems,
                         'implementation':duct._implementation(), 'rule':duct._rule_binding()})


def _incoming(workspace, data, sheets, producer):
    core, bindings = duct._core(), {}
    sources = producer['current_sources']
    if not isinstance(sources, dict) or not 1 <= len(sources) <= 100:
        duct._fail('duct_revision_source', 'Review a bounded producer with original source context.')
    core._current_context(sources)
    observations = [core.validate_observation(o) for o in core._array(producer['observations'], 2000)]
    for key, context in sources.items():
        source = _source(context['geometry'])
        if source['sheet_id'] != key:
            duct._fail('duct_revision_source', 'Each source needs its exact sheet identity.')
        unused, bindings[key] = duct._source_context(data, sheets, source)
    for observation in observations:
        context = sources.get(observation['source']['sheet_id'])
        if context is None or observation['source'] != _source(context['geometry']):
            duct._fail('duct_revision_source', 'Every proposal must bind its own original page.')
    rule = duct._rule_binding()
    admissions = [dict(duct._admission(o, rule), id='revision-validation-' + o['id']) for o in observations]
    calculated = core.calculate(observations=observations, admissions=admissions, correspondences=[],
        coverage=duct._coverage(observations, sources), current_sources=sources,
        scale_facts=data['calibrations'], scale_decisions=data['scale_decisions'], rule_binding=rule)
    return observations, bindings, duct._digest({'dependencies':calculated['dependencies'], 'bindings':bindings,
                                                'implementation':duct._implementation(), 'rule':rule})


def _relations(generation):
    return [r for r in {r['id']:r for r in generation['correspondences']}.values() if r['state'] != 'rejected']


def _families(generation):
    active = {o['id'] for o in generation['observations']}
    parents = {r['parent']['id']:r for r in generation.get('planar_splits', [])}
    def leaves(key, seen):
        if key in seen:
            duct._fail('duct_revision_family', 'A retained split has cyclic membership.')
        if key in active:
            return {key}
        if key not in parents:
            duct._fail('duct_revision_family', 'A retained split member is missing.')
        return set().union(*(leaves(m['observation_id'], seen | {key}) for m in parents[key]['members']))
    return [{'id':r['id'], 'parent_id':r['parent']['id'], 'parent_fingerprint':r['parent_fingerprint'],
        'record_fingerprint':duct._digest(r), 'member_ids':[m['observation_id'] for m in r['members']],
        'descendant_ids':sorted(leaves(r['parent']['id'], set()))} for r in generation.get('planar_splits', [])]


def stage(workspace, data, sheets, values, actor, reason):
    duct._exact(values, {'generation_id','producer_record_id'})
    generation = duct._generation(data, values['generation_id'])
    producer = duct._producer(workspace, values['producer_record_id'])
    consumed(data, producer)  # Enforce historical identity; explicit review is allowed.
    observations, bindings, incoming_dependency = _incoming(workspace, data, sheets, producer)
    dependency, producer_hash = _base_dependency(workspace, data, sheets, generation), duct._digest(producer)
    for record in reversed(data['duct_revisions']):
        if (record['producer_record_id'] == producer['id'] and record['producer_sha256'] == producer_hash
                and record['base_generation_fingerprint'] == duct._digest(generation)
                and record['base_dependency_fingerprint'] == dependency
                and record['incoming_dependency_fingerprint'] == incoming_dependency and _decision(data, record['id']) is None):
            _verify(workspace, record)
            return record
    if len(data['duct_revisions']) >= 1000:
        duct._fail('duct_revision_limit', 'The retained source review collection is full.')
    record = _seal({'schema':'duct-revision-1', 'id':'duct_revision_' + uuid.uuid4().hex,
        'base_generation_id':generation['id'], 'base_generation_fingerprint':duct._digest(generation),
        'base_dependency_fingerprint':dependency, 'producer_record_id':producer['id'], 'producer_sha256':producer_hash,
        'producer':copy.deepcopy(producer), 'source_bindings':bindings, 'incoming_dependency_fingerprint':incoming_dependency,
        'incoming':[{'id':'incoming-' + str(i),'observation':copy.deepcopy(o),'fingerprint':duct._observation_fingerprint(o)} for i,o in enumerate(observations)],
        'current_targets':[{'id':o['id'],'observation_fingerprint':duct._observation_fingerprint(o),
            'source':copy.deepcopy(o['source']),'observation':copy.deepcopy(o),
            'required_current_ids':duct._closure([o['id']], generation, generation)} for o in generation['observations']],
        'arc_check_index':duct._module('project_duct_revision_checks').index(generation),
        'retained_family_index':_families(generation),
        'correspondence_index':[{'id':r['id'],'fingerprint':duct._digest(r),'record':copy.deepcopy(r),
            'member_ids':list(r['members'])} for r in _relations(generation)],
        'actor':actor,'reason':reason,'at':datetime.now(timezone.utc).isoformat()})
    data['duct_revisions'].append(record)
    for old in data['duct_revisions'][:-1]:
        if old['producer_record_id'] == producer['id'] and _decision(data, old['id']) is None:
            _verify(workspace, old)
            data['duct_revision_decisions'].append(_seal({'schema':'duct-revision-decision-1',
                'id':'duct_revision_decision_' + uuid.uuid4().hex, 'revision_id':old['id'],
                'revision_fingerprint':old['fingerprint'], 'request':{'kind':'superseded','groups':[],'actor':actor,'reason':reason},
                'groups':[], 'base_generation_id':old['base_generation_id'], 'replacement_generation_id':None,
                'successor_revision_id':record['id'],'successor_revision_fingerprint':record['fingerprint'],
                'at':datetime.now(timezone.utc).isoformat()}))
    return record


def _current(workspace, data, sheets, record):
    generation = duct._generation(data, record['base_generation_id'])
    if (duct._digest(generation) != record['base_generation_fingerprint'] or
            _base_dependency(workspace, data, sheets, generation) != record['base_dependency_fingerprint'] or
            _incoming(workspace, data, sheets, record['producer'])[2] != record['incoming_dependency_fingerprint']):
        duct._fail('duct_revision_stale', 'Source or calculation dependencies changed. Explicitly restage this source review.')
    return generation


def _get(data, values):
    record = next((r for r in data['duct_revisions'] if r['id'] == values['revision_id']), None)
    if record is None or record['fingerprint'] != values['revision_fingerprint']:
        duct._fail('duct_revision_changed', 'Select the unchanged source review.')
    _verify_seal(record)
    return record


def project_sources(generation):
    """Apply only explicit retirement intent; missing evidence is still a use."""
    if not generation.get('revision_retirement_sources'):
        return generation
    result = copy.deepcopy(generation)
    known = duct._known_evidence(generation)
    used = duct._current_evidence_ids(generation)
    required = {o['source']['sheet_id'] for o in generation['observations']}
    required.update(r['parent']['source']['sheet_id'] for r in generation.get('planar_splits', []))
    required.update(known[eid]['source']['sheet_id'] for eid in used if eid in known)
    # Unresolvable current ownership cannot justify source retirement.
    if any(eid not in known for eid in used):
        return result
    retired = {s['sheet_id'] for s in generation['revision_retirement_sources']
        if s['sheet_id'] in generation['current_sources'] and
        s == _source(generation['current_sources'][s['sheet_id']]['geometry'])} - required
    references = []
    for ref in result['source_records']:
        ref['sheet_ids'] = [key for key in ref['sheet_ids'] if key not in retired]
        if 'bindings' in ref:
            ref['bindings'] = {key:v for key,v in ref['bindings'].items() if key in ref['sheet_ids']}
        if ref['sheet_ids']:
            references.append(ref)
    result['source_records'] = references
    for field in ('current_sources','source_bindings'):
        result[field] = {key:v for key,v in result[field].items() if key not in retired}
    return result


def _old_evidence(workspace, data, sheets, generation, identifier):
    found = []
    for ref in generation.get('user_evidence_refs', []):
        record = ref['record']
        if record['id'] == identifier:
            if duct._digest(record) != ref['sha256'] or record not in data['duct_evidence']:
                duct._fail('duct_revision_evidence', 'The retained decision evidence changed.')
            duct._capture_record(workspace, data, sheets, record, current=False)
            found.append(record['evidence'])
    for ref in generation['source_records']:
        if any(e['id']==identifier for key in ref['sheet_ids'] for e in generation['current_sources'].get(key,{}).get('evidence',[])):
            producer = duct._producer(workspace, ref['id'])
            if duct._digest(producer) != ref['sha256']:
                duct._fail('duct_revision_evidence', 'The retained old producer changed.')
            found.extend(e for key in ref['sheet_ids'] for e in producer['current_sources'][key]['evidence'] if e['id']==identifier)
    if not found or any(e != found[0] for e in found):
        duct._fail('duct_revision_evidence', 'Choose verifiable evidence for each old source.')
    return found[0]


def _groups(record, generation, groups):
    core = duct._core()
    core._array(groups, 2000, 1)
    incoming = {r['id']:r for r in record['incoming']}
    current = {o['id']:o for o in generation['observations']}
    seen_in, seen_old = set(), set()
    for group in groups:
        duct._exact(group, {'incoming_ids','current_ids','disposition','evidence_ids','reason'} |
                    ({'check_dispositions'} if 'check_dispositions' in group else set()))
        for key in ('incoming_ids','current_ids','evidence_ids'):
            core._ids(group[key], 2000, 1 if key=='evidence_ids' else 0)
        core._plain(group['reason'],500)
        new, old = set(group['incoming_ids']), set(group['current_ids'])
        mode = group['disposition']
        if (not new <= set(incoming) or not old <= set(current) or new & seen_in or old & seen_old or
                mode not in ('add_new','replace_current','retain_current','exclude') or
                (mode in ('add_new','exclude') and old) or
                (mode in ('replace_current','retain_current') and (not old or not new)) or
                (not new and (incoming or mode!='exclude' or len(groups)!=1))):
            duct._fail('duct_revision_partition', 'Explicitly account for every proposal and non-overlapping current scope.')
        if mode == 'replace_current' and set(duct._closure(old,generation,generation)) != old:
            duct._fail('duct_revision_family', 'Select the complete split and correspondence family for replacement.')
        duct._module('project_duct_revision_checks').validate_group(record,generation,group)
        seen_in.update(new); seen_old.update(old)
    if seen_in != set(incoming):
        duct._fail('duct_revision_partition', 'Every incoming proposal needs one explicit disposition.')
    return incoming, current


def review(workspace, data, sheets, values, actor, reason):
    duct._module('project_duct_revision_checks').verify_lineage(data)
    duct._exact(values, {'revision_id','revision_fingerprint','groups'})
    record = _get(data, values)
    request = {'kind':'applied','groups':copy.deepcopy(values['groups']),'actor':actor,'reason':reason}
    decision = _decision(data, record['id'])
    if decision:
        if decision['request'] != request:
            duct._fail('duct_revision_decided', 'This source review already has a different decision.')
        return decision
    producer = _verify(workspace, record)
    generation = _current(workspace, data, sheets, record)
    incoming, current = _groups(record, generation, values['groups'])
    candidate, lineage, changed, retire = copy.deepcopy(generation), [], set(), set()
    checks = duct._module('project_duct_revision_checks')
    decision_id = 'duct_revision_decision_' + uuid.uuid4().hex
    check_changes, check_jobs, link_events = [], [], []
    new_evidence = {e['id']:e for c in producer['current_sources'].values() for e in c['evidence']}
    new_sources = []
    old_known = duct._known_evidence(generation)
    for group in values['groups']:
        old_rows = [current[key] for key in group['current_ids']]
        proposals = [copy.deepcopy(incoming[key]['observation']) for key in group['incoming_ids']]
        old_sources, incoming_sources = _sources(old_rows), _sources(proposals)
        if not proposals:
            incoming_sources = [_source(c['geometry']) for c in producer['current_sources'].values()]
        evidence = []
        for key in group['evidence_ids']:
            item = new_evidence.get(key)
            if item is None and key in old_known:
                item = _old_evidence(workspace,data,sheets,generation,key)
            if item is None:
                capture = next((r for r in data['duct_evidence'] if r['id']==key), None)
                if capture:
                    item = duct._capture_record(workspace,data,sheets,capture,
                        current=any(capture['evidence']['source']==s for s in incoming_sources))['evidence']
                else:
                    item = _old_evidence(workspace,data,sheets,generation,key)
            if item['source'] not in old_sources + incoming_sources:
                duct._fail('duct_revision_evidence', 'Decision evidence must belong to an explicitly selected old or new source.')
            evidence.append(copy.deepcopy(item))
        if any(not any(e['source']==source for e in evidence) for source in old_sources + incoming_sources):
            duct._fail('duct_revision_evidence', 'Cite evidence on every selected old and new source.')
        retired_splits, retired_relations, outputs = [], [], []
        if group['disposition'] == 'replace_current':
            retired = set(group['current_ids'])
            families = [f for f in record['retained_family_index'] if set(f['descendant_ids']) & retired]
            by_id = {o['id']:o for o in generation['observations']}
            by_id.update({s['parent']['id']:s['parent'] for s in generation.get('planar_splits',[])})
            for family in families:
                split = next(s for s in generation['planar_splits'] if s['id']==family['id'])
                if (split['parent_fingerprint'] != duct._observation_fingerprint(split['parent']) or
                    any(m['observation_id'] not in by_id or m['observation_fingerprint'] != duct._observation_fingerprint(by_id[m['observation_id']]) for m in split['members'])):
                    duct._fail('duct_revision_family', 'Resolve the exact retained split membership before replacing its scope.')
                parent = split['parent']
                members = [by_id[m['observation_id']] for m in split['members']]
                context = generation['current_sources'].get(parent['source']['sheet_id'])
                if context is None:
                    duct._fail('duct_revision_family', 'Retained split members need their exact source geometry.')
                try:
                    duct._core().prove_split_partition(parent, members, context['geometry'], split['cuts'],
                        arc=split.get('schema') == 'duct-arc-split-1')
                except ValueError:
                    duct._fail('duct_revision_family', 'Retained children must exactly partition their parent source interval.')
                retired.add(family['parent_id'])
                retired_splits.append(copy.deepcopy(split))
            candidate['observations'] = [o for o in candidate['observations'] if o['id'] not in retired]
            candidate['admissions'] = [a for a in candidate['admissions'] if a['observation_id'] not in retired]
            candidate['planar_splits'] = [s for s in candidate.get('planar_splits',[]) if s['parent']['id'] not in retired]
            for relation in _relations(generation):
                if set(relation['members']) & retired:
                    if not set(relation['members']) <= retired or any(duct._observation_fingerprint(by_id[key]) != fp for key,fp in relation['members'].items()):
                        duct._fail('duct_revision_family', 'A correspondence must be replaced as one complete current family.')
                    retired_relations.append(copy.deepcopy(relation))
            relation_ids = {r['id'] for r in retired_relations}
            candidate['correspondences'] = [r for r in candidate['correspondences'] if r['id'] not in relation_ids]
            changed.update(retired)
            for source in old_sources:
                if source not in candidate.setdefault('revision_retirement_sources',[]):
                    candidate['revision_retirement_sources'].append(copy.deepcopy(source))
                retire.add(source['sheet_id'])
        if group['disposition'] in ('replace_current','add_new'):
            for key, proposal in zip(group['incoming_ids'],proposals):
                sheet_id = proposal['source']['sheet_id']
                duct._merge_source(candidate['current_sources'],producer['current_sources'][sheet_id])
                if sheet_id not in new_sources:
                    new_sources.append(sheet_id)
                candidate['source_bindings'][sheet_id] = copy.deepcopy(record['source_bindings'][sheet_id])
                proposal['id'] = 'duct_revision_portion_' + uuid.uuid4().hex
                # Only the incoming observation's own evidence can support its admission.
                local_ids = [eid for eid in proposal['evidence_ids'] if new_evidence.get(eid, {}).get('source')==proposal['source']]
                candidate['observations'].append(proposal)
                candidate['admissions'].append(duct._admission(proposal,duct._rule_binding(),method='user_review',evidence_ids=local_ids))
                changed.add(proposal['id'])
                outputs.append({'incoming_id':key,'new_observation_id':proposal['id'],'new_fingerprint':duct._observation_fingerprint(proposal)})
        lineage.append(dict(copy.deepcopy(group),old_sources=old_sources,new_sources=incoming_sources,
            old_observation_fingerprints={o['id']:duct._observation_fingerprint(o) for o in old_rows},
            incoming_fingerprints={key:incoming[key]['fingerprint'] for key in group['incoming_ids']}, outputs=outputs,
            retired_split_records=retired_splits,retired_correspondence_records=retired_relations,decision_evidence=evidence))
        if group.get('check_dispositions'):
            check_jobs.append((group,outputs,lineage[-1]))
    if new_sources:
        candidate['source_records'].append({'id':producer['id'],'sha256':record['producer_sha256'],
            'sheet_ids':sorted(new_sources),'bindings':{key:copy.deepcopy(record['source_bindings'][key]) for key in new_sources}})
    # All new sources and server-allocated identities must exist before check compilation.
    for group,outputs,lineage_group in check_jobs:
        entries={e['id']:e for e in checks.affected(generation,group)}
        output_ids={o['incoming_id']:o['new_observation_id'] for o in outputs}
        lineage_group['arc_check_transitions']=[]
        for decision in group['check_dispositions']:
            old=entries[decision['id']]['record']
            proposal=copy.deepcopy(decision)
            if proposal['scope'] is not None:
                scope=proposal['scope']
                if not isinstance(scope,dict) or scope.get('incoming_id') not in proposal['incoming_ids']:
                    duct._fail('duct_revision_check_scope','Choose the explicitly selected incoming root.')
                scope['observation_id']=output_ids[scope.pop('incoming_id')]
            binding={'revision_id':record['id'],'revision_fingerprint':record['fingerprint'],
                'base_generation_id':generation['id'],'previous_check_fingerprint':entries[old['id']]['fingerprint'],'decision_id':decision_id}
            new,support,events=checks.compile_check(workspace,data,sheets,generation,candidate,old,proposal,
                [output_ids[key] for key in decision['incoming_ids']],binding,actor,reason)
            candidate['arc_obligations']=[c for c in candidate.get('arc_obligations',[]) if c['id']!=old['id']]+([new] if new else [])
            change={'before':copy.deepcopy(old),'after':copy.deepcopy(new),'decision_evidence':support}
            check_changes.append(change);lineage_group['arc_check_transitions'].append(change);link_events.extend(events)
    candidate['coverage'] = duct._coverage(candidate['observations'],candidate['current_sources'], ['Revision decisions need renewed source coverage review.'])
    candidate = project_sources(candidate)
    for group in lineage:
        for output in group['outputs']:
            proposal = next(o for o in candidate['observations'] if o['id']==output['new_observation_id'])
            duct._register_evidence(workspace,data,sheets,candidate,proposal,
                next(a['evidence_ids'] for a in candidate['admissions'] if a['observation_id']==proposal['id']))
    token = checks.transition(generation,candidate,check_changes,kind='revision',revision_id=record['id'],
        revision_fingerprint=record['fingerprint'],decision_id=decision_id,groups=copy.deepcopy(values['groups'])) if check_changes else None
    result = duct._retain(workspace,data,sheets,candidate,'source_revision_review',actor,reason,changed,check_transition=token)
    for event in link_events:
        event['replacement_generation_id']=result['id'];data['duct_reading_relationship_events'].append(event)
    selected = {key for r in result['source_records'] for key in r['sheet_ids']}
    decision = _seal({'schema':'duct-revision-decision-1','id':decision_id,
        'revision_id':record['id'],'revision_fingerprint':record['fingerprint'],'request':request,'groups':lineage,
        'base_generation_id':generation['id'],'replacement_generation_id':result['id'],
        'source_projection':{'before_source_records':copy.deepcopy(generation['source_records']),
            'after_source_records':copy.deepcopy(result['source_records']),'retired_sheet_ids':sorted(retire-selected),
            'still_required_old_sources':sorted(retire & selected)},'at':datetime.now(timezone.utc).isoformat()})
    data['duct_revision_decisions'].append(decision)
    checks.verify_lineage(data)
    return result


def reject(workspace,data,sheets,values,actor,reason):
    duct._exact(values, {'revision_id','revision_fingerprint'})
    record = _get(data,values)
    request = {'kind':'rejected','groups':[],'actor':actor,'reason':reason}
    decision = _decision(data,record['id'])
    if decision:
        if decision['request'] != request:
            duct._fail('duct_revision_decided','This source review already has a different decision.')
        return decision
    _verify(workspace,record)
    decision = _seal({'schema':'duct-revision-decision-1','id':'duct_revision_decision_' + uuid.uuid4().hex,
        'revision_id':record['id'],'revision_fingerprint':record['fingerprint'],'request':request,'groups':[],
        'base_generation_id':record['base_generation_id'],'replacement_generation_id':None,'at':datetime.now(timezone.utc).isoformat()})
    data['duct_revision_decisions'].append(decision)
    return decision


def view(workspace,data,sheets):
    output=[]
    for record in data['duct_revisions']:
        issues=[]
        decision=_decision(data,record['id'])
        try:
            _verify(workspace,record)
            if decision is None:
                _current(workspace,data,sheets,record)
        except (ValueError,OSError,KeyError) as error:
            issues.append(getattr(error,'message','The saved revision source is unavailable or stale.'))
        evidence = duct._known_evidence(next(g for g in data['duct_generations'] if g['id']==record['base_generation_id']))
        evidence.update({e['id']:e for c in record['producer']['current_sources'].values() for e in c['evidence']})
        # Persisted source maps may reopen in a different insertion order. Only
        # this derived list is ordered; sealed stages and source bytes are kept.
        ordered_evidence = sorted(evidence.values(), key=lambda e: (
            e['source']['revision_id'], e['source']['index'], e['source']['sheet_id'],
            e['source']['geometry_fingerprint'], e['id']))
        output.append({'id':record['id'],'fingerprint':record['fingerprint'],
            'state':decision['request']['kind'] if decision else 'pending','validity':'stale' if issues else 'current','issues':issues,
            **{k:copy.deepcopy(record[k]) for k in ('base_generation_id','base_dependency_fingerprint','producer_record_id','producer_sha256',
                'incoming','current_targets','source_bindings','incoming_dependency_fingerprint')},
            'arc_check_index':copy.deepcopy(record.get('arc_check_index',[])),
            'families':copy.deepcopy(record['retained_family_index']),'correspondences':copy.deepcopy(record['correspondence_index']),
            'evidence':copy.deepcopy(ordered_evidence),'decision_id':decision['id'] if decision else None,
            'replacement_generation_id':decision['replacement_generation_id'] if decision else None,
            'successor_revision_id':decision.get('successor_revision_id') if decision else None})
    return output


def verify_export(workspace,data):
    records={}
    for record in data['duct_revisions']:
        producer=_verify(workspace,record)
        records[producer['id']]=producer
        _decision(data,record['id'])
    return list(records.values())
