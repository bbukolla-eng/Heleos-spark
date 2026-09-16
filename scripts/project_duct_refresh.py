"""Immutable reread staging and explicit source-bound duct proposal mapping."""
import copy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import uuid

_spec = importlib.util.spec_from_file_location('duct_refresh_takeoff', Path(__file__).with_name('project_duct_takeoff.py'))
duct = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(duct)


def _seal(record):
    record['fingerprint'] = duct._digest(record)
    return record


def _verify_seal(record):
    if record.get('fingerprint') != duct._digest({k: v for k, v in record.items() if k != 'fingerprint'}):
        duct._fail('duct_refresh_changed', 'The retained reread or review record changed.')


def _dependency(workspace, data, sheets):
    generation = duct._saved(data)
    fresh, problems = duct._fresh(workspace, data, sheets, generation)
    return duct._digest({'generation_id': generation['id'], 'dependencies': fresh['dependencies'], 'problems': problems})


def _verify(workspace, record):
    _verify_seal(record)
    producer = duct._producer(workspace, record['producer_record_id'])
    if duct._digest(producer) != record['producer_sha256'] or producer != record['producer']:
        duct._fail('duct_refresh_changed', 'The retained reread producer bytes changed.')
    return producer


def _decision(data, identifier):
    found = [record for record in data['duct_refresh_decisions'] if record['refresh_id'] == identifier]
    if len(found) > 1:
        duct._fail('duct_refresh_changed', 'A reread has conflicting retained review decisions.')
    if found:
        _verify_seal(found[0])
        return found[0]
    return None


def _stage(data, values):
    record = next((r for r in data['duct_refreshes'] if r['id'] == values['refresh_id']), None)
    if record is None or record['fingerprint'] != values['refresh_fingerprint']:
        duct._fail('duct_refresh_changed', 'Select the unchanged saved reread before reviewing it.')
    _verify_seal(record)
    return record


def _current(workspace, data, sheets, record, dependency=None):
    if data['duct_current'] != record['base_generation_id']:
        duct._fail('duct_refresh_stale', 'The duct draft changed. Restage this reread against the current draft.')
    if (dependency or _dependency(workspace, data, sheets)) != record['base_dependency_fingerprint']:
        duct._fail('duct_refresh_stale', 'Source or calculation dependencies changed. Restage this reread.')
    for key, context in record['producer']['current_sources'].items():
        geom = context['geometry']
        source = {field: geom[field] for field in ('revision_id', 'index', 'sheet_id')}
        source['geometry_fingerprint'] = geom['fingerprint']
        unused, binding = duct._source_context(data, sheets, source)
        if binding != record['source_bindings'][key]:
            duct._fail('duct_refresh_stale', 'The reread page assignment changed. Restage against its current assignment.')


