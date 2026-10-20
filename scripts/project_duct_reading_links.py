"""Explicit applicability for retained length/elevation readings on another page.

The pure functions do not grant geometry, grouping, admission or datum equivalence.
Workflow functions append decisions through the existing atomic generation writer.
"""
import copy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import uuid


def consumed_reading_ids(observation):
    geometry = observation['geometry']
    if geometry['kind'] == 'planar':
        return list(geometry['dimension_check_ids'])
    if geometry['kind'] in ('circular_arc', 'circular_arc_span'):
        return list(geometry['dimension_check_ids']) + list(geometry['radius_check_ids'])
    if geometry['kind'] == 'vertical':
        return ([geometry['rise_reading_id']] if geometry['rise_reading_id'] else []) + geometry['elevation_ids']
    if geometry['kind'] == 'slope':
        projection = geometry['projection']
        return geometry['elevation_ids'] + ([projection['reading_id']] if projection['kind'] == 'dimension' else [])
    return []


def validate_records(core, records):
    records = [] if records is None else core._array(records, 4000)
    seen = set()
    for record in records:
        core._exact(record, 'schema id observation_id observation_fingerprint reading_id reading_fingerprint observation_source reading_source observation_reference_evidence_ids reading_reference_evidence_ids state')
        core._enum(record['schema'], {'duct-reading-relationship-1'})
        for key in ('id', 'observation_id', 'reading_id'):
            core._plain(record[key], 160)
        for key in ('observation_fingerprint', 'reading_fingerprint'):
            core._hash(record[key])
        for key in ('observation_source', 'reading_source'):
            core._source(record[key])
        for key in ('observation_reference_evidence_ids', 'reading_reference_evidence_ids'):
            core._ids(record[key], minimum=1)
        core._enum(record['state'], {'verified', 'withdrawn'})
        if record['id'] in seen:
            core._fail('reading_relationship_identity', 'Reading relationship identities must be unique.')
        seen.add(record['id'])
    return records


def target_only_ids(observation, admission_ids=()):
    return set(observation['group']['evidence_ids']) | set(observation['geometry'].get('support_ids', [])) | set(admission_ids)


def applicability(core, observation, records, geometries, evidence, admission_ids=(), only_reading_id=None):
    """Return issues, optional exact dependencies, and valid foreign reading IDs."""
    selected = [r for r in records if r['observation_id'] == observation['id']]
    readings = {r['id']: r for r in observation['readings']}
    consumed = consumed_reading_ids(observation)
    # Historical links stay in the generation/view/event ledger. Only readings
    # that still consume foreign or missing evidence depend on their endpoints.
    # In particular, missing evidence must not look like a new local reading.
    dependency_records = [record for record in selected if record['reading_id'] in consumed and
        not all(eid in evidence and evidence[eid]['source'] == observation['source']
                for eid in readings[record['reading_id']]['evidence_ids'])]
    used = {eid for key in consumed for eid in readings[key]['evidence_ids']}
    foreign = {eid for eid in observation['evidence_ids'] if eid in evidence and evidence[eid]['source'] != observation['source']}
    forbidden = target_only_ids(observation, admission_ids)
    issues = []
    if foreign - used or any(eid in evidence and evidence[eid]['source'] != observation['source'] for eid in forbidden):
        issues.append('reading_evidence_role')
    allowed = set()
    for key in consumed:
        if only_reading_id is not None and key != only_reading_id:
            continue
        reading = readings[key]
        refs = [evidence[eid] for eid in reading['evidence_ids'] if eid in evidence and evidence[eid]['source'] != observation['source']]
        links = [r for r in selected if r['reading_id'] == key]
        if not refs and all(eid in evidence for eid in reading['evidence_ids']):
            # A replacement may reuse an old reading ID for local evidence.
            # Its historical foreign link grants no authority and cannot block
            # the independently supported local reading (the old record stays).
            continue
        if not refs and not links:
            continue
        if not links:
            issues.append('reading_relationship_missing')
            continue
        if len(links) != 1:
            issues.append('reading_relationship_conflict')
            continue
        link = links[0]
        if link['state'] == 'withdrawn':
            issues.append('reading_relationship_withdrawn')
            continue
        valid = (link['observation_fingerprint'] == core.fingerprint(observation) and
            link['reading_fingerprint'] == core.fingerprint(reading) and
            link['observation_source'] == observation['source'] and
            link['reading_source'] != observation['source'] and bool(refs) and
            all(ref['source'] == link['reading_source'] for ref in refs) and
            all(eid in evidence for eid in reading['evidence_ids']))
        for source_key, marker_key in (('observation_source', 'observation_reference_evidence_ids'),
                                      ('reading_source', 'reading_reference_evidence_ids')):
            source = link[source_key]
            geometry = geometries.get(source['sheet_id'])
            valid = valid and geometry is not None and all(source[k] == geometry[k] for k in ('revision_id', 'index', 'sheet_id')) and source['geometry_fingerprint'] == geometry['fingerprint']
            valid = valid and all(eid in evidence and evidence[eid]['source'] == source for eid in link[marker_key])
        if not valid:
            issues.append('reading_relationship_stale')
        else:
            allowed.update(ref['id'] for ref in refs)
    dependency = None
    if dependency_records or foreign:
        markers = {eid for r in dependency_records for key in ('observation_reference_evidence_ids', 'reading_reference_evidence_ids') for eid in r[key]}
        endpoints = {r[key]['sheet_id'] for r in dependency_records for key in ('observation_source', 'reading_source')}
        dependency = {'records': dependency_records, 'markers': {key: evidence.get(key) for key in sorted(markers)},
            'sources': {key: geometries[key]['fingerprint'] if key in geometries else None for key in sorted(endpoints)}}
    return sorted(set(issues)), dependency, allowed - forbidden


