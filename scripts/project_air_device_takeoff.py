"""Append-only, source-bound air-device drafts for the workflow's atomic writer.

Only the pure count kernel determines quantities. Model relations remain proposed
until reviewed; repeated readings are staged rather than accumulated. No scale,
renderer or model execution is part of viewing/reopening/exporting saved drafts.
"""
import copy
import csv
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import uuid

VERSION = 'project-air-device-takeoff-1'
SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
KINDS = {'correspondence': 'correspondences', 'multiplicity': 'multiplicities',
         'relocation': 'relocations', 'schedule': 'schedules'}
ROLES = {'plan', 'enlarged_plan', 'detail', 'legend', 'schedule', 'specification', 'catalog', 'other'}
ELIGIBLE = {'plan', 'enlarged_plan', 'detail'}
_FIELDS = ('air_device_generations', 'air_device_events', 'air_device_readings')
_CACHE = {}


def _load(name):
    path = Path(__file__).with_name(name + '.py')
    raw = path.read_bytes()
    signature = hashlib.sha256(raw).hexdigest()
    if name == 'air_device_calculation':
        signature = _digest([signature] + [hashlib.sha256(Path(__file__).with_name(n + '.py').read_bytes()).hexdigest()
            for n in ('air_device_attributes', 'air_device_rules')])
    if name not in _CACHE or _CACHE[name][0] != signature:
        spec = importlib.util.spec_from_file_location('project_air_' + name, path)
        module = importlib.util.module_from_spec(spec)
        exec(compile(raw, str(path), 'exec'), module.__dict__)
        _CACHE[name] = (signature, module)
    return _CACHE[name][1]


class ProjectAirDeviceError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _fail(code, message):
    raise ProjectAirDeviceError(code, message)