def stage(workspace, data, sheets, producer, observations, sources, bindings, actor, reason):
    prior = duct._saved(data)
    core = duct._core()
    core._current_context(sources)
    if len(observations) > 2000 or len({o['id'] for o in observations}) != len(observations):
        duct._fail('duct_refresh_partition', 'The reread needs bounded, unique original proposal identities.')
    for observation in observations:
        context = sources.get(observation['source']['sheet_id'])
        geom = context['geometry'] if context else {}
        if any(observation['source'][k] != geom.get(k) for k in ('revision_id', 'index', 'sheet_id')) or observation['source']['geometry_fingerprint'] != geom.get('fingerprint'):
            duct._fail('duct_refresh_source', 'Each incoming proposal must bind its exact retained source.')
    dependency = _dependency(workspace, data, sheets)
    producer_hash = duct._digest(producer)
    for previous in data['duct_refreshes']:
        if (previous['producer_record_id'] == producer['id'] and previous['producer_sha256'] == producer_hash
                and previous['base_generation_id'] == prior['id']
                and previous['base_dependency_fingerprint'] == dependency
                and previous['source_bindings'] == bindings):
            _verify(workspace, previous)
            return previous
    if len(data['duct_refreshes']) >= 1000:
        duct._fail('duct_refresh_limit', 'The retained reread collection is full.')
    record = _seal({'schema': 'duct-refresh-1', 'id': 'duct_refresh_' + uuid.uuid4().hex,
        'base_generation_id': prior['id'], 'base_dependency_fingerprint': dependency,
        'producer_record_id': producer['id'], 'producer_sha256': producer_hash,
        'producer': copy.deepcopy(producer), 'source_bindings': copy.deepcopy(bindings),
        'incoming': [{'id': 'incoming-' + str(index), 'observation': copy.deepcopy(observation),
                      'fingerprint': duct._observation_fingerprint(observation)} for index, observation in enumerate(observations)],
        'current_ids': [o['id'] for o in prior['observations'] if o['source']['sheet_id'] in sources],
        'actor': actor, 'reason': reason, 'at': datetime.now(timezone.utc).isoformat()})
    data['duct_refreshes'].append(record)
    for previous in data['duct_refreshes'][:-1]:
        if (previous['producer_record_id'] == producer['id'] and _decision(data, previous['id']) is None):
            _verify(workspace, previous)
            data['duct_refresh_decisions'].append(_seal({
                'id': 'duct_refresh_decision_' + uuid.uuid4().hex, 'refresh_id': previous['id'],
                'refresh_fingerprint': previous['fingerprint'], 'request': {'kind': 'superseded',
                    'groups': [], 'actor': actor, 'reason': reason}, 'groups': [],
                'base_generation_id': previous['base_generation_id'], 'replacement_generation_id': None,
                'successor_refresh_id': record['id'], 'successor_refresh_fingerprint': record['fingerprint'],
                'at': datetime.now(timezone.utc).isoformat()}))
    return record


def _groups(values, record, generation):
    groups = values['groups']
    if not isinstance(groups, list) or len(groups) > 2000:
        duct._fail('duct_refresh_partition', 'Provide bounded explicit review groups.')
    incoming = {r['id']: r for r in record['incoming']}
    current = {r['id']: r for r in generation['observations']}
    split_members = {member['observation_id'] for split in generation.get('planar_splits', [])
                     for member in split['members']}
    seen_incoming, seen_current = set(), set()
    core = duct._core()
    for group in groups:
        tagged = isinstance(group, dict) and group.get('schema') == 'duct-refresh-arc-partition-1'
        fields = {'incoming_ids', 'current_ids', 'disposition', 'evidence_ids', 'reason', 'cuts'}
        duct._exact(group, fields | ({'schema', 'groups'} if tagged else set()))
        for name in ('incoming_ids', 'current_ids', 'evidence_ids'):
            core._ids(group[name], 2000, 0 if name == 'current_ids' else 1)
        core._plain(group['reason'], 500)
        if (not set(group['incoming_ids']) <= set(incoming) or set(group['incoming_ids']) & seen_incoming
                or not set(group['current_ids']) <= set(record['current_ids'])
                or not set(group['current_ids']) <= set(current) or set(group['current_ids']) & seen_current):
            duct._fail('duct_refresh_partition', 'Every incoming proposal belongs to one group; current mappings cannot overlap.')
        seen_incoming.update(group['incoming_ids'])
        seen_current.update(group['current_ids'])
        disposition = group['disposition']
        if disposition not in ('retain_current', 'replace_current', 'add_new', 'exclude'):
            duct._fail('duct_refresh_mapping', 'Choose retain, replace, add or exclude for each incoming group.')
        if (disposition in ('retain_current', 'replace_current') and not group['current_ids'] or
                disposition in ('add_new', 'exclude') and group['current_ids'] or
                disposition == 'replace_current' and len(group['current_ids']) != 1):
            duct._fail('duct_refresh_mapping', 'Replacement maps one current portion; retain, add and exclude need their matching identities.')
        if (not tagged and disposition == 'replace_current' and len(group['incoming_ids']) == 1 and
                set(group['current_ids']) & split_members and
                incoming[group['incoming_ids'][0]]['observation']['geometry']['kind'] == 'circular_arc'):
            duct._fail('duct_arc_split_unsupported', 'A retained planar split member cannot be replaced by a circular arc.')
        if tagged and (disposition != 'replace_current' or len(group['incoming_ids']) != 1 or
                incoming[group['incoming_ids'][0]]['observation']['geometry']['kind'] != 'circular_arc' or
                current[group['current_ids'][0]]['geometry']['kind'] not in ('circular_arc', 'circular_arc_span') or
                not isinstance(group['groups'], list) or not isinstance(group['cuts'], list) or
                len(group['groups']) != len(group['cuts']) + 1):
            duct._fail('duct_refresh_mapping', 'An explicit arc review derives groups from one incoming full arc and one current arc interval.')
        if not tagged and (not isinstance(group['cuts'], list) or (group['cuts'] and not (
                disposition == 'replace_current' and len(group['incoming_ids']) > 1))):
            duct._fail('duct_refresh_mapping', 'Cuts apply only to an ordered one-to-many planar replacement.')
        if tagged:
            core._array(group['cuts'], 127)
            core._array(group['groups'], 128, 1)
            if any(not isinstance(item, dict) for item in group['groups']):
                duct._fail('duct_split_groups', 'Provide a source-supported grouping object for every derived portion.')
        sources = [incoming[key]['observation']['source'] for key in group['incoming_ids']]
        sources += [current[key]['source'] for key in group['current_ids']]
        if any(source != sources[0] for source in sources[1:]):
            duct._fail('duct_refresh_source', 'A review group must use one exact original page identity.')
    if seen_incoming != set(incoming):
        duct._fail('duct_refresh_partition', 'Account for every incoming proposal before applying the review.')
    return groups, incoming, current