def prune_unused_foreign_readings(observation, evidence):
    consumed = set(consumed_reading_ids(observation))
    removed = [r for r in observation['readings'] if r['id'] not in consumed and
        any(key in evidence and evidence[key]['source'] != observation['source'] for key in r['evidence_ids'])]
    observation['readings'] = [r for r in observation['readings'] if r not in removed]
    used = {key for r in observation['readings'] for key in r['evidence_ids']} | target_only_ids(observation)
    discard = {key for r in removed for key in r['evidence_ids'] if key not in used and
        key in evidence and evidence[key]['source'] != observation['source']}
    observation['evidence_ids'] = [key for key in observation['evidence_ids'] if key not in discard]


def _duct():
    spec = importlib.util.spec_from_file_location('heleos_reading_project', Path(__file__).with_name('project_duct_takeoff.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def targets(generation, core):
    return [{'id': observation['id'], 'kind': kind, 'fingerprint': core.fingerprint(observation),
        'observation': copy.deepcopy(observation), 'source': copy.deepcopy(observation['source']),
        'consumed_reading_ids': consumed_reading_ids(observation)}
        for kind, rows in [('observation', generation['observations']),
                           ('split_parent', [r['parent'] for r in generation.get('planar_splits', [])]),
                           ('arc_check', [r['observation'] for r in generation.get('arc_obligations', []) if r['state']=='active'])]
        for observation in rows]


def _latest(data, target_id, reading_id):
    return next((event['id'] for event in reversed(data['duct_reading_relationship_events'])
        if event['target_id'] == target_id and event['reading_id'] == reading_id), None)


def _review_relationships(duct, core, workspace, data, sheets, generation, candidate, observation, target, descriptors, actor, reason):
    evidence_ids = set(observation['evidence_ids'])
    readings = {r['id']: r for r in observation['readings']}
    seen, events = set(), []
    for item in descriptors:
        duct._exact(item, {'reading_id', 'reading_source', 'observation_reference_evidence_ids', 'reading_reference_evidence_ids', 'supersedes'})
        key = item['reading_id']
        if key not in consumed_reading_ids(observation) or key in seen:
            duct._fail('duct_reading_consumed', 'Review each explicitly consumed reading at most once.')
        seen.add(key)
        if item['supersedes'] != _latest(data, target['id'], key):
            duct._fail('duct_reading_decision_stale', 'The reading decision changed. Refresh before reviewing it.')
        prior = next((r for r in candidate.get('reading_relationships', []) if r['observation_id'] == target['id'] and r['reading_id'] == key), None)
        record = {'schema': 'duct-reading-relationship-1', 'id': prior['id'] if prior else 'duct_reading_' + uuid.uuid4().hex,
            'observation_id': target['id'], 'observation_fingerprint': core.fingerprint(observation),
            'reading_id': key, 'reading_fingerprint': core.fingerprint(readings[key]),
            'observation_source': copy.deepcopy(observation['source']), 'reading_source': copy.deepcopy(item['reading_source']),
            'observation_reference_evidence_ids': copy.deepcopy(item['observation_reference_evidence_ids']),
            'reading_reference_evidence_ids': copy.deepcopy(item['reading_reference_evidence_ids']), 'state': 'verified'}
        validate_records(core, [record])
        for source in (record['observation_source'], record['reading_source']):
            duct._source_context(data, sheets, source)
        evidence_ids.update(record['observation_reference_evidence_ids'] + record['reading_reference_evidence_ids'])
        candidate['reading_relationships'] = [r for r in candidate.get('reading_relationships', []) if r['id'] != record['id']] + [record]
        events.append({'id': uuid.uuid4().hex, 'kind': 'reviewed', 'relationship_id': record['id'],
            'target_kind': target['kind'], 'target_id': target['id'], 'reading_id': key, 'supersedes': item['supersedes'],
            'before': copy.deepcopy(prior), 'after': copy.deepcopy(record), 'actor': actor, 'reason': reason,
            'base_generation_id': generation['id'], 'at': datetime.now(timezone.utc).isoformat(),
            'source_assignments': {label: copy.deepcopy(duct._assignment(data, record[label])) for label in ('observation_source', 'reading_source')}})
    evidence = duct._attach_evidence(workspace, data, sheets, candidate, evidence_ids)
    for item in descriptors:
        if not any(evidence[eid]['source'] == item['reading_source'] and
                   evidence[eid]['source'] != observation['source']
                   for eid in readings[item['reading_id']]['evidence_ids']):
            duct._fail('duct_reading_applicability', 'Each reviewed relationship must bind evidence actually consumed from its declared foreign source.')
    sources, unused = duct._current_sources(workspace, data, sheets, candidate)
    geometries = {key: context['geometry'] for key, context in sources.items()}
    issues, unused, allowed = applicability(core, observation, candidate.get('reading_relationships', []), geometries, evidence)
    if issues:
        duct._fail('duct_reading_applicability', 'Reading relationships are not current and applicable: ' + ', '.join(issues))
    return events, evidence


def review(workspace, data, sheets, values, actor, reason):
    duct = _duct()
    core = duct._core()
    duct._exact(values, {'generation_id', 'target_kind', 'target_id', 'target_fingerprint', 'observation', 'relationships'})
    generation = duct._generation(data, values['generation_id'])
    target = next((t for t in targets(generation, core) if t['id'] == values['target_id'] and t['kind'] == values['target_kind']), None)
    if target is None or target['fingerprint'] != values['target_fingerprint']:
        duct._fail('duct_reading_target_stale', 'Refresh the exact duct target before reviewing its reading relationships.')
    observation = core.validate_observation(values['observation'])
    if observation['id'] != target['id'] or observation['source'] != target['source']:
        duct._fail('duct_reading_target_source', 'A reading review retains the exact target and source identity.')
    if target['kind'] in ('split_parent', 'arc_check') and observation != target['observation']:
        duct._fail('duct_reading_parent_readonly', 'Review the current retained parent without editing its geometry or readings.')
    duct._preserve_span_geometry(target['observation'], observation)
    descriptors = core._array(values['relationships'], 128, 1)
    candidate = copy.deepcopy(generation)
    if target['kind'] == 'observation':
        candidate['observations'] = [observation if row['id'] == target['id'] else row for row in candidate['observations']]
    events, evidence = _review_relationships(duct, core, workspace, data, sheets, generation,
        candidate, observation, target, descriptors, actor, reason)
    if target['kind'] == 'observation':
        latest = next(a for a in reversed(generation['admissions']) if a['observation_id'] == target['id'])
        admission_ids = [eid for eid in latest['evidence_ids'] if eid in evidence and evidence[eid]['source'] == observation['source']]
        duct._register_evidence(workspace, data, sheets, candidate, observation, admission_ids)
        candidate['observations'] = [observation if row['id'] == target['id'] else row for row in candidate['observations']]
        duct._rebind_splits(candidate, [target['id']])
        candidate['admissions'].append(duct._admission(observation, duct._rule_binding(), latest['state'], latest['role'], 'user_review', admission_ids))
    candidate['coverage'] = duct._coverage(candidate['observations'], candidate['current_sources'], ['Reading applicability review needs renewed coverage review.'])
    result = duct._retain(workspace, data, sheets, candidate, 'reading_relationship_review', actor, reason, [target['id']])
    for event in events:
        event['replacement_generation_id'] = result['id']
        data['duct_reading_relationship_events'].append(event)
    return result


def _parent_target(duct, core, generation, parent_id, parent_fingerprint):
    matches = [r for r in generation.get('planar_splits', []) if r['parent']['id'] == parent_id]
    if len(matches) != 1:
        duct._fail('duct_parent_missing', 'Select one retained parent to correct.')
    original = matches[0]
    parent = original['parent']
    old_fingerprint = core.fingerprint(parent)
    if original['parent_fingerprint'] != old_fingerprint or parent_fingerprint != old_fingerprint:
        duct._fail('duct_parent_stale', 'The retained parent changed. Refresh before correcting it.')
    if parent['geometry']['kind'] not in ('planar', 'circular_arc', 'circular_arc_span'):
        duct._fail('duct_parent_geometry', 'Only a retained planar or circular parent can be corrected here.')
    return original, parent, old_fingerprint


def _replace_parent(duct, core, candidate, original, replacement, old_fingerprint):
    core.validate_observation(replacement)
    record = next(r for r in candidate['planar_splits'] if r['id'] == original['id'])
    record['parent'] = replacement
    new_fingerprint = core.fingerprint(replacement)
    record['parent_fingerprint'] = new_fingerprint
    enclosing = [(r, member) for r in candidate['planar_splits'] for member in r['members']
                 if member['observation_id'] == replacement['id']]
    if len(enclosing) > 1:
        duct._fail('duct_parent_membership', 'A retained parent cannot have more than one enclosing membership.')
    member_update = None
    if enclosing:
        enclosing_record, member = enclosing[0]
        if member['observation_fingerprint'] != old_fingerprint:
            duct._fail('duct_parent_membership_stale', 'The enclosing parent membership changed. Its existing mismatch cannot be repaired by a parent edit.')
        member_update = {'split_id': enclosing_record['id'], 'observation_id': replacement['id'],
            'before': old_fingerprint, 'after': new_fingerprint}
        member['observation_fingerprint'] = new_fingerprint
    return new_fingerprint, member_update


def _review_parent_relationships(duct, core, workspace, data, sheets, generation, candidate,
                                replacement, descriptors, actor, reason):
    events, evidence = _review_relationships(duct, core, workspace, data, sheets, generation,
        candidate, replacement, {'id': replacement['id'], 'kind': 'split_parent'}, descriptors, actor, reason)
    foreign_readings = {reading['id'] for reading in replacement['readings']
        if reading['id'] in consumed_reading_ids(replacement) and
        any(evidence[eid]['source'] != replacement['source'] for eid in reading['evidence_ids'])}
    if foreign_readings != {item['reading_id'] for item in descriptors}:
        duct._fail('duct_parent_relationships', 'Explicitly re-review every resulting foreign reading, including unchanged siblings.')
    return events


def correct_parent_readings(workspace, data, sheets, values, actor, reason):
    """Edit named parent length readings without reconstructing any geometry."""
    duct = _duct()
    core = duct._core()
    duct._exact(values, {'generation_id', 'parent_id', 'parent_fingerprint', 'reading_updates', 'relationships'})
    generation = duct._generation(data, values['generation_id'])
    original, parent, old_fingerprint = _parent_target(duct, core, generation, values['parent_id'], values['parent_fingerprint'])
    updates = core._array(values['reading_updates'], 128, 1)
    descriptors = core._array(values['relationships'], 128)
    replacement = copy.deepcopy(parent)
    by_id = {r['id']: r for r in replacement['readings']}
    seen, old_ids = set(), set()
    for update in updates:
        duct._exact(update, {'reading_id', 'value', 'unit', 'evidence_ids'})
        key = update['reading_id']
        core._plain(key, 160)
        if key in seen or key not in consumed_reading_ids(parent) or by_id[key]['kind'] != 'length':
            duct._fail('duct_parent_reading', 'Update each existing consumed length reading at most once; keep every check identity.')
        if not isinstance(update['value'], str):
            duct._fail('duct_parent_reading', 'Use a positive numeric string for each source length.')
        core._number(update['value'], positive=True)
        core._ids(update['evidence_ids'], minimum=1)
        old_ids.update(by_id[key]['evidence_ids'])
        by_id[key].update({field: copy.deepcopy(update[field]) for field in ('value', 'unit', 'evidence_ids')})
        seen.add(key)
    candidate = copy.deepcopy(generation)
    # A correction explicitly reopens coverage before projecting current sources.
    candidate['coverage'] = duct._coverage(candidate['observations'], candidate['current_sources'],
        ['Parent reading correction needs renewed source coverage review.'])
    record = next(r for r in candidate['planar_splits'] if r['id'] == original['id'])
    record['parent'] = replacement
    new_ids = {eid for reading in replacement['readings'] for eid in reading['evidence_ids']}
    known = duct._known_evidence(generation)
    protected = new_ids | target_only_ids(replacement)
    protected.update(eid for eid in parent['evidence_ids'] if eid in known and
                     known[eid]['source'] == parent['source'] and known[eid]['text'] is None)
    # Keep this parent's structural obligations. Other targets, admissions and
    # coverage retain their own references through the global usage projection;
    # their sharing a capture cannot make it an unused foreign ref here.
    protected.update(record['evidence_ids'])
    protected.update(eid for cut in record['cuts'] for eid in cut['evidence_ids'])
    replacement['evidence_ids'] = [eid for eid in parent['evidence_ids'] if eid not in old_ids or eid in protected]
    replacement['evidence_ids'].extend(sorted(new_ids - set(replacement['evidence_ids'])))
    new_fingerprint, member_update = _replace_parent(duct, core, candidate, original, replacement, old_fingerprint)
    events = _review_parent_relationships(duct, core, workspace, data, sheets, generation,
        candidate, replacement, descriptors, actor, reason)
    result = duct._retain(workspace, data, sheets, candidate, 'parent_readings_correction', actor, reason, [parent['id']])
    for event in events:
        event['replacement_generation_id'] = result['id']
        data['duct_reading_relationship_events'].append(event)
    data['duct_parent_reading_events'].append({'id': uuid.uuid4().hex, 'kind': 'parent_readings_corrected',
        'parent_id': parent['id'], 'before_fingerprint': old_fingerprint, 'after_fingerprint': new_fingerprint,
        'before': copy.deepcopy(parent), 'after': copy.deepcopy(replacement), 'reading_updates': copy.deepcopy(updates),
        'relationship_ids': [event['relationship_id'] for event in events],
        'relationship_event_ids': [event['id'] for event in events], 'enclosing_member_update': member_update,
        'base_generation_id': generation['id'], 'replacement_generation_id': result['id'],
        'actor': actor, 'reason': reason, 'at': datetime.now(timezone.utc).isoformat()})
    return result


def _retained_graphic(duct, workspace, generation, identifier):
    """Read a citation's pinned role without requiring its current availability.

    This permits withdrawal of stale support, never grants it current authority,
    and does not bypass the separate frozen-input checks on historical export.
    """
    found = []
    for reference in generation.get('user_evidence_refs', []):
        record = reference['record']
        if record['id'] != identifier:
            continue
        if (duct._digest(record) != reference['sha256'] or record['evidence']['id'] != identifier or
                record['project_id'] != workspace.metadata['project']['id']):
            duct._fail('duct_parent_graphic_metadata', 'The retained graphic citation metadata changed.')
        found.append(record['evidence'])
    if found:
        if any(item != found[0] for item in found):
            duct._fail('duct_parent_graphic_metadata', 'Retained graphic citations have conflicting metadata.')
        return found[0]
    for reference in generation['source_records']:
        # Only inspect producers whose pinned saved context contains this ID.
        if not any(e['id'] == identifier for key in reference['sheet_ids']
                   for e in generation['current_sources'].get(key, {}).get('evidence', [])):
            continue
        record = duct._producer(workspace, reference['id'])
        if duct._digest(record) != reference['sha256']:
            duct._fail('duct_producer_changed', 'The retained duct source record changed.')
        found.extend(e for key in reference['sheet_ids'] for e in record['current_sources'][key]['evidence']
                     if e['id'] == identifier)
    if not found or any(item != found[0] for item in found):
        duct._fail('duct_parent_graphic_metadata', 'Resolve the exact retained graphic citation before removing it.')
    return found[0]


def correct_parent_graphics(workspace, data, sheets, values, actor, reason):
    """Change only a retained parent's explicit graphic citations."""
    duct = _duct()
    core = duct._core()
    duct._exact(values, {'generation_id', 'parent_id', 'parent_fingerprint', 'add_evidence_ids',
                         'remove_evidence_ids', 'relationships'})
    generation = duct._generation(data, values['generation_id'])
    original, parent, old_fingerprint = _parent_target(duct, core, generation, values['parent_id'], values['parent_fingerprint'])
    added, removed = values['add_evidence_ids'], values['remove_evidence_ids']
    core._ids(added)
    core._ids(removed)
    descriptors = core._array(values['relationships'], 128)
    if (not added and not removed or set(added) & set(removed) or
            set(added) & set(parent['evidence_ids']) or set(removed) - set(parent['evidence_ids'])):
        duct._fail('duct_parent_graphic_change', 'Choose an effective, disjoint addition or removal of current parent citations.')
    protected = target_only_ids(parent) | set(original['evidence_ids'])
    protected.update(eid for reading in parent['readings'] for eid in reading['evidence_ids'])
    protected.update(eid for cut in original['cuts'] for eid in cut['evidence_ids'])
    if set(removed) & protected:
        duct._fail('duct_parent_graphic_role', 'This parent still uses the selected citation for a preserved reading, group, support or boundary.')
    for identifier in removed:
        evidence = _retained_graphic(duct, workspace, generation, identifier)
        if evidence['source'] != parent['source'] or evidence['text'] is not None:
            duct._fail('duct_parent_graphic_role', 'Remove only known same-source null-text parent citations.')
    candidate, replacement = copy.deepcopy(generation), copy.deepcopy(parent)
    replacement['evidence_ids'] = [eid for eid in parent['evidence_ids'] if eid not in removed] + sorted(added)
    new_fingerprint, member_update = _replace_parent(duct, core, candidate, original, replacement, old_fingerprint)
    candidate['coverage'] = duct._coverage(candidate['observations'], candidate['current_sources'],
        ['Parent graphic correction needs renewed source coverage review.'])
    evidence = duct._attach_evidence(workspace, data, sheets, candidate, added)
    if any(evidence[eid]['source'] != parent['source'] or evidence[eid]['text'] is not None for eid in added):
        duct._fail('duct_parent_graphic_role', 'Add current graphic evidence on the exact retained parent source.')
    events = _review_parent_relationships(duct, core, workspace, data, sheets, generation,
        candidate, replacement, descriptors, actor, reason)
    result = duct._retain(workspace, data, sheets, candidate, 'parent_graphics_correction', actor, reason, [parent['id']])
    for event in events:
        event['replacement_generation_id'] = result['id']
        data['duct_reading_relationship_events'].append(event)
    data['duct_parent_graphic_events'].append({'id': uuid.uuid4().hex, 'kind': 'parent_graphics_corrected',
        'parent_id': parent['id'], 'before_fingerprint': old_fingerprint, 'after_fingerprint': new_fingerprint,
        'before': copy.deepcopy(parent), 'after': copy.deepcopy(replacement),
        'add_evidence_ids': copy.deepcopy(added), 'remove_evidence_ids': copy.deepcopy(removed),
        'relationship_ids': [event['relationship_id'] for event in events],
        'relationship_event_ids': [event['id'] for event in events], 'enclosing_member_update': member_update,
        'base_generation_id': generation['id'], 'replacement_generation_id': result['id'],
        'actor': actor, 'reason': reason, 'at': datetime.now(timezone.utc).isoformat()})
    return result


def withdraw(workspace, data, sheets, values, actor, reason):
    duct = _duct()
    duct._exact(values, {'generation_id', 'relationship_id', 'relationship_fingerprint', 'supersedes'})
    generation = duct._generation(data, values['generation_id'])
    prior = next((r for r in generation.get('reading_relationships', []) if r['id'] == values['relationship_id']), None)
    if (prior is None or duct._digest(prior) != values['relationship_fingerprint'] or
            values['supersedes'] != _latest(data, prior['observation_id'], prior['reading_id'])):
        duct._fail('duct_reading_decision_stale', 'The reading relationship changed. Refresh before withdrawing it.')
    if prior['state'] == 'withdrawn':
        duct._fail('duct_reading_withdrawn', 'This reading relationship is already withdrawn.')
    candidate = copy.deepcopy(generation)
    record = dict(copy.deepcopy(prior), state='withdrawn')
    candidate['reading_relationships'] = [record if r['id'] == record['id'] else r for r in candidate['reading_relationships']]
    result = duct._retain(workspace, data, sheets, candidate, 'reading_relationship_withdrawal', actor, reason, [record['observation_id']])
    target = next((t for t in targets(generation, duct._core()) if t['id'] == record['observation_id']), None)
    data['duct_reading_relationship_events'].append({'id': uuid.uuid4().hex, 'kind': 'withdrawn',
        'relationship_id': record['id'], 'target_kind': target['kind'] if target else None,
        'target_id': record['observation_id'], 'reading_id': record['reading_id'], 'supersedes': values['supersedes'],
        'before': copy.deepcopy(prior), 'after': copy.deepcopy(record), 'actor': actor, 'reason': reason,
        'base_generation_id': generation['id'], 'replacement_generation_id': result['id'],
        'at': datetime.now(timezone.utc).isoformat(),
        'source_assignments': {label: copy.deepcopy(duct._assignment(data, record[label])) for label in ('observation_source', 'reading_source')}})
    return result


def view(workspace, data, sheets, generation):
    duct, entries = _duct(), []
    core = duct._core()
    current_targets = targets(generation, core)
    sources, unused = duct._current_sources(workspace, data, sheets, generation)
    geometries = {key: context['geometry'] for key, context in sources.items()}
    evidence = {e['id']: e for context in sources.values() for e in context['evidence']}
    for record in generation.get('reading_relationships', []):
        target = next((t for t in current_targets if t['id'] == record['observation_id']), None)
        if (target is None or record['reading_id'] not in target['consumed_reading_ids'] or
                record['observation_fingerprint'] != target['fingerprint'] or
                record['reading_fingerprint'] != core.fingerprint(next(r for r in target['observation']['readings'] if r['id'] == record['reading_id']))):
            issues = ['reading_relationship_stale']
        else:
            issues, unused, unused = applicability(core, target['observation'],
                generation.get('reading_relationships', []), geometries, evidence, only_reading_id=record['reading_id'])
        validity = ('withdrawn' if record['state'] == 'withdrawn' else 'current' if not issues else
                    'stale' if 'reading_relationship_stale' in issues else 'unresolved')
        entries.append({'id': record['id'], 'fingerprint': core.fingerprint(record), 'record': copy.deepcopy(record),
            'target_kind': target['kind'] if target else None, 'target_id': record['observation_id'],
            'reading_id': record['reading_id'], 'validity': validity, 'issues': issues,
            'latest_event_id': _latest(data, record['observation_id'], record['reading_id'])})
    return {'reading_targets': current_targets, 'reading_relationships': entries,
        'reading_relationship_events': copy.deepcopy(data['duct_reading_relationship_events'])}