def _packed(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def _digest(value):
    return hashlib.sha256(_packed(value)).hexdigest()


def _core():
    return _load('air_device_calculation')


def _implementation():
    return {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ('project_air_device_takeoff.py', 'air_device_calculation.py',
                     'air_device_attributes.py', 'air_device_rules.py')}


def _exact(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        _fail('air_device_fields', 'The air-device action has missing or unexpected fields.')


def _text(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 1000 or
            any(ord(c) < 32 or 127 <= ord(c) < 160 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        _fail('air_device_text', 'Supply a bounded actor and source-review reason.')


def initialize(data):
    for field in _FIELDS:
        data.setdefault(field, [])
    data.setdefault('air_device_current', None)


def _seal(record):
    record['sha256'] = _digest(record)
    return record


def _verify_seal(record):
    if not isinstance(record, dict) or record.get('sha256') != _digest({k: v for k, v in record.items() if k != 'sha256'}):
        _fail('air_device_integrity', 'Saved air-device state failed integrity verification.')


def _saved(data):
    entries = data.get('air_device_generations', [])
    selected = next((g for g in entries if g['id'] == data.get('air_device_current')), None)
    if data.get('air_device_current') is not None and selected is None:
        _fail('air_device_integrity', 'The selected air-device generation is missing.')
    return selected


def _verify(data):
    previous = None
    identifiers = set()
    for g in data.get('air_device_generations', []):
        _verify_seal(g)
        if g['id'] in identifiers or g['previous_sha256'] != previous:
            _fail('air_device_integrity', 'Air-device generation lineage changed.')
        identifiers.add(g['id'])
        previous = g['sha256']
        if g['implementation'] == _implementation() and _core().calculate(g['request']) != g['result']:
            _fail('air_device_integrity', 'Saved air-device result does not replay its exact request.')
    previous = None
    for event in data.get('air_device_events', []):
        _verify_seal(event)
        if event['previous_sha256'] != previous:
            _fail('air_device_integrity', 'Air-device review history changed.')
        previous = event['sha256']
    seen = set()
    for entry in data.get('air_device_readings', []):
        _verify_seal(entry)
        if entry['reading_id'] in seen or entry['reading_sha256'] != _digest(entry['record']):
            _fail('air_device_integrity', 'Retained air-device reading changed.')
        seen.add(entry['reading_id'])
    _saved(data)


def _event(data, action, values, actor, reason):
    events = data['air_device_events']
    events.append(_seal({'id': uuid.uuid4().hex, 'at': datetime.now(timezone.utc).isoformat(),
        'action': action, 'values': copy.deepcopy(values), 'actor': actor, 'reason': reason,
        'generation': data['air_device_current'], 'previous_sha256': events[-1]['sha256'] if events else None}))


def _page(source):
    return source['revision_id'] + ':' + str(source['index'])


def _producer(workspace):
    producer = getattr(workspace, 'air_device_producer', None)
    if producer is None:
        _fail('air_device_producer_unavailable', 'The saved air-device source reader is unavailable.')
    return producer


def _historical_record(workspace, entry, files=None):
    """Byte verification against a digest retained after verified admission.

    Current source membership/geometry is intentionally checked separately. This
    preserves old originals in history when a current source changes or retires.
    """
    producer = _producer(workspace)
    record = producer.result(entry['reading_id'], verify=False)
    if record != entry['record'] or _digest(record) != entry['reading_sha256']:
        _fail('air_device_source_changed', 'A retained air-device reading changed after admission.')
    prepared = record['input']
    if producer.inputs._read('inputs', prepared['id']) != prepared:
        _fail('air_device_source_changed', 'Retained air-device image input changed.')
    if producer._input_geometry_binding(prepared, record['geometry'], establish=False) != record['input_geometry_binding']:
        _fail('air_device_source_changed', 'Retained image geometry proof changed.')
    raw = producer.artifact_bytes(record['response'])
    image = producer.inputs.knowledge.source_bytes(prepared['source_id'])
    if files is not None:
        prefix = 'air-device-sources/' + record['id'] + '/'
        files[prefix + 'producer.json'] = _packed(record)
        files[prefix + 'response.json'] = raw
        files[prefix + 'page.png'] = image
        files[prefix + 'input.json'] = _packed(prepared)
        files[prefix + 'geometry.json'] = _packed(record['input_geometry_binding'])
    return record



def _relevant_sources(draft):
    selected = set(draft['scope']['source_keys'])
    observations = {o['id']: o for o in draft['observations']}
    members = {o['id'] for o in observations.values() if _digest(o['source']) in selected}
    records = [r for name in KINDS.values() for r in draft[name]]
    records += [p['record'] for name in KINDS.values() for p in draft['proposals'][name] if p['state'] == 'pending']
    while True:
        expanded = members | {m['observation_id'] for r in records
            if members & {m['observation_id'] for m in r.get('members', [])} for m in r.get('members', [])}
        if expanded == members:
            break
        members = expanded
    evidence_ids = {eid for identifier in members for eid in observations[identifier]['evidence_ids']}
    for record in records:
        if not record.get('members') or members & {m['observation_id'] for m in record['members']}:
            evidence_ids.update(record['evidence_ids'])
    selected.update(_digest(observations[i]['source']) for i in members)
    selected.update(_digest(e['source']) for e in draft['evidence'] if e['id'] in evidence_ids)
    return selected, members


def _contexts(workspace, data, sheets, draft):
    current, displays, problems = [], [], []
    retained = {e['reading_id']: e for e in data['air_device_readings']}
    for identifier in draft['reading_ids']:
        entry = retained[identifier]
        record = entry['record']
        source = record['source']
        role = record['role'] if record['role'] in ROLES else 'other'
        context = {'source': copy.deepcopy(source), 'role': role, 'artifact_sha256': record['input']['input_sha256']}
        issues = []
        try:
            _historical_record(workspace, entry)
            _producer(workspace).result(identifier, verify=True)
            sheet = sheets.get(_page(source))
            if sheet is None or sheet.get('sheet_id') != source['sheet_id']:
                _fail('air_device_source_missing', 'The saved source page is missing.')
            geometry = _load('sheet_geometry').sheet_geometry(sheet, source['revision_id'])
            if geometry['fingerprint'] != source['geometry_fingerprint']:
                _fail('air_device_geometry_changed', 'The source crop or coordinates changed; review a current reading.')
            if data.get('pages', {}).get(_page(source), {}).get('role') != record['role']:
                _fail('air_device_role_changed', 'The source role changed; review a current reading.')
        except Exception as error:
            if not isinstance(error, (ValueError, OSError, KeyError)) and not (hasattr(error, 'code') and hasattr(error, 'message')):
                raise
            issues.append({'code': getattr(error, 'code', 'air_device_source_changed'),
                'message': getattr(error, 'message', str(error)), 'source': copy.deepcopy(source)})
        if not issues:
            current.append(context)
        displays.append(dict(context, key=_digest(source), original_role=record['role'], current=not issues, issues=issues))
        problems.extend(issues)
    # Multiple jobs on one source cannot enter an active draft; intake enforces this.
    relevant, unused = _relevant_sources(draft)
    return ([c for c in current if _digest(c['source']) in relevant], displays,
            [p for p in problems if _digest(p['source']) in relevant])


def _blank():
    return {'reading_ids': [], 'observations': [], 'evidence': [], 'decisions': [],
        'correspondences': [], 'multiplicities': [], 'relocations': [], 'schedules': [],
        'proposals': {name: [] for name in KINDS.values()}, 'scope': None,
        'coverage': {'state': 'unknown', 'basis_sha256': None, 'evidence_ids': [], 'unresolved_requirements': []}}


def _admission(observation, evidence):
    physical = observation['depiction'] == 'physical' and any(e['kind'] == 'graphic' and
        e['source'] == observation['source'] and e['bbox'] == observation['bbox']
        for e in evidence if e['id'] in observation['evidence_ids'])
    family = observation['attributes']['family']
    supported = family['state'] == 'known' and family['value'] in {
        'diffuser', 'register', 'grille', 'linear_diffuser', 'mechanical_louver'}
    return {'observation_id': observation['id'], 'observation_sha256': _core().fingerprint(observation),
        'action': 'include' if physical and supported else 'unresolved',
        'reason': 'Source validator: physical graphic and supported air-device family.' if physical and supported else
            'Source proposal needs review; source/class support is unresolved.',
        'evidence_ids': copy.deepcopy(observation['evidence_ids'])}


def _add_reading(draft, record):
    draft['reading_ids'].append(record['id'])
    observations = [_core().validate_observation(o) for o in record['observations']]
    draft['observations'].extend(observations)
    draft['evidence'].extend(copy.deepcopy(record['evidence']))
    draft['decisions'].extend(_admission(o, record['evidence']) for o in observations)
    for name in KINDS.values():
        key = 'schedule_declarations' if name == 'schedules' else 'proposed_' + name
        for proposal in record[key]:
            normalized = copy.deepcopy(proposal)
            if name != 'schedules':
                canonical = {o['id']: o for o in observations}
                normalized['members'] = [{'observation_id': m['observation_id'],
                    'observation_sha256': _core().fingerprint(canonical[m['observation_id']])}
                    for m in proposal['members']]
            draft['proposals'][name].append({'id': proposal['id'], 'sha256': _digest(normalized),
                'record': normalized, 'state': 'pending', 'source_proposal_sha256': _digest(proposal)})
    source_key = _digest(record['source'])
    if draft['scope'] is None:
        if record['role'] not in ELIGIBLE:
            _fail('air_device_scope_source', 'Prepare an eligible plan or detail before adding context readings.')
        draft['scope'] = {'id': 'air-device-scope-' + uuid.uuid4().hex, 'version': 1,
            'source_keys': [source_key], 'group_by': ['family', 'type_tag', 'work_status'],
            'required_fields': ['family', 'work_status'], 'work_statuses': ['new_install']}
    elif record['role'] in ELIGIBLE and source_key not in draft['scope']['source_keys']:
        draft['scope']['source_keys'].append(source_key)
        draft['scope']['version'] += 1


def _pending(data):
    dispositions = {e['values']['reading_id']: e['values']['action'] for e in data['air_device_events']
        if e['action'] == 'air_device_intake'}
    active = {identifier for g in data['air_device_generations'] for identifier in g['draft']['reading_ids']}
    return [e for e in data['air_device_readings'] if e['reading_id'] not in active and e['reading_id'] not in dispositions]


def _request(workspace, data, sheets, draft, block_ids=(), block_all=False):
    sources, displays, problems = _contexts(workspace, data, sheets, draft)
    blocked = set(block_ids)
    requirements = []
    selected = set(draft['scope']['source_keys'])
    exclusions = {d['observation_id'] for d in draft['decisions'] if d['action'] == 'exclude'}
    for observation in draft['observations']:
        if _digest(observation['source']) in selected and observation['id'] not in exclusions:
            requirements.extend('Reader issue ' + observation['id'] + ': ' + issue for issue in observation['issues'])
    for name, records in draft['proposals'].items():
        for proposal in records:
            if proposal['state'] == 'pending':
                requirements.append('Unreviewed ' + name + ' proposal ' + proposal['id'])
                if name != 'schedules':
                    blocked.update(m['observation_id'] for m in proposal['record']['members'])
    if any(e['reading_id'] not in draft['reading_ids'] for e in _pending(data)):
        requirements.append('A later air-device source reading awaits intake review.')
    decisions = copy.deepcopy(draft['decisions'])
    for decision in decisions:
        if block_all or decision['observation_id'] in blocked:
            decision.update(action='unresolved', reason='Review pending or saved calculation dependency changed.')
    coverage = copy.deepcopy(draft['coverage'])
    coverage['unresolved_requirements'] = sorted(set(coverage['unresolved_requirements'] + requirements))
    request = dict(schema='air-device-calculation-request-1', binding=_load('air_device_rules').load_binding(),
        scope=copy.deepcopy(draft['scope']), sources=[] if block_all else sources,
        evidence=copy.deepcopy(draft['evidence']), observations=copy.deepcopy(draft['observations']),
        decisions=decisions, coverage=coverage)
    for name in KINDS.values():
        request[name] = copy.deepcopy(draft[name])
    return request, displays, problems


def _retain(workspace, data, sheets, draft, action, actor, reason):
    request, unused, unused_problems = _request(workspace, data, sheets, draft)
    result = _core().calculate(request)
    previous = _saved(data)
    record = _seal({'id': uuid.uuid4().hex, 'previous_generation': previous['id'] if previous else None,
        'previous_sha256': data['air_device_generations'][-1]['sha256'] if data['air_device_generations'] else None,
        'at': datetime.now(timezone.utc).isoformat(), 'actor': actor, 'reason': reason, 'action': action,
        'implementation': _implementation(), 'draft': copy.deepcopy(draft), 'request': request, 'result': result})
    data['air_device_generations'].append(record)
    data['air_device_current'] = record['id']
    return record


def _generation(data, values):
    generation = _saved(data)
    if generation is None or values.get('generation') != generation['id']:
        _fail('air_device_stale_generation', 'The air-device generation changed; refresh before saving.')
    return generation


def _observation(draft, values):
    found = next((o for o in draft['observations'] if o['id'] == values['observation_id']), None)
    if found is None or _core().fingerprint(found) != values['observation_sha256']:
        _fail('air_device_stale_observation', 'The selected air-device observation changed.')
    return found


def _set_decision(draft, observation, action, reason):
    old = next(d for d in draft['decisions'] if d['observation_id'] == observation['id'])
    old.update(observation_sha256=_core().fingerprint(observation), action=action, reason=reason,
        evidence_ids=copy.deepcopy(observation['evidence_ids']))


def _relation(draft, name, record):
    old = next((r for r in draft[name] if r['id'] == record['id']), None)
    if old is not None:
        draft[name].remove(old)
    draft[name].append(copy.deepcopy(record))


def _apply(workspace, data, sheets, action, values, actor, reason):
    if 'air_devices' not in data.get('project', {}).get('scopes', []):
        _fail('air_device_scope_excluded', 'Include air devices in project scope before calculating.')
    if action == 'air_device_prepare':
        _exact(values, {'reading_id'})
        if any(e['reading_id'] == values['reading_id'] for e in data['air_device_readings']):
            return False
        record = _producer(workspace).result(values['reading_id'], verify=True)
        if record['state'] != 'completed' or record['project_id'] != workspace.metadata['project']['id']:
            _fail('air_device_reading_unavailable', 'Select a completed air-device reading from this project.')
        entry = _seal({'reading_id': record['id'], 'reading_sha256': _digest(record),
            'record': record, 'actor': actor, 'reason': reason})
        data['air_device_readings'].append(entry)
        if _saved(data) is None and record['role'] in ELIGIBLE:
            draft = _blank()
            _add_reading(draft, record)
            _retain(workspace, data, sheets, draft, action, actor, reason)
        return True
    generation = _generation(data, values)
    draft = copy.deepcopy(generation['draft'])
    if action == 'air_device_intake':
        _exact(values, {'generation', 'reading_id', 'reading_sha256', 'action'})
        entry = next((e for e in _pending(data) if e['reading_id'] == values['reading_id']), None)
        if entry is None or entry['reading_sha256'] != values['reading_sha256']:
            _fail('air_device_stale_reading', 'The selected pending reading changed or was already reviewed.')
        if values['action'] == 'discard':
            return True
        if values['action'] not in ('add', 'replace'):
            _fail('air_device_intake_action', 'Choose add, replace or discard.')
        record = _producer(workspace).result(entry['reading_id'], verify=True)
        if record != entry['record']:
            _fail('air_device_source_changed', 'The staged reading changed.')
        same = [e for e in data['air_device_readings'] if e['reading_id'] in draft['reading_ids']
            and _page(e['record']['source']) == _page(record['source'])]
        if (values['action'] == 'add' and same) or (values['action'] == 'replace' and not same):
            _fail('air_device_intake_overlap', 'Use replacement for the same page; add only distinct pages.')
        if same:
            removed = {o['id'] for o in draft['observations'] if _page(o['source']) == _page(record['source'])}
            old_evidence = {evidence['id'] for entry in same for evidence in entry['record']['evidence']}
            for name in KINDS.values():
                for relation in draft[name]:
                    ids = {m['observation_id'] for m in relation['members']}
                    if ((ids & removed or old_evidence & set(relation['evidence_ids'])) and ids - removed and name != 'schedules'):
                        _fail('air_device_relationship_closure', 'Review or remove the cross-page relationship before replacing its source.')
                draft[name] = [r for r in draft[name] if not (removed & {m['observation_id'] for m in r['members']} or
                    old_evidence & set(r['evidence_ids']))]
                draft['proposals'][name] = [p for p in draft['proposals'][name]
                    if p['id'] not in {r['id'] for e in same for r in e['record'][
                        'schedule_declarations' if name == 'schedules' else 'proposed_' + name]}]
            old_sources = {_digest(e['record']['source']) for e in same}
            draft['scope']['source_keys'] = [k for k in draft['scope']['source_keys'] if k not in old_sources]
            draft['scope']['version'] += 1
            draft['reading_ids'] = [i for i in draft['reading_ids'] if i not in {e['reading_id'] for e in same}]
            draft['observations'] = [o for o in draft['observations'] if o['id'] not in removed]
            draft['decisions'] = [d for d in draft['decisions'] if d['observation_id'] not in removed]
        _add_reading(draft, record)
    elif action == 'air_device_review':
        _exact(values, {'generation', 'observation_id', 'observation_sha256', 'action'})
        if values['action'] not in ('include', 'exclude', 'unresolved'):
            _fail('air_device_review_action', 'Choose include, exclude or unresolved.')
        _set_decision(draft, _observation(draft, values), values['action'], reason)
    elif action == 'air_device_correct':
        _exact(values, {'generation', 'observation_id', 'observation_sha256', 'attributes', 'depiction'} | ({'issues'} if 'issues' in values else set()))
        observation = _observation(draft, values)
        if 'issues' in values:
            if (not isinstance(values['issues'], list) or any(not isinstance(v, str) for v in values['issues']) or
                    len(set(values['issues'])) != len(values['issues']) or not set(values['issues']) <= set(observation['issues'])):
                _fail('air_device_issue_resolution', 'Explicit issue resolution may only remove existing source issues.')
            observation['issues'] = copy.deepcopy(values['issues'])
        observation.update(attributes=copy.deepcopy(values['attributes']), depiction=values['depiction'])
        if isinstance(values['attributes'], dict):
            refs = {eid for attribute in values['attributes'].values() if isinstance(attribute, dict)
                    for eid in attribute.get('evidence_ids', [])}
            known = {e['id'] for e in draft['evidence']}
            if not refs <= known:
                _fail('air_device_evidence_missing', 'Corrected attributes must cite retained source evidence.')
            observation['evidence_ids'] = sorted(set(observation['evidence_ids']) | refs)
        canonical = _core().validate_observation(observation)
        observation.clear()
        observation.update(canonical)
        # A correction may cite existing source evidence; it cannot manufacture it.
        _set_decision(draft, observation, 'include', reason)
    elif action == 'air_device_scope':
        _exact(values, {'generation', 'source_keys', 'group_by', 'required_fields', 'work_statuses'})
        draft['scope'].update({k: copy.deepcopy(values[k]) for k in ('source_keys', 'group_by', 'required_fields', 'work_statuses')})
        draft['scope']['version'] += 1
    elif action == 'air_device_coverage':
        _exact(values, {'generation', 'state'})
        if values['state'] not in ('complete', 'partial', 'unknown'):
            _fail('air_device_coverage_state', 'Choose complete, partial or unknown.')
        request, displays, problems = _request(workspace, data, sheets, draft)
        selected = set(draft['scope']['source_keys'])
        current = {s['key']: s for s in displays if s['current']}
        if values['state'] == 'complete' and not selected <= set(current):
            _fail('air_device_coverage_stale', 'Every selected source must be current before complete coverage review.')
        witness_ids = []
        for key in sorted(selected & set(current)):
            context = current[key]
            eid = 'air_device_scope_witness_' + _digest({'source': context['source'], 'artifact': context['artifact_sha256']})
            if not any(e['id'] == eid for e in draft['evidence']):
                draft['evidence'].append({'id': eid, 'source': copy.deepcopy(context['source']),
                    'artifact_sha256': context['artifact_sha256'], 'bbox': [0, 0, 1, 1], 'kind': 'graphic', 'text': None})
            witness_ids.append(eid)
        draft['coverage'] = {'state': values['state'], 'basis_sha256': None,
            'evidence_ids': witness_ids, 'unresolved_requirements': []}
        request, unused, unused2 = _request(workspace, data, sheets, draft)
        draft['coverage']['basis_sha256'] = _core().coverage_basis(request)
    elif action == 'air_device_recalculate':
        _exact(values, {'generation'})
    elif action == 'air_device_proposal':
        _exact(values, {'generation', 'proposal_id', 'proposal_sha256', 'action', 'member_ids'})
        found = [(name, p) for name, entries in draft['proposals'].items() for p in entries if p['id'] == values['proposal_id']]
        if len(found) != 1 or found[0][1]['sha256'] != values['proposal_sha256'] or found[0][1]['state'] != 'pending':
            _fail('air_device_stale_proposal', 'The proposed relationship changed or was already reviewed.')
        name, proposal = found[0]
        if values['action'] not in ('accept', 'reject') or not isinstance(values['member_ids'], list):
            _fail('air_device_proposal_action', 'Accept or reject the retained proposal.')
        if name != 'schedules' and values['member_ids']:
            _fail('air_device_proposal_members', 'Retain the exact proposed members or create a separately reviewed relationship.')
        if values['action'] == 'accept':
            record = copy.deepcopy(proposal['record'])
            if name == 'schedules':
                lookup = {o['id']: o for o in draft['observations']}
                if not values['member_ids'] or len(set(values['member_ids'])) != len(values['member_ids']) or any(i not in lookup for i in values['member_ids']):
                    _fail('air_device_schedule_members', 'Select exact observed members for the schedule declaration.')
                record = {k: record[k] for k in ('id', 'declared_each', 'attributes', 'evidence_ids')}
                record['members'] = [{'observation_id': i, 'observation_sha256': _core().fingerprint(lookup[i])} for i in values['member_ids']]
            _relation(draft, name, record)
        proposal['state'] = 'accepted' if values['action'] == 'accept' else 'rejected'
    elif action == 'air_device_relation':
        _exact(values, {'generation', 'kind', 'relation_id', 'relation_sha256', 'action', 'record'})
        if values['kind'] not in KINDS or values['action'] not in ('save', 'remove'):
            _fail('air_device_relation_action', 'Choose a supported relationship kind and save or remove.')
        name = KINDS[values['kind']]
        old = next((r for r in draft[name] if r['id'] == values['relation_id']), None)
        if values['relation_sha256'] != (_digest(old) if old else None):
            _fail('air_device_stale_relationship', 'The selected relationship changed.')
        if values['action'] == 'remove':
            if old is None or values['record'] is not None:
                _fail('air_device_relation_action', 'Select an existing relationship to remove.')
            draft[name].remove(old)
            for member in old['members']:
                obs = next(o for o in draft['observations'] if o['id'] == member['observation_id'])
                _set_decision(draft, obs, 'unresolved', reason)
        else:
            if not isinstance(values['record'], dict) or values['record'].get('id') != values['relation_id']:
                _fail('air_device_relation_identity', 'The replacement relationship must keep its explicit identity.')
            if old is not None:
                remaining = {m['observation_id'] for m in values['record'].get('members', [])}
                for member in old['members']:
                    if member['observation_id'] not in remaining:
                        observation = next(o for o in draft['observations'] if o['id'] == member['observation_id'])
                        _set_decision(draft, observation, 'unresolved', reason)
            _relation(draft, name, values['record'])
    else:
        _fail('air_device_action', 'Unknown air-device action.')
    if action != 'air_device_coverage':
        draft['coverage'].update(state='unknown', basis_sha256=None)
    _retain(workspace, data, sheets, draft, action, actor, reason)
    return True


def apply(workspace, data, sheets, action, values, actor, reason):
    """Mutate only on complete success; workflow persists the resulting candidate."""
    _text(actor)
    _text(reason)
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != SOURCE_SHA256:
        _fail('air_device_implementation_changed', 'Reload the air-device adapter before saving after a code update.')
    candidate = copy.deepcopy(data)
    initialize(candidate)
    _verify(candidate)
    _core().fingerprint(values)
    if any(e['action'] == action and e['values'] == values and e['actor'] == actor and e['reason'] == reason
           for e in candidate['air_device_events']):
        return data.get('air_device_current')
    try:
        changed = _apply(workspace, candidate, sheets, action, values, actor, reason)
        if changed:
            _event(candidate, action, values, actor, reason)
            data.update({field: candidate[field] for field in _FIELDS + ('air_device_current',)})
    except ProjectAirDeviceError:
        raise
    except (ValueError, KeyError, TypeError, OSError) as error:
        _fail(getattr(error, 'code', 'air_device_invalid'), getattr(error, 'message', str(error)))
    return data.get('air_device_current')


def view(workspace, data, sheets):
    _verify(data)
    generation = _saved(data)
    output = {'available': generation is not None, 'generation': data.get('air_device_current'), 'fingerprint': None,
        'stale': False, 'result': None, 'scope': None, 'sources': [], 'observations': [], 'evidence': [],
        'prepared_reading_ids': [r['reading_id'] for r in data.get('air_device_readings', [])],
        'coverage': None, 'history': copy.deepcopy(data.get('air_device_generations', [])),
        'decisions': copy.deepcopy(data.get('air_device_events', [])), 'proposals': {'readings': []}, 'issues': [],
        'relationships': {name: [] for name in KINDS.values()}}
    if generation is None:
        for entry in _pending(data):
            record = entry['record']
            output['proposals']['readings'].append({'reading_id': entry['reading_id'],
                'reading_sha256': entry['reading_sha256'], 'source': copy.deepcopy(record['source']),
                'role': record['role'], 'state': 'pending', 'same_page': False})
        output['fingerprint'] = _digest(output)
        return output
    draft = generation['draft']
    request, sources, problems = _request(workspace, data, sheets, draft)
    current = _core().calculate(request)
    saved = {r['row_id']: r for r in generation['result']['rows']}
    relevant_sources, relevant_members = _relevant_sources(draft)
    changed = {i for r in current['rows'] if set(r['member_ids']) & relevant_members and
        (r['row_id'] not in saved or r['dependency_sha256'] != saved[r['row_id']]['dependency_sha256'])
        for i in r['member_ids']}
    implementation_changed = generation['implementation'] != _implementation()
    if changed or implementation_changed:
        request, sources, problems = _request(workspace, data, sheets, draft, changed, implementation_changed)
        current = _core().calculate(request)
    output.update(result=current, scope=copy.deepcopy(draft['scope']), sources=sources,
        coverage=copy.deepcopy(draft['coverage']), evidence=copy.deepcopy(draft['evidence']),
        stale=bool(changed or problems or implementation_changed), issues=problems,
        relationships={name: [{'id': r['id'], 'sha256': _digest(r), 'record': copy.deepcopy(r)}
            for r in draft[name]] for name in KINDS.values()})
    if implementation_changed:
        output['issues'].append({'code': 'air_device_implementation_changed',
            'message': 'The count implementation changed; explicitly recalculate this draft.', 'source': None})
    for observation in draft['observations']:
        decision = next(d for d in draft['decisions'] if d['observation_id'] == observation['id'])
        output['observations'].append({'observation': copy.deepcopy(observation),
            'observation_sha256': _core().fingerprint(observation), 'decision': copy.deepcopy(decision)})
    output['proposals'].update(copy.deepcopy(draft['proposals']))
    for entry in _pending(data):
        record = entry['record']
        output['proposals']['readings'].append({'reading_id': entry['reading_id'],
            'reading_sha256': entry['reading_sha256'], 'source': copy.deepcopy(record['source']),
            'role': record['role'], 'state': 'pending',
            'same_page': any(_page(s['source']) == _page(record['source']) for s in sources)})
        output['issues'].append({'code': 'air_device_intake_pending',
            'message': 'A later air-device source reading awaits explicit intake review.', 'source': record['source']})
    for issue in current['issues']:
        output['issues'].append(issue if isinstance(issue, dict) else {'code': str(issue), 'message': str(issue), 'source': None})
    output['fingerprint'] = _digest({k: v for k, v in output.items() if k not in ('fingerprint', 'history', 'decisions')})
    return output


def export_files(workspace, data, sheets):
    current = view(workspace, data, sheets)
    files = {}
    for entry in data.get('air_device_readings', []):
        _historical_record(workspace, entry, files)
    files['air-device-counts.json'] = _packed(current)
    files['air-device-history.json'] = _packed({key: data.get(key) for key in _FIELDS + ('air_device_current',)})
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(['generation', 'state', 'row_id', 'member_ids', 'physical_each', 'requested',
        'remove_each', 'reinstall_each', 'new_purchase_each', 'sources', 'evidence', 'issues'])
    for row in (current['result'] or {}).get('rows', []):
        writer.writerow([current['generation'], 'stale_draft' if current['stale'] else 'current_draft', row['row_id'],
            json.dumps(row['member_ids']), '' if row['physical_each'] is None else row['physical_each'], row['requested'],
            row['operations']['remove'], row['operations']['reinstall'],
            '' if row['new_purchase_each'] is None else row['new_purchase_each'],
            json.dumps([o['observation']['source'] for o in current['observations'] if o['observation']['id'] in row['member_ids']], sort_keys=True),
            json.dumps([e for o in current['observations'] if o['observation']['id'] in row['member_ids'] for e in o['observation']['evidence_ids']]),
            json.dumps(row['issues'])])
    files['air-device-counts.csv'] = stream.getvalue().encode('utf-8')
    return files