def _preserve_dimensions(previous, replacement):
    """A reread preserves every supported check and its original semantic role."""
    shape = previous['geometry']
    if shape['kind'] not in ('planar', 'circular_arc', 'circular_arc_span'):
        if previous['readings'] and (replacement['geometry'] != shape or replacement['readings'] != previous['readings']):
            duct._fail('duct_refresh_applicability', 'This replacement would change supported nonplanar readings. Retain it and use a source-bound correction.')
        return
    roles = ['dimension_check_ids'] + (['radius_check_ids'] if shape['kind'] in ('circular_arc', 'circular_arc_span') else [])
    if not any(shape[role] for role in roles):
        return
    if (replacement['geometry']['kind'] != shape['kind'] and
            not {replacement['geometry']['kind'], shape['kind']} <= {'circular_arc', 'circular_arc_span'}):
        duct._fail('duct_refresh_applicability', 'A retained planar whole-run check cannot be assigned to different geometry.')
    old = {r['id']: r for r in previous['readings']}
    new = {r['id']: r for r in replacement['readings']}
    for role in roles:
        other_role_ids = {key for other in roles if other != role for key in replacement['geometry'][other]}
        for identifier in shape[role]:
            reading = copy.deepcopy(old[identifier])
            if (identifier in new and new[identifier] != reading) or identifier in other_role_ids:
                # Existing planar aliases retain their exact derivation. Arc
                # aliases also bind radius versus whole-length consumption.
                binding = {'role': role, 'reading': reading} if shape['kind'] in ('circular_arc', 'circular_arc_span') else reading
                reading['id'] = 'duct_retained_reading_' + duct._digest(binding)
            if (reading['id'] in new and new[reading['id']] != reading) or reading['id'] in other_role_ids:
                duct._fail('duct_refresh_applicability', 'Retained and incoming readings use a conflicting identity or role.')
            if reading['id'] not in new:
                replacement['readings'].append(reading)
                new[reading['id']] = reading
            if reading['id'] not in replacement['geometry'][role]:
                replacement['geometry'][role].append(reading['id'])
            replacement['evidence_ids'] = sorted(set(replacement['evidence_ids']) | set(reading['evidence_ids']))


