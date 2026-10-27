"""Atomic same-page circular family corrections with exhaustive check decisions."""
import copy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import uuid

_spec = importlib.util.spec_from_file_location('arc_repartition_project', Path(__file__).with_name('project_duct_takeoff.py'))
duct = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(duct)


def verify_events(data):
    for event in data.get('duct_arc_repartition_events', []):
        if (not isinstance(event, dict) or event.get('fingerprint') !=
                duct._digest({key: value for key, value in event.items() if key != 'fingerprint'})):
            duct._fail('duct_arc_history_changed', 'A saved circular correction history differs from its retained fingerprint.')


def _families(generation):
    return duct._module('duct_arc_obligations').family_index(duct._core(), generation['observations'],
        generation.get('planar_splits', []), {k: v['geometry'] for k, v in generation['current_sources'].items()})


def _checks(generation, family):
    result = [copy.deepcopy(r) for r in generation.get('arc_obligations', [])
        if r['family_root_id'] == family['root_id'] or (r.get('schema') == 'duct-arc-revision-check-1' and
            set(duct._module('project_duct_revision_checks').dependent_ids(generation,r)).intersection(family['leaf_ids']))]
    for kind, observations in [('split_parent', family['parents']), ('observation', family['leaves'])]:
        for observation in observations:
            readings = {r['id']: r for r in observation['readings']}
            for role in ('dimension_check_ids', 'radius_check_ids'):
                for key in observation['geometry'][role]:
                    origin = {'target_kind': kind, 'target_id': observation['id'],
                        'target_fingerprint': duct._observation_fingerprint(observation), 'reading_id': key,
                        'reading_fingerprint': duct._observation_fingerprint(readings[key]), 'role': role, 'observation': copy.deepcopy(observation)}
                    result.append({'id': 'duct_arc_check_' + duct._digest(origin)[:40], 'family_root_id': family['root_id'],
                        'origin': origin, 'state': 'active', 'observation': copy.deepcopy(observation), 'retained_observation': copy.deepcopy(observation),
                        'evidence_ids': list(observation['group']['evidence_ids']), 'reason': 'Original consumed source reading'})
    for check in result:
        check['fingerprint'] = duct._digest(check)
    return result


def view(workspace, data, sheets, generation):
    families = copy.deepcopy(_families(generation))
    if not families:
        return []
    fresh, problems = duct._fresh(workspace, data, sheets, generation)
    for family in families:
        family['root_fingerprint'] = duct._observation_fingerprint(family['root'])
        family['checks'] = _checks(generation, family)
        closure = duct._closure(family['leaf_ids'], generation, generation)
        family['family_fingerprint'] = duct._digest({'family': family, 'generation_id': generation['id'],
            'dependencies': {key: fresh['dependencies']['rows'].get(key) for key in closure},
            'problems': problems, 'implementation': duct._implementation()})
    return families


def _source_evidence(workspace, data, sheets, candidate, identifiers, source):
    duct._core()._ids(identifiers, minimum=1)
    evidence = duct._attach_evidence(workspace, data, sheets, candidate, set(identifiers))
    if any(evidence[key]['source'] != source for key in identifiers):
        duct._fail('duct_arc_decision_evidence', 'Cite correction evidence on this exact original page.')
    return evidence


def _scope(core, old, root, shapes, proposal, geometry, evidence):
    scope = proposal['scope']
    if not isinstance(scope, dict):
        duct._fail('duct_arc_check_scope', 'Choose an explicit physical scope for this retained check.')
    kind = scope.get('kind')
    root_span = core.arc_span(root['geometry'], geometry)
    try:
        if kind == 'retained':
            duct._exact(scope, {'kind'})
            previous = old['observation'] or old['retained_observation']
            span = core.arc_span(previous['geometry'], geometry)
            if (span.center != root_span.center or span.radius_squared != root_span.radius_squared or span.direction != root_span.direction):
                duct._fail('duct_arc_check_scope', 'A changed circle needs an explicit new check scope or unresolved decision.')
            retained = core.sheet_scale.arc_geometry.make_span(root_span.root, span.start_ray, span.end_ray)
            return core.span_geometry(root['geometry'], retained)
        if kind == 'root':
            duct._exact(scope, {'kind'})
            return copy.deepcopy(root['geometry'])
        if kind == 'portion':
            duct._exact(scope, {'kind', 'index'})
            index = scope['index']
            if type(index) is not int or not 0 <= index < len(shapes):
                duct._fail('duct_arc_check_scope', 'Choose one current proposed portion for the reading.')
            return copy.deepcopy(shapes[index])
        if kind == 'interval':
            duct._exact(scope, {'kind', 'start_point', 'end_point'})
            points = core._path([scope['start_point'], scope['end_point']])
            rays = [core.sheet_scale.arc_geometry.ray_from_point(root_span.root,
                duct.geometry.display_to_pdf(point, geometry)) for point in [scope['start_point'], scope['end_point']]]
            retained = core.sheet_scale.arc_geometry.make_span(root_span.root, *rays)
            for ray in rays:
                if not any(core.sheet_scale.arc_geometry.endpoint_in_rect(root_span.root, ray,
                    core._pdf_box(evidence[eid]['bbox'], geometry)) for eid in proposal['evidence_ids']):
                    duct._fail('duct_arc_check_scope', 'Each actual check endpoint needs its own source region support.')
            return core.span_geometry(root['geometry'], retained)
    except ValueError as exc:
        duct._fail('duct_arc_check_scope', str(exc))
    duct._fail('duct_arc_check_scope', 'Choose retained, whole arc, resulting portion or a supported independent interval.')