def _partition(workspace, data, sheets, generation, parent, proposals, group):
    core = duct._core()
    if parent['geometry']['kind'] in ('circular_arc', 'circular_arc_span') or any(p['geometry']['kind'] in ('circular_arc', 'circular_arc_span') for p in proposals):
        duct._fail('duct_arc_split_unsupported', 'Circular-arc partitioning is not supported; retain the whole arc or correct it explicitly.')
    if parent['geometry']['kind'] != 'planar' or parent['issues']:
        duct._fail('duct_refresh_applicability', 'Only a supported planar parent can be mapped to ordered child portions.')
    geom, unused = duct._source_context(data, sheets, parent['source'])
    partition = core.partition_planar_path(parent['geometry']['points'], geom, group['cuts'])
    if len(proposals) != len(partition['paths']):
        duct._fail('duct_refresh_partition', 'Provide one incoming child for each ordered partition.')
    available = duct._register_evidence(workspace, data, sheets, generation, parent, group['evidence_ids'])
    for index, cut in enumerate(partition['cuts']):
        if not set(cut['evidence_ids']) <= set(group['evidence_ids']):
            duct._fail('duct_refresh_boundary', 'Include every cut reference in the group evidence.')
        point = partition['paths'][index][-1]
        for key in cut['evidence_ids']:
            evidence = available.get(key)
            if evidence is None or evidence['source'] != parent['source']:
                duct._fail('duct_refresh_boundary', 'Each boundary needs retained evidence on the parent source.')
            x0, y0, x1, y1 = map(str, evidence['bbox'])
            number = core._number
            if not (number(x0) <= number(str(point[0])) <= number(x1) and number(y0) <= number(str(point[1])) <= number(y1)):
                duct._fail('duct_refresh_boundary', 'Each split boundary must lie inside its cited source regions.')
    for proposal, path in zip(proposals, partition['pdf_paths']):
        if (proposal['geometry']['kind'] != 'planar' or proposal['source'] != parent['source'] or
                core.validate_planar_path(proposal['geometry']['points'], geom)['pdf_points'] != path):
            duct._fail('duct_refresh_partition', 'Incoming child paths must exactly partition the retained parent in the selected order.')
    return partition


def _arc_review(workspace, data, sheets, generation, previous, incoming, group):
    """Explicitly derive only the reviewed source interval from frozen v2 input."""
    core = duct._core()
    geom, unused = duct._source_context(data, sheets, previous['source'])
    replacement = copy.deepcopy(incoming)
    replacement['id'] = previous['id']
    replacement['issues'] = sorted(set(previous['issues']) | set(incoming['issues']))
    original_shape = previous['geometry']
    if original_shape['kind'] == 'circular_arc_span':
        current_span = core.arc_span(original_shape, geom)
        incoming_span = core.arc_span(incoming['geometry'], geom)
        if any(getattr(current_span.root, field) != getattr(incoming_span.root, field)
               for field in ('center', 'radius_squared', 'direction')):
            duct._fail('duct_refresh_arc_interval', 'A derived interval reread must retain its exact circle and direction.')
        try:
            core.sheet_scale.arc_geometry.make_span(incoming_span.root, current_span.start_ray, current_span.end_ray)
        except ValueError:
            duct._fail('duct_refresh_arc_interval', 'The incoming arc must contain the entire retained interval.')
        if ((incoming_span.start_ray, incoming_span.end_ray) != (current_span.start_ray, current_span.end_ray) and
                incoming['geometry']['dimension_check_ids']):
            duct._fail('duct_refresh_arc_whole_scope', 'A wider incoming whole-arc reading cannot check this smaller interval. Retain it and repair the applicable retained parent reading explicitly.')
        replacement['schema'] = 'duct-observation-3'
        replacement['geometry'] = core.span_geometry(original_shape, current_span)
        for key in ('scale_fact_id', 'dimension_check_ids', 'radius_check_ids'):
            replacement['geometry'][key] = copy.deepcopy(incoming['geometry'][key])
    incoming_refs = list(replacement['evidence_ids'])
    _preserve_dimensions(previous, replacement)
    replacement['evidence_ids'] = incoming_refs + sorted(set(replacement['evidence_ids']) - set(incoming_refs))
    core.validate_observation(replacement)
    available = duct._register_evidence(workspace, data, sheets, generation, replacement, group['evidence_ids'])
    if not group['cuts']:
        replacement['group'] = copy.deepcopy(group['groups'][0])
        replacement['evidence_ids'] = sorted(set(replacement['evidence_ids']) | set(replacement['group'].get('evidence_ids', [])))
        core.validate_observation(replacement)
        duct._register_evidence(workspace, data, sheets, generation, replacement, group['evidence_ids'])
        return replacement, [replacement], None
    partition = core.partition_arc_span(replacement['geometry'], geom, group['cuts'])
    for index, cut in enumerate(partition['cuts']):
        if not set(cut['evidence_ids']) <= set(group['evidence_ids']):
            duct._fail('duct_refresh_boundary', 'Include every exact boundary reference in the review evidence.')
        for key in cut['evidence_ids']:
            item = available.get(key)
            if (item is None or item['source'] != previous['source'] or
                    not core.split_boundary_inside(partition, index, item['bbox'], geom)):
                duct._fail('duct_refresh_boundary', 'The actual arc boundary must lie in every cited region on its exact source.')
    boundary_ids = sorted({key for cut in partition['cuts'] for key in cut['evidence_ids']})
    children = []
    links = duct._module('project_duct_reading_links')
    for shape, grouping in zip(partition['geometries'], group['groups']):
        child = copy.deepcopy(replacement)
        child.update(id='duct_refresh_portion_' + uuid.uuid4().hex, schema='duct-observation-3',
                     geometry=shape, group=copy.deepcopy(grouping))
        child['evidence_ids'] = sorted(set(child['evidence_ids']) | set(grouping.get('evidence_ids', [])) | set(boundary_ids))
        links.prune_unused_foreign_readings(child, duct._known_evidence(generation))
        core.validate_observation(child)
        duct._register_evidence(workspace, data, sheets, generation, child, group['evidence_ids'])
        children.append(child)
    constraint = {'schema': 'duct-arc-split-1', 'id': 'duct_refresh_split_' + uuid.uuid4().hex,
        'parent': replacement, 'parent_fingerprint': duct._observation_fingerprint(replacement),
        'cuts': copy.deepcopy(group['cuts']), 'evidence_ids': boundary_ids,
        'members': [{'observation_id': row['id'], 'observation_fingerprint': duct._observation_fingerprint(row)} for row in children]}
    return replacement, children, constraint