def preserve_obligated_roots(previous, candidate):
    """Legacy mutators cannot detach a family from its independent checks."""
    if previous is None or not previous.get('arc_obligations'):
        return
    def nodes(generation):
        return {o['id']: o for o in generation['observations'] +
                [r['parent'] for r in generation.get('planar_splits', [])]}
    old, new = nodes(previous), nodes(candidate)
    retained = {r['id']: r for r in candidate.get('arc_obligations', [])}
    for check in previous['arc_obligations']:
        target_ids = check.get('affected_observation_ids', [check['family_root_id']])
        if check['id'] not in retained or retained[check['id']] != check:
            duct._fail('duct_arc_family_required', 'Use the reviewed family or revision check workflow to change a retained check.')
        for root_id in target_ids:
            before, after = old.get(root_id), new.get(root_id)
            if (before is None or after is None or after['schema'] != before['schema'] or
                    after['source'] != before['source'] or after['geometry'] != before['geometry']):
                duct._fail('duct_arc_family_required',
                    'Use Correct saved arc family or source revision review to change geometry with retained checks.')


def review(workspace, data, sheets, values, actor, reason):
    duct._exact(values, {'generation_id', 'root_id', 'family_fingerprint', 'root', 'cuts', 'groups', 'dispositions', 'evidence_ids'})
    generation = duct._generation(data, values['generation_id'])
    family = next((f for f in view(workspace, data, sheets, generation) if f['root_id'] == values['root_id']), None)
    if family is None or family['family_fingerprint'] != values['family_fingerprint']:
        duct._fail('duct_arc_family_stale', 'The saved circular family changed. Reopen its current correction.')
    core, links = duct._core(), duct._module('project_duct_reading_links')
    geometry, unused = duct._source_context(data, sheets, family['source'])
    latest = {a['observation_id']: a for a in generation['admissions']}
    if any(latest[o['id']]['state'] != 'included' or latest[o['id']]['role'] != 'duct' for o in family['leaves']):
        duct._fail('duct_arc_family_role', 'Review each saved family portion as included ductwork before replacing it.')
    root = core.validate_observation(values['root'])
    if (root['id'] != family['root_id'] or root['source'] != family['source'] or root['schema'] != 'duct-observation-2' or
            root['geometry']['kind'] != 'circular_arc' or root['readings'] or
            root['geometry']['dimension_check_ids'] or root['geometry']['radius_check_ids']):
        duct._fail('duct_arc_root', 'Correct this same-page circular root; account for every old reading in the check decisions.')
    candidate = copy.deepcopy(generation)
    root['id'] = 'duct_arc_root_' + uuid.uuid4().hex
    _source_evidence(workspace, data, sheets, candidate, values['evidence_ids'], root['source'])
    duct._register_evidence(workspace, data, sheets, candidate, root)
    span = core.arc_span(root['geometry'], geometry)
    current_sources, unused = duct._current_sources(workspace, data, sheets, candidate)
    evidence = {e['id']: e for c in current_sources.values() for e in c['evidence']}
    core._require_graphic_support(root, None, geometry, evidence, span=span)
    cuts = core._array(values['cuts'], 127)
    partition = core.partition_arc_span(root['geometry'], geometry, cuts) if cuts else None
    shapes = partition['geometries'] if cuts else [copy.deepcopy(root['geometry'])]
    if len(core._array(values['groups'], 128, 1)) != len(shapes):
        duct._fail('duct_arc_groups', 'Review a complete group for every proposed interval.')
    for index, cut in enumerate(cuts):
        refs = _source_evidence(workspace, data, sheets, candidate, cut['evidence_ids'], root['source'])
        if any(not core.split_boundary_inside(partition, index, refs[eid]['bbox'], geometry) for eid in cut['evidence_ids']):
            duct._fail('duct_split_boundary', 'The actual circular boundary must lie inside every cited source region.')
    boundary_ids = sorted({eid for cut in cuts for eid in cut['evidence_ids']})
    children = []
    for shape, group in zip(shapes, values['groups']):
        child = copy.deepcopy(root)
        child.update(group=copy.deepcopy(group), geometry=copy.deepcopy(shape))
        if cuts:
            child.update(id='duct_arc_portion_' + uuid.uuid4().hex, schema='duct-observation-3')
        child['evidence_ids'] = sorted(set(root['evidence_ids']) | set(group.get('evidence_ids', [])) | set(boundary_ids))
        child = core.validate_observation(child)
        duct._register_evidence(workspace, data, sheets, candidate, child)
        children.append(child)
    old_ids = set(family['leaf_ids']) | {o['id'] for o in family['parents']}
    candidate['observations'] = [o for o in candidate['observations'] if o['id'] not in old_ids] + children
    candidate['planar_splits'] = [s for s in candidate.get('planar_splits', []) if s['parent']['id'] not in old_ids]
    if cuts:
        candidate['planar_splits'].append({'schema': 'duct-arc-split-1', 'id': 'duct_split_' + uuid.uuid4().hex,
            'parent': root, 'parent_fingerprint': core.fingerprint(root), 'cuts': copy.deepcopy(cuts),
            'members': [{'observation_id': o['id'], 'observation_fingerprint': core.fingerprint(o)} for o in children],
            'evidence_ids': boundary_ids})
    candidate['admissions'] = [a for a in candidate['admissions'] if a['observation_id'] not in old_ids]
    candidate['admissions'].extend(duct._admission(o, duct._rule_binding(), method='user_review') for o in children)
    proposals = core._array(values['dispositions'], 2000)
    by_id = {c['id']: c for c in family['checks']}
    if (len(proposals) != len(by_id) or any(not isinstance(p, dict) for p in proposals) or
            {p.get('id') for p in proposals} != set(by_id)):
        duct._fail('duct_arc_checks_incomplete', 'Explicitly decide every retained reading once, including removed boundaries.')
    candidate['arc_obligations'] = [r for r in candidate.get('arc_obligations', []) if r['id'] not in by_id]
    events, check_changes = [], []
    for proposal in proposals:
        duct._exact(proposal, {'id', 'fingerprint', 'state', 'scope', 'reading', 'evidence_ids', 'relationships', 'reason'})
        old = by_id[proposal['id']]
        if old['fingerprint'] != proposal['fingerprint']:
            duct._fail('duct_arc_check_stale', 'A retained reading changed. Reopen the current family.')
        core._enum(proposal['state'], {'active', 'unresolved', 'not_applicable'})
        core._plain(proposal['reason'], 2000)
        evidence = _source_evidence(workspace, data, sheets, candidate, proposal['evidence_ids'], root['source'])
        record = {'id': old['id'], 'family_root_id': root['id'], 'origin': copy.deepcopy(old['origin']),
            'state': proposal['state'], 'observation': None,
            'retained_observation': copy.deepcopy(old['observation'] or old['retained_observation']), 'evidence_ids': copy.deepcopy(proposal['evidence_ids']), 'reason': proposal['reason']}
        versioned = old.get('schema') == 'duct-arc-revision-check-1'
        if versioned:
            if (proposal['state'] == 'active' and
                    not set(duct._module('project_duct_revision_checks').dependent_ids(generation,old)) <= set(family['leaf_ids'])):
                duct._fail('duct_arc_check_state',
                    'Resolve a check spanning other families in revision check review, with evidence for every current page.')
            record.update(schema=old['schema'], current_source=copy.deepcopy(root['source']),
                affected_observation_ids=[root['id']], revision_binding=copy.deepcopy(old['revision_binding']))
            if proposal['state'] == 'unresolved':
                record['family_root_id'] = None
                record['affected_observation_ids'] = [key for key in old['affected_observation_ids'] if key not in old_ids] + [root['id']]
                sources = {duct._digest(o['source']):o['source'] for key,o in duct._module('project_duct_revision_checks').nodes(candidate).items()
                    if key in record['affected_observation_ids']}
                record['current_source'] = copy.deepcopy(next(iter(sources.values()))) if len(sources)==1 else None
            if proposal['state'] == 'not_applicable' and not set(duct._module('project_duct_revision_checks').dependent_ids(generation,old)) <= set(family['leaf_ids']):
                duct._fail('duct_arc_check_state', 'Retire a multiple-family pending check in the revision check review.')
        if proposal['state'] != 'active':
            if proposal['scope'] is not None or proposal['reading'] is not None or proposal['relationships'] != []:
                duct._fail('duct_arc_check_state', 'Unresolved or inapplicable decisions retain their old reading without an active scope.')
        else:
            shape = _scope(core, old, root, shapes, proposal, geometry, evidence)
            shape.update(dimension_check_ids=[], radius_check_ids=[])
            shape[old['origin']['role']] = [old['origin']['reading_id']]
            observation = copy.deepcopy(root)
            observation.update(id=old['id'], schema='duct-observation-3' if shape['kind']=='circular_arc_span' else 'duct-observation-2',
                geometry=shape, readings=[copy.deepcopy(proposal['reading'])])
            observation['evidence_ids'] = sorted(set(root['evidence_ids']) | set(proposal['evidence_ids']) | set(proposal['reading'].get('evidence_ids', [])))
            observation = core.validate_observation(observation)
            if observation['readings'][0]['id'] != old['origin']['reading_id']:
                duct._fail('duct_arc_check_reading', 'The retained reading identity and role cannot change.')
            record['observation'] = observation
            record['retained_observation'] = copy.deepcopy(observation)
            candidate['arc_obligations'].append(record)
            target = {'id': old['id'], 'kind': 'arc_check'}
            link_events, unused = links._review_relationships(duct, core, workspace, data, sheets, generation,
                candidate, observation, target, core._array(proposal['relationships'], 128), actor, reason)
            events.extend(link_events)
            duct._register_evidence(workspace, data, sheets, candidate, observation)
            record['observation'] = observation
        if proposal['state'] != 'active' and not (versioned and proposal['state'] == 'not_applicable'):
            candidate['arc_obligations'].append(record)
        check_changes.append({'before':{k:v for k,v in old.items() if k!='fingerprint'},
            'after':copy.deepcopy(record) if not (versioned and proposal['state']=='not_applicable') else None,
            'decision_evidence':[copy.deepcopy(evidence[eid]) for eid in proposal['evidence_ids']]})
    duct._module('duct_arc_obligations').validate(core, candidate['arc_obligations'])
    # Replacing identities invalidates former physical correspondence. Bind the
    # replacement portions into an explicitly uncertain review, so no new row
    # bypasses the old duplicate uncertainty while unrelated rows remain intact.
    for relation in {r['id']: r for r in generation['correspondences']}.values():
        if relation['state'] == 'rejected' or not old_ids.intersection(relation['members']):
            continue
        pending = copy.deepcopy(relation)
        pending['members'] = {key: value for key, value in pending['members'].items() if key not in old_ids}
        pending['members'].update({o['id']: core.fingerprint(o) for o in children})
        if len(pending['members']) < 2:
            old_key = next(iter(relation['members']))
            pending['members'][old_key] = relation['members'][old_key]
        pending.update(state='uncertain', primary_observation_id=children[0]['id'])
        candidate['correspondences'].append(pending)
    candidate['coverage'] = duct._coverage(candidate['observations'], candidate['current_sources'],
        ['Corrected circular families need renewed source coverage and correspondence review.'])
    selected = list(old_ids) + [o['id'] for o in children]
    token = duct._module('project_duct_revision_checks').transition(generation,candidate,check_changes,kind='family',root_id=family['root_id'])
    result = duct._retain(workspace, data, sheets, candidate, 'arc_family_correction', actor, reason, selected,check_transition=token)
    for event in events:
        event['replacement_generation_id'] = result['id']
        data['duct_reading_relationship_events'].append(event)
    event = {'id': 'duct_arc_correction_' + uuid.uuid4().hex, 'schema': 'duct-arc-repartition-1',
        'base_generation_id': generation['id'], 'replacement_generation_id': result['id'],
        'before': copy.deepcopy(family),
        'after': None, 'arc_check_transitions':check_changes,
        'after_root_id': root['id'], 'after_leaf_ids': [o['id'] for o in children],
        'request': copy.deepcopy(values), 'actor': actor, 'reason': reason, 'at': datetime.now(timezone.utc).isoformat()}
    event['fingerprint'] = duct._digest(event)
    data['duct_arc_repartition_events'].append(event)
    event['after'] = next(f for f in view(workspace, data, sheets, result) if f['root_id'] == root['id'])
    event['fingerprint'] = duct._digest({key:value for key,value in event.items() if key!='fingerprint'})
    duct._module('project_duct_revision_checks').verify_lineage(data)
    return result