def review(workspace, data, sheets, values, actor, reason):
    duct._exact(values, {'refresh_id', 'refresh_fingerprint', 'groups'})
    record = _stage(data, values)
    decided = _decision(data, record['id'])
    request = {'kind': 'applied', 'groups': values['groups'], 'actor': actor, 'reason': reason}
    if decided:
        if decided['request'] != request:
            duct._fail('duct_refresh_decided', 'This reread already has a different retained review decision.')
        return decided
    producer = _verify(workspace, record)
    _current(workspace, data, sheets, record)
    generation = duct._saved(data)
    groups, incoming, current = _groups(values, record, generation)
    candidate = copy.deepcopy(generation)
    for context in producer['current_sources'].values():
        duct._merge_source(candidate['current_sources'], context)
    candidate['source_bindings'].update(copy.deepcopy(record['source_bindings']))
    candidate['source_records'].append({'id': producer['id'], 'sha256': record['producer_sha256'],
        'sheet_ids': sorted(producer['current_sources']), 'bindings': copy.deepcopy(record['source_bindings'])})
    changed, lineage = [], []
    core, rule = duct._core(), duct._rule_binding()
    for group in groups:
        proposals = [copy.deepcopy(incoming[key]['observation']) for key in group['incoming_ids']]
        for proposal in proposals:
            duct._register_evidence(workspace, data, sheets, candidate, proposal, group['evidence_ids'])
        disposition = group['disposition']
        mappings = []
        if disposition in ('retain_current', 'exclude'):
            mappings = [{'incoming_id': key, 'output_ids': copy.deepcopy(group['current_ids'])}
                        for key in group['incoming_ids']]
        elif disposition == 'add_new':
            for key, proposal in zip(group['incoming_ids'], proposals):
                proposal['id'] = 'duct_refresh_portion_' + uuid.uuid4().hex
                candidate['observations'].append(proposal)
                candidate['admissions'].append(duct._admission(proposal, rule, method='user_review', evidence_ids=group['evidence_ids']))
                changed.append(proposal['id'])
                mappings.append({'incoming_id': key, 'output_ids': [proposal['id']]})
        else:
            parent = current[group['current_ids'][0]]
            if group.get('schema') == 'duct-refresh-arc-partition-1':
                replacement, children, constraint = _arc_review(workspace, data, sheets, candidate, parent, proposals[0], group)
                # The original target becomes a retained parent only for 1+ cuts.
                # Update its immediate enclosing pin explicitly, without rebinding
                # unrelated stale ancestors or changing their reading obligations.
                old_pin = duct._observation_fingerprint(parent)
                for split in candidate.get('planar_splits', []):
                    for member in split['members']:
                        if member['observation_id'] == parent['id']:
                            if member['observation_fingerprint'] != old_pin:
                                duct._fail('duct_refresh_arc_interval', 'The enclosing retained membership is stale; repair it before rereading this span.')
                            member['observation_fingerprint'] = duct._observation_fingerprint(replacement)
                if constraint is not None:
                    candidate.setdefault('planar_splits', []).append(constraint)
                candidate['observations'] = [row for row in candidate['observations'] if row['id'] != parent['id']] + children
                candidate['admissions'].extend(duct._admission(row, rule, method='user_review', evidence_ids=group['evidence_ids']) for row in children)
                changed.extend([parent['id']] + [row['id'] for row in children])
                mappings = [{'incoming_id': group['incoming_ids'][0], 'output_ids': [row['id'] for row in children],
                    'derivation': {'target_id': parent['id'], 'target_fingerprint': old_pin,
                        'incoming_fingerprint': duct._observation_fingerprint(proposals[0]),
                        'retained_geometry': copy.deepcopy(replacement['geometry']),
                        'retained_fingerprint': duct._observation_fingerprint(replacement),
                        'constraint_id': constraint['id'] if constraint else None}}]
            elif len(proposals) == 1:
                replacement = proposals[0]
                replacement['id'] = parent['id']
                duct._preserve_span_geometry(parent, replacement)
                replacement['issues'] = sorted(set(parent['issues']) | set(replacement['issues']))
                _preserve_dimensions(parent, replacement)
                core.validate_observation(replacement)
                duct._register_evidence(workspace, data, sheets, candidate, replacement, group['evidence_ids'])
                candidate['observations'] = [replacement if row['id'] == parent['id'] else row for row in candidate['observations']]
                candidate['admissions'].append(duct._admission(replacement, rule, method='user_review', evidence_ids=group['evidence_ids']))
                changed.append(parent['id'])
                mappings = [{'incoming_id': group['incoming_ids'][0], 'output_ids': [parent['id']]}]
            else:
                partition = _partition(workspace, data, sheets, candidate, parent, proposals, group)
                for key, proposal in zip(group['incoming_ids'], proposals):
                    proposal['id'] = 'duct_refresh_portion_' + uuid.uuid4().hex
                    mappings.append({'incoming_id': key, 'output_ids': [proposal['id']]})
                candidate.setdefault('planar_splits', []).append({'id': 'duct_refresh_split_' + uuid.uuid4().hex,
                    'parent': copy.deepcopy(parent), 'parent_fingerprint': duct._observation_fingerprint(parent),
                    'cuts': partition['cuts'], 'members': [{'observation_id': proposal['id'],
                        'observation_fingerprint': duct._observation_fingerprint(proposal)} for proposal in proposals],
                    'evidence_ids': sorted({key for cut in partition['cuts'] for key in cut['evidence_ids']})})
                candidate['observations'] = [row for row in candidate['observations'] if row['id'] != parent['id']] + proposals
                candidate['admissions'].extend(duct._admission(proposal, rule, method='user_review', evidence_ids=group['evidence_ids']) for proposal in proposals)
                changed.extend([parent['id']] + [proposal['id'] for proposal in proposals])
        lineage.append({'group': copy.deepcopy(group), 'mappings': mappings})
    duct._rebind_splits(candidate, changed)
    candidate['coverage'] = duct._coverage(candidate['observations'], candidate['current_sources'],
        ['Source reread decisions need renewed source coverage and correspondence review.'])
    candidate.setdefault('refresh_lineage', []).append({'refresh_id': record['id'], 'refresh_fingerprint': record['fingerprint'],
        'producer_record_id': record['producer_record_id'], 'producer_sha256': record['producer_sha256'], 'groups': lineage})
    result = duct._retain(workspace, data, sheets, candidate, 'source_refresh_review', actor, reason, changed)
    decision = _seal({'id': 'duct_refresh_decision_' + uuid.uuid4().hex, 'refresh_id': record['id'],
        'refresh_fingerprint': record['fingerprint'], 'request': copy.deepcopy(request), 'groups': lineage,
        'base_generation_id': generation['id'], 'replacement_generation_id': result['id'],
        'at': datetime.now(timezone.utc).isoformat()})
    data['duct_refresh_decisions'].append(decision)
    return result


def reject(workspace, data, sheets, values, actor, reason):
    duct._exact(values, {'refresh_id', 'refresh_fingerprint'})
    record = _stage(data, values)
    request = {'kind': 'rejected', 'groups': [], 'actor': actor, 'reason': reason}
    decided = _decision(data, record['id'])
    if decided:
        if decided['request'] != request:
            duct._fail('duct_refresh_decided', 'This reread already has a different retained decision.')
        return decided
    _verify(workspace, record)
    decision = _seal({'id': 'duct_refresh_decision_' + uuid.uuid4().hex, 'refresh_id': record['id'],
        'refresh_fingerprint': record['fingerprint'], 'request': request, 'groups': [],
        'base_generation_id': record['base_generation_id'], 'replacement_generation_id': None,
        'at': datetime.now(timezone.utc).isoformat()})
    data['duct_refresh_decisions'].append(decision)
    return decision


def view(workspace, data, sheets):
    output = []
    dependency = _dependency(workspace, data, sheets) if data['duct_refreshes'] else None
    for record in data['duct_refreshes']:
        issues = []
        decision = _decision(data, record['id'])
        try:
            _verify(workspace, record)
            if decision is None:
                _current(workspace, data, sheets, record, dependency)
        except (ValueError, OSError, KeyError) as error:
            issues.append(getattr(error, 'message', 'The saved reread is unavailable or stale.'))
        output.append({'id': record['id'], 'fingerprint': record['fingerprint'],
            'state': decision['request']['kind'] if decision else 'pending',
            'validity': 'stale' if issues else 'current', 'issues': issues,
            'base_generation_id': record['base_generation_id'],
            'base_dependency_fingerprint': record['base_dependency_fingerprint'],
            'producer_record_id': record['producer_record_id'], 'source_bindings': copy.deepcopy(record['source_bindings']),
            'incoming': copy.deepcopy(record['incoming']), 'current_ids': copy.deepcopy(record['current_ids']),
            'evidence': [copy.deepcopy(e) for context in record['producer']['current_sources'].values() for e in context['evidence']],
            'decision_id': decision['id'] if decision else None,
            'replacement_generation_id': decision['replacement_generation_id'] if decision else None,
            'successor_refresh_id': decision.get('successor_refresh_id') if decision else None})
    return output


def verify_export(workspace, data):
    records = {}
    for record in data['duct_refreshes']:
        producer = _verify(workspace, record)
        records[producer['id']] = producer
        _decision(data, record['id'])
    return list(records.values())
