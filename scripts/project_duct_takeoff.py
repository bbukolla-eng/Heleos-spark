"""Source-bound duct drafts owned by the workflow's single atomic writer."""
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import uuid


def _module(name):
    spec = importlib.util.spec_from_file_location('heleos_project_' + name, Path(__file__).with_name(name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


geometry = _module('sheet_geometry')


def _core():
    return _module('duct_calculation')


class ProjectDuctError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _fail(code, message):
    raise ProjectDuctError(code, message)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _observation_fingerprint(value):
    return _core().fingerprint(value)


def _exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        _fail('duct_fields', 'The duct action has missing or unexpected fields.')


def _rule_binding():
    root = Path(__file__).resolve().parent.parent
    raw = (root / 'tests/fixtures/duct-takeoff/2026-09-14-owner-decision.json').read_bytes()
    decision = json.loads(raw)
    if decision.get('decision') != 'approved_for_implementation':
        _fail('duct_rule_unapproved', 'The saved duct calculation basis is not approved.')
    references = []
    for name in ('approved_packet', 'checked_examples'):
        actual = hashlib.sha256((root / decision[name + '_path']).read_bytes()).hexdigest()
        if actual != decision[name + '_sha256']:
            _fail('duct_rule_changed', 'The approved duct rules or examples changed; preserve their decision before recovery.')
        references.append(actual)
    return {'id': 'duct-rules-D01-D10-2026-09-14', 'state': 'approved',
            'fingerprint': _digest([hashlib.sha256(raw).hexdigest(), references])}


def _implementation():
    return _digest({name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                    for name in ('project_duct_takeoff.py', 'project_duct_arc_repartition.py', 'duct_arc_obligations.py', 'project_duct_refresh.py', 'project_duct_revision.py', 'project_duct_revision_checks.py', 'project_duct_reading_links.py', 'duct_calculation.py', 'duct_arc_geometry.py', 'sheet_geometry.py', 'sheet_scale.py')})


def initialize(data):
    data.setdefault('duct_generations', [])
    data.setdefault('duct_current', None)
    data.setdefault('duct_decisions', [])
    data.setdefault('duct_evidence', [])
    data.setdefault('duct_last_capture_id', None)
    data.setdefault('duct_capture_ack', None)
    data.setdefault('duct_refreshes', [])
    data.setdefault('duct_refresh_decisions', [])
    data.setdefault('duct_reading_relationship_events', [])
    data.setdefault('duct_parent_reading_events', [])
    data.setdefault('duct_parent_graphic_events', [])
    data.setdefault('duct_revisions', [])
    data.setdefault('duct_revision_decisions', [])
    data.setdefault('duct_arc_repartition_events', [])
    data.setdefault('duct_revision_check_events', [])


def _saved(data):
    result = next((entry for entry in data['duct_generations'] if entry['id'] == data['duct_current']), None)
    if data['duct_current'] and result is None:
        _fail('duct_generation_missing', 'The current duct generation is missing. Preserve the saved workflow.')
    return result


def _assignment(data, source):
    page = data['pages'].get(source['revision_id'] + ':' + str(source['index']), {})
    return {key: page.get(key) for key in ('role', 'building', 'level')}


def _source_context(data, sheets, source):
    sheet = sheets.get(source['revision_id'] + ':' + str(source['index']))
    if sheet is None or sheet.get('sheet_id') != source['sheet_id']:
        _fail('duct_source_missing', 'The original duct source sheet is unavailable.')
    current = geometry.sheet_geometry(sheet, source['revision_id'])
    if current['fingerprint'] != source['geometry_fingerprint']:
        _fail('duct_geometry_changed', 'The duct source coordinates changed; extract current observations.')
    assignment = _assignment(data, source)
    if assignment['role'] not in ('plan', 'detail', 'riser'):
        _fail('duct_source_role', 'Assign the duct source as a plan, detail or riser before using its observations.')
    return current, assignment


def _producer(workspace, identifier):
    producer = getattr(workspace, 'duct_producer', None)
    if producer is None:
        _fail('duct_producer_unavailable', 'The local duct source reader is unavailable.')
    record = producer.result(identifier, verify=True)
    if record.get('id') != identifier or record.get('project_id') != workspace.metadata['project']['id']:
        _fail('duct_producer_project', 'The producer record does not belong to this project.')
    if record.get('state') != 'completed':
        _fail('duct_producer_incomplete', 'Finish the observation job before calculating its ductwork.')
    return record


def _input_engine(workspace):
    return workspace.workflow.model_inference


def _capture_kind_text(kind, transcription):
    if kind not in ('graphic', 'label', 'dimension', 'elevation', 'datum'):
        _fail('duct_evidence_kind', 'Select the source evidence type.')
    if kind == 'graphic':
        if transcription is not None:
            _fail('duct_evidence_text', 'Capture graphics without a transcription; capture their labels separately.')
    elif (not isinstance(transcription, str) or not transcription.strip() or
          len(transcription) > 4000 or
          any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in transcription)):
        _fail('duct_evidence_text', 'Transcribe this source text using at most 4000 characters.')


def _capture_record(workspace, data, sheets, record, current=True):
    """Verify frozen bytes and their exact original-page binding on every use."""
    if record['project_id'] != workspace.metadata['project']['id']:
        _fail('duct_evidence_project', 'Captured evidence belongs to another project.')
    if current:
        # Current use must satisfy the role convention. Frozen-byte export
        # retains formerly accepted captures even when they are now stale.
        _capture_kind_text(record['kind'], record['evidence']['text'])
    engine = _input_engine(workspace)
    prepared = engine._read('inputs', record['input']['id'])
    if prepared != record['input']:
        _fail('duct_evidence_changed', 'The frozen duct evidence input changed.')
    engine._lineage(prepared)
    evidence, geom = record['evidence'], record['geometry']
    expected = {key: prepared[key] for key in ('revision_id', 'index', 'sheet_id')}
    expected['geometry_fingerprint'] = geom['fingerprint']
    if (record['id'] != evidence['id'] or evidence['source'] != expected or
            evidence['artifact_sha256'] != prepared['input_sha256'] or
            geometry.sheet_geometry(geom, prepared['revision_id'])['fingerprint'] != geom['fingerprint']):
        _fail('duct_evidence_changed', 'Captured evidence no longer matches its original page coordinates.')
    if current:
        unused, assignment = _source_context(data, sheets, expected)
        if assignment != record['source_binding']:
            _fail('duct_evidence_assignment_changed', 'The captured source role, building or level changed.')
    return copy.deepcopy(record)


def _merge_source(result, context):
    key = context['geometry']['sheet_id']
    if key not in result:
        result[key] = copy.deepcopy(context)
        return
    if result[key]['geometry'] != context['geometry']:
        _fail('duct_evidence_geometry', 'Evidence on one sheet has conflicting coordinate identities.')
    evidence = {entry['id']: entry for entry in result[key]['evidence']}
    for entry in context['evidence']:
        if entry['id'] in evidence and evidence[entry['id']] != entry:
            _fail('duct_evidence_collision', 'Different duct evidence uses one identity.')
        if entry['id'] not in evidence:
            result[key]['evidence'].append(copy.deepcopy(entry))
            evidence[entry['id']] = entry


def capture_evidence(workspace, data, sheets, values, actor, reason):
    _exact(values, {'generation_id', 'source', 'bbox', 'kind', 'text'})
    generation = _generation(data, values['generation_id'])
    _exact(values['source'], {'revision_id', 'index'})
    source = values['source']
    if type(source['index']) is not int or not isinstance(source['revision_id'], str):
        _fail('duct_evidence_source', 'Select an original project page.')
    sheet = sheets.get(source['revision_id'] + ':' + str(source['index']))
    if sheet is None:
        _fail('duct_evidence_source', 'Select an available original project page.')
    geom = geometry.sheet_geometry(sheet, source['revision_id'])
    canonical = dict(source, sheet_id=geom['sheet_id'], geometry_fingerprint=geom['fingerprint'])
    unused, binding = _source_context(data, sheets, canonical)
    bounds = values['bbox']
    if (not isinstance(bounds, list) or len(bounds) != 4 or
            any(type(v) not in (int, float) or not math.isfinite(v) for v in bounds) or
            not (0 <= bounds[0] < bounds[2] <= 1 and 0 <= bounds[1] < bounds[3] <= 1)):
        _fail('duct_evidence_bounds', 'Capture a positive region inside the original page.')
    transcription = values['text']
    _capture_kind_text(values['kind'], transcription)
    if len(data['duct_evidence']) >= 10000:
        _fail('duct_evidence_limit', 'The saved duct evidence collection is full.')
    prepared = _input_engine(workspace).prepare_input(copy.deepcopy(source), actor, reason)
    identifier = 'duct_user_evidence_' + uuid.uuid4().hex
    record = {'id': identifier, 'project_id': data['project_id'], 'generation_at_capture': generation['id'],
        'kind': values['kind'], 'text_origin': 'operator_transcription' if transcription is not None else None,
        'evidence': {'id': identifier, 'source': canonical, 'bbox': copy.deepcopy(bounds),
                     'text': transcription, 'artifact_sha256': prepared['input_sha256']},
        'input': prepared, 'geometry': geom, 'source_binding': binding, 'actor': actor, 'reason': reason}
    _capture_record(workspace, data, sheets, record)
    data['duct_evidence'].append(record)
    data['duct_last_capture_id'] = identifier
    data['duct_capture_ack'] = {'evidence_id': identifier, 'generation_id': generation['id'],
        'base_workflow_version': data['version'], 'saved_workflow_version': data['version'] + 1, 'actor': actor}
    return record


def _known_evidence(generation):
    result = {e['id']: e for context in generation['current_sources'].values() for e in context['evidence']}
    result.update({r['record']['evidence']['id']: r['record']['evidence']
                   for r in generation.get('user_evidence_refs', [])})
    return result


def _current_evidence_ids(generation):
    """Conservative obligations, including inherited child/admission provenance."""
    observations = generation['observations'] + [r['parent'] for r in generation.get('planar_splits', [])]
    observations += [r['observation'] or r['origin']['observation'] for r in generation.get('arc_obligations', [])
                     if r.get('schema') != 'duct-arc-revision-check-1' or r['state'] == 'active']
    used = {eid for observation in observations for eid in observation['evidence_ids']}
    used.update(eid for r in generation.get('arc_obligations', []) for eid in r['evidence_ids'])
    active = {o['id'] for o in generation['observations']}
    latest = {a['observation_id']: a for a in generation['admissions'] if a['observation_id'] in active}
    used.update(eid for a in latest.values() for eid in a['evidence_ids'])
    for record in generation.get('planar_splits', []):
        used.update(record['evidence_ids'])
        used.update(eid for cut in record['cuts'] for eid in cut['evidence_ids'])
    used.update(generation['coverage']['evidence_ids'])
    # Correspondence history remains retained, but only its latest non-rejected
    # record is a current obligation, matching calculation and closure semantics.
    relations = {record['id']: record for record in generation['correspondences']}
    used.update(eid for record in relations.values() if record['state'] != 'rejected'
                for eid in record['evidence_ids'])
    known = _known_evidence(generation)
    links = _module('project_duct_reading_links')
    targets = {o['id']: o for o in observations}
    for record in generation.get('reading_relationships', []):
        target = targets.get(record['observation_id'])
        if target is None or record['reading_id'] not in links.consumed_reading_ids(target):
            continue
        reading = next(r for r in target['readings'] if r['id'] == record['reading_id'])
        if all(eid in known and known[eid]['source'] == target['source'] for eid in reading['evidence_ids']):
            continue
        used.update(record['observation_reference_evidence_ids'])
        used.update(record['reading_reference_evidence_ids'])
    return used


def _current_sources(workspace, data, sheets, generation, strict=False):
    generation = _module('project_duct_revision').project_sources(generation)
    result, problems = {}, []
    used = _current_evidence_ids(generation)
    def problem(error, source):
        if strict:
            raise error
        problems.append({'code': getattr(error, 'code', 'duct_source_unavailable'),
            'message': getattr(error, 'message', 'Saved duct source evidence is unavailable or changed.'),
            'source': source})
    for reference in generation['source_records']:
        try:
            record = _producer(workspace, reference['id'])
            if _digest(record) != reference['sha256']:
                _fail('duct_producer_changed', 'The retained duct source record changed.')
        except (ValueError, OSError, KeyError) as error:
            problem(error, next((o['source'] for o in generation['observations']
                if o['source']['sheet_id'] in reference['sheet_ids']), None))
            continue
        # One valid original producer may contain multiple independently assigned
        # pages. A stale page cannot hide a different current page needed to repair
        # a surviving reading; producer-byte corruption still rejects all pages.
        for sheet_id in reference['sheet_ids']:
            source = None
            try:
                context = record['current_sources'][sheet_id]
                geom = context['geometry']
                source = {key: geom[key] for key in ('revision_id', 'index', 'sheet_id')}
                source['geometry_fingerprint'] = geom['fingerprint']
                current, assignment = _source_context(data, sheets, source)
                if assignment != reference.get('bindings', generation['source_bindings'])[sheet_id]:
                    _fail('duct_source_assignment_changed', 'The duct source role, building or level changed.')
                _merge_source(result, dict(copy.deepcopy(context), geometry=current))
            except (ValueError, OSError, KeyError) as error:
                problem(error, source)
    for reference in generation.get('user_evidence_refs', []):
        if reference.get('record', {}).get('id') not in used:
            continue
        try:
            record = reference['record']
            if _digest(record) != reference['sha256']:
                _fail('duct_evidence_changed', 'Retained user evidence changed.')
            registered = next((r for r in data['duct_evidence'] if r['id'] == record['id']), None)
            if registered != record:
                _fail('duct_evidence_changed', 'The registered user evidence no longer matches its retained snapshot.')
            _capture_record(workspace, data, sheets, record)
            _merge_source(result, {'geometry': record['geometry'], 'evidence': [record['evidence']]})
        except (ValueError, OSError, KeyError) as error:
            if strict:
                raise
            problems.append({'code': getattr(error, 'code', 'duct_evidence_unavailable'),
                'message': getattr(error, 'message', 'Captured duct source evidence is unavailable or changed.'),
                'source': reference.get('record', {}).get('evidence', {}).get('source')})
    return result, problems


def _selected_source_snapshot(generation):
    """Retain expected coordinates for active obligations, including stale ones.

    This is recovery metadata, not current-use validation. _current_sources
    independently verifies original bytes/assignments before any calculation.
    """
    generation = _module('project_duct_revision').project_sources(generation)
    used = _current_evidence_ids(generation)
    known = _known_evidence(generation)
    producer_sheets = {key for reference in generation['source_records'] for key in reference['sheet_ids']}
    selected = producer_sheets | {o['source']['sheet_id'] for o in generation['observations']}
    selected.update(r['parent']['source']['sheet_id'] for r in generation.get('planar_splits', []))
    selected.update(known[eid]['source']['sheet_id'] for eid in used if eid in known)
    result = {key: {'geometry': copy.deepcopy(context['geometry']),
        'evidence': [copy.deepcopy(e) for e in context['evidence'] if key in producer_sheets or e['id'] in used]}
        for key, context in generation['current_sources'].items() if key in selected}
    for reference in generation.get('user_evidence_refs', []):
        record = reference['record']
        if record['id'] in used and _digest(record) == reference['sha256']:
            _merge_source(result, {'geometry': record['geometry'], 'evidence': [record['evidence']]})
    return result


def _attach_evidence(workspace, data, sheets, generation, identifiers):
    """Attach only referenced captures; unused captures cannot alter row dependencies."""
    sources, unused = _current_sources(workspace, data, sheets, generation)
    existing = {e['id']: e for context in sources.values() for e in context['evidence']}
    captured = {record['id']: record for record in data['duct_evidence']}
    refs = generation.setdefault('user_evidence_refs', [])
    attached = {reference['record']['id'] for reference in refs}
    for identifier in sorted(identifiers):
        if identifier in captured:
            record = _capture_record(workspace, data, sheets, captured[identifier])
            if identifier not in attached:
                refs.append({'record': record, 'sha256': _digest(record)})
                attached.add(identifier)
            context = {'geometry': record['geometry'], 'evidence': [record['evidence']]}
            _merge_source(generation['current_sources'], context)
            _merge_source(sources, context)
            existing[identifier] = record['evidence']
        if identifier not in existing:
            _fail('duct_evidence_missing', 'Every used duct evidence reference must be registered and current.')
    return existing


def _register_evidence(workspace, data, sheets, generation, observation, admission_ids=None):
    existing = _attach_evidence(workspace, data, sheets, generation,
                                set(observation['evidence_ids']) | set(admission_ids or []))
    links = _module('project_duct_reading_links')
    consumed = set(links.consumed_reading_ids(observation))
    foreign_allowed = set()
    previous = next((o for o in generation['observations'] if o['id'] == observation['id']), None)
    # Existing explicit reviews allow an edited portion to be retained unresolved.
    # They never authorize new foreign evidence or silently rebind changed bytes.
    for candidate in [observation] + ([previous] if previous else []):
        for reading in candidate['readings']:
            if reading['id'] in links.consumed_reading_ids(candidate) and any(
                    r['observation_id'] == candidate['id'] and r['reading_id'] == reading['id'] and
                    (candidate is previous or r['observation_fingerprint'] == _observation_fingerprint(candidate))
                    for r in generation.get('reading_relationships', [])):
                foreign_allowed.update(reading['evidence_ids'])
    used = {eid for reading in observation['readings'] if reading['id'] in consumed for eid in reading['evidence_ids']}
    foreign_allowed &= used
    foreign_allowed -= links.target_only_ids(observation, admission_ids or [])
    for identifier in set(observation['evidence_ids']) | set(admission_ids or []):
        if existing[identifier]['source'] != observation['source'] and identifier not in foreign_allowed:
            _fail('duct_cross_page_applicability',
                  'Cross-page evidence needs a supported applicability relationship. Use evidence on this exact source page for this correction.')
    _source_context(data, sheets, observation['source'])
    if not any(existing[eid]['source'] == observation['source'] for eid in observation['evidence_ids']):
        _fail('duct_evidence_source', 'A duct portion needs evidence on its exact original source page.')
    return existing


def _request(workspace, data, sheets, generation, strict=False):
    sources, problems = _current_sources(workspace, data, sheets, generation, strict)
    request = {key: copy.deepcopy(generation[key]) for key in ('observations', 'admissions', 'correspondences', 'coverage')}
    request.update(current_sources=sources, scale_facts=data['calibrations'], scale_decisions=data['scale_decisions'],
                   rule_binding=_rule_binding())
    if generation.get('planar_splits'):
        request['planar_splits'] = copy.deepcopy(generation['planar_splits'])
    if generation.get('reading_relationships'):
        request['reading_relationships'] = copy.deepcopy(generation['reading_relationships'])
    if generation.get('arc_obligations'):
        request['arc_obligations'] = copy.deepcopy(generation['arc_obligations'])
    return request, problems


def _fresh(workspace, data, sheets, generation, strict=False):
    _module('project_duct_revision_checks').verify_lineage(data)
    request, problems = _request(workspace, data, sheets, generation, strict)
    result = _core().calculate(**request)
    if generation.get('implementation_sha256') != _implementation():
        result['dependencies'] = {'rows': {key: _digest([value, _implementation()])
                                  for key, value in result['dependencies']['rows'].items()},
                                  'coverage': _digest([result['dependencies']['coverage'], _implementation()])}
    return result, problems


def _coverage(observations, sources, issues=None):
    return {'state': 'partial', 'observation_fingerprints': {o['id']: _observation_fingerprint(o) for o in observations},
        'source_fingerprints': {key: value['geometry']['fingerprint'] for key, value in sources.items()},
        'evidence_ids': sorted({eid for o in observations for eid in o['evidence_ids']}),
        'issues': issues or ['Source observations do not establish complete drawing coverage.']}


def _admission(observation, rule, state='included', role='duct', method='source_validator', evidence_ids=None):
    return {'id': uuid.uuid4().hex, 'observation_id': observation['id'],
        'observation_fingerprint': _observation_fingerprint(observation), 'state': state, 'role': role,
        'evidence_ids': copy.deepcopy(evidence_ids if evidence_ids is not None else observation['evidence_ids']),
        'method': method, 'rule_fingerprint': rule['fingerprint']}


def _closure(selected, previous, replacement):
    affected = set(selected)
    # Match core semantics independently in each generation: latest record per
    # identity, with rejected edges absent; union old/new active dependencies.
    relationships = [r for g in (previous, replacement)
        for r in {v['id']:v for v in g.get('correspondences', [])}.values() if r['state'] != 'rejected']
    partitions = previous.get('planar_splits', []) + replacement.get('planar_splits', [])
    while True:
        expanded = set(affected)
        for record in relationships:
            members = set(record['members'])
            if affected & members:
                expanded.update(members)
        for record in partitions:
            members = {record['parent']['id']} | {member['observation_id'] for member in record['members']}
            if affected & members:
                expanded.update(members)
        for g in (previous, replacement):
            for check in g.get('arc_obligations', []):
                members = {check['id']} | set(check.get('affected_observation_ids', [check['family_root_id']]))
                if affected & members:
                    expanded.update(members)
        if expanded == affected:
            return sorted(affected & {o['id'] for g in (previous, replacement) for o in g['observations']})
        affected = expanded


def _retain(workspace, data, sheets, generation, kind, actor, reason, selected=None, check_transition=None):
    _module('project_duct_revision_checks').verify_lineage(data)
    if check_transition is not None:
        _module('project_duct_revision_checks').transition_guard(data, _saved(data), generation, check_transition)
    else:
        _module('project_duct_arc_repartition').preserve_obligated_roots(_saved(data), generation)
    result = copy.deepcopy(_module('project_duct_revision').project_sources(generation))
    result.update(id=uuid.uuid4().hex, previous_generation_id=data['duct_current'], kind=kind,
        actor=actor, reason=reason, at=datetime.now(timezone.utc).isoformat(),
        implementation_sha256=_implementation(), rule_binding=_rule_binding())
    result['current_sources'] = _selected_source_snapshot(result)
    if result['coverage']['state'] == 'partial':
        result['coverage']['source_fingerprints'] = {key: context['geometry']['fingerprint']
            for key, context in result['current_sources'].items()}
    request, _ = _request(workspace, data, sheets, result)
    core = _core()
    result['result'] = core.calculate(**request)
    previous = _saved(data)
    if previous is not None and selected is not None:
        result['result'] = core.replace_rows(previous['result'], result['result'],
            _closure(selected, previous, result))
    data['duct_generations'].append(result)
    data['duct_current'] = result['id']
    return result


def prepare(workspace, data, sheets, values, actor, reason):
    _exact(values, {'producer_record_id'})
    if 'ductwork' not in data['project']['scopes']:
        _fail('duct_scope_excluded', 'Include ductwork in the project scope before calculating it.')
    record = _producer(workspace, values['producer_record_id'])
    prior = _saved(data)
    if prior and _module('project_duct_revision').consumed(data, record):
        return prior
    core = _core()
    observations = [core.validate_observation(row) for row in record['observations']]
    sources = copy.deepcopy(record['current_sources'])
    if not sources or len(sources) > 100:
        _fail('duct_source_limit', 'A source producer must contain bounded source evidence.')
    bindings = {}
    for sheet_id, context in sources.items():
        geom = context['geometry']
        source = {key: geom[key] for key in ('revision_id', 'index', 'sheet_id')}
        source['geometry_fingerprint'] = geom['fingerprint']
        _, bindings[sheet_id] = _source_context(data, sheets, source)
        if sheet_id != source['sheet_id']:
            _fail('duct_source_mismatch', 'Producer evidence uses a different sheet identity.')
    if any(o['source']['sheet_id'] not in sources for o in observations):
        _fail('duct_source_mismatch', 'Every observation needs its retained producer source evidence.')
    if prior:
        previous_sheets = set(prior['current_sources'])
        previous_sheets.update(o['source']['sheet_id'] for o in prior['observations'])
        previous_sheets.update(s['parent']['source']['sheet_id'] for s in prior.get('planar_splits', []))
        previous_sheets.update(key for ref in prior['source_records'] for key in ref['sheet_ids'])
        if set(sources) - previous_sheets:
            return _module('project_duct_revision').stage(workspace, data, sheets,
                {'generation_id': prior['id'], 'producer_record_id': record['id']}, actor, reason)
        if previous_sheets & set(sources):
            return _module('project_duct_refresh').stage(workspace, data, sheets, record,
                observations, sources, bindings, actor, reason)
    rule = _rule_binding()
    generation = {'observations': [], 'admissions': [], 'correspondences': [], 'source_records': [],
                  'current_sources': {}, 'source_bindings': {}}
    if prior:
        generation = copy.deepcopy(prior)
    if {o['id'] for o in generation['observations']} & {o['id'] for o in observations}:
        _fail('duct_observation_collision', 'New observation identities collide with another retained source.')
    generation['observations'].extend(observations)
    generation['admissions'].extend(_admission(o, rule) for o in observations)
    generation['current_sources'].update(sources)
    generation['source_bindings'].update(bindings)
    generation['source_records'].append({'id': record['id'], 'sha256': _digest(record),
                                        'sheet_ids': sorted(sources), 'bindings': copy.deepcopy(bindings)})
    generation['coverage'] = _coverage(generation['observations'], generation['current_sources'])
    # Lineage only: image proposals do not derive geometry/grouping from schedules.
    generation['context_identities'] = {'document_run': data.get('document_run'),
                                      'reconciliation_generation': data.get('reconciliation_current')}
    selected = {o['id'] for o in observations}
    if prior:
        selected.update(o['id'] for o in prior['observations'] if o['source']['sheet_id'] in sources)
    return _retain(workspace, data, sheets, generation, 'source_observations', actor, reason, selected)


def _generation(data, identifier):
    generation = _saved(data)
    if generation is None or generation['id'] != identifier:
        _fail('duct_stale_generation', 'The duct generation changed. Refresh before saving this action.')
    return generation


def review_refresh(workspace, data, sheets, values, actor, reason):
    return _module('project_duct_refresh').review(workspace, data, sheets, values, actor, reason)


def reject_refresh(workspace, data, sheets, values, actor, reason):
    return _module('project_duct_refresh').reject(workspace, data, sheets, values, actor, reason)


def _latest_decision(data, identifier):
    return next((entry['id'] for entry in reversed(data['duct_decisions']) if entry.get('segment_id') == identifier), None)


def add(workspace, data, sheets, values, actor, reason):
    _exact(values, {'generation_id', 'observation'})
    generation = _generation(data, values['generation_id'])
    _exact(values['observation'], {'schema', 'source', 'group', 'geometry', 'readings', 'evidence_ids', 'issues'})
    observation = _core().validate_observation(dict(copy.deepcopy(values['observation']),
                                                 id='duct_user_portion_' + uuid.uuid4().hex))
    result = copy.deepcopy(generation)
    _register_evidence(workspace, data, sheets, result, observation)
    result['observations'].append(observation)
    result['admissions'].append(_admission(observation, _rule_binding(), method='user_review'))
    result['coverage'] = _coverage(result['observations'], result['current_sources'],
                                   ['Added portions need renewed source coverage review.'])
    result = _retain(workspace, data, sheets, result, 'added_portion', actor, reason, [observation['id']])
    data['duct_decisions'].append({'id': uuid.uuid4().hex, 'kind': 'added_portion',
        'segment_id': observation['id'], 'generation_id': generation['id'],
        'replacement_generation_id': result['id'], 'actor': actor, 'reason': reason,
        'observation': copy.deepcopy(observation)})
    return result


def split(workspace, data, sheets, values, actor, reason):
    _exact(values, {'generation_id', 'segment_id', 'segment_fingerprint', 'cuts', 'groups'})
    generation = _generation(data, values['generation_id'])
    parent = next((o for o in generation['observations'] if o['id'] == values['segment_id']), None)
    if parent is None or _observation_fingerprint(parent) != values['segment_fingerprint']:
        _fail('duct_stale_segment', 'The duct portion changed. Refresh before splitting it.')
    arc = parent['geometry']['kind'] in ('circular_arc', 'circular_arc_span')
    if parent['geometry']['kind'] not in ('planar', 'circular_arc', 'circular_arc_span'):
        _fail('duct_split_geometry', 'Split a supported planar path at source-backed size boundaries.')
    if parent['issues']:
        _fail('duct_split_source_issues', 'Resolve source exceptions on this parent portion before splitting it.')
    latest = next(a for a in reversed(generation['admissions']) if a['observation_id'] == parent['id'])
    if latest['state'] != 'included' or latest['role'] != 'duct':
        _fail('duct_split_role', 'Resolve this portion as included ductwork before splitting it.')
    geom, unused = _source_context(data, sheets, parent['source'])
    core = _core()
    partition = (core.partition_arc_span(parent['geometry'], geom, values['cuts']) if arc else
                 core.partition_planar_path(parent['geometry']['points'], geom, values['cuts']))
    pieces = partition['geometries'] if arc else partition['paths']
    if not isinstance(values['groups'], list) or len(values['groups']) != len(pieces):
        _fail('duct_split_groups', 'Provide source-supported grouping for every derived child portion.')
    captured = {record['id']: record for record in data['duct_evidence']}
    boundary_ids = sorted({eid for cut in partition['cuts'] for eid in cut['evidence_ids']})
    for index, cut in enumerate(partition['cuts']):
        for identifier in cut['evidence_ids']:
            record = captured.get(identifier)
            if record is None or record['kind'] not in ('graphic', 'label'):
                _fail('duct_split_boundary', 'Capture a graphic or label supporting each size-change boundary.')
            _capture_record(workspace, data, sheets, record)
            bounds = record['evidence']['bbox']
            if (record['evidence']['source'] != parent['source'] or
                    not core.split_boundary_inside(partition, index, bounds, geom)):
                _fail('duct_split_boundary', 'Each boundary must lie inside its evidence region on the same source page.')
    result = copy.deepcopy(generation)
    children = []
    for path, group in zip(pieces, values['groups']):
        if not isinstance(group, dict):
            _fail('duct_split_groups', 'Use a source-supported grouping object for each child.')
        child = copy.deepcopy(parent)
        child.update(id='duct_split_portion_' + uuid.uuid4().hex, group=copy.deepcopy(group))
        if arc:
            child.update(schema='duct-observation-3', geometry=copy.deepcopy(path))
        else:
            child['geometry'].update(points=path, dimension_check_ids=[])
        child['evidence_ids'] = sorted(set(parent['evidence_ids']) | set(group.get('evidence_ids', [])) | set(boundary_ids))
        _module('project_duct_reading_links').prune_unused_foreign_readings(child,
            {e['id']: e for context in generation['current_sources'].values() for e in context['evidence']})
        child = core.validate_observation(child)
        _register_evidence(workspace, data, sheets, result, child)
        children.append(child)
    constraint = {'id': 'duct_split_' + uuid.uuid4().hex, 'parent': copy.deepcopy(parent),
        'parent_fingerprint': _observation_fingerprint(parent), 'cuts': partition['cuts'],
        'members': [{'observation_id': child['id'], 'observation_fingerprint': _observation_fingerprint(child)} for child in children],
        'evidence_ids': boundary_ids}
    if arc:
        constraint['schema'] = 'duct-arc-split-1'
    result.setdefault('planar_splits', []).append(constraint)
    result['observations'] = [o for o in result['observations'] if o['id'] != parent['id']] + children
    result['admissions'].extend(_admission(child, _rule_binding(), method='user_review') for child in children)
    result['coverage'] = _coverage(result['observations'], result['current_sources'],
                                   ['Split portions need renewed source coverage and correspondence review.'])
    result = _retain(workspace, data, sheets, result, 'split_portion', actor, reason,
                     [parent['id']] + [child['id'] for child in children])
    data['duct_decisions'].append({'id': uuid.uuid4().hex, 'kind': 'split_portion',
        'segment_id': parent['id'], 'generation_id': generation['id'], 'replacement_generation_id': result['id'],
        'actor': actor, 'reason': reason, 'parent': copy.deepcopy(parent),
        'children': [child['id'] for child in children], 'constraint_id': constraint['id']})
    return result


def _rebind_splits(generation, changed_ids):
    """Reconstruct explicitly edited paths, preserving every whole-run reading.

    A gap, source mismatch or unsupported child retains the prior constraint;
    the pure engine exposes that mismatch on all affected leaves. This is never
    an unconditional fingerprint refresh or removal of an inconvenient check.
    """
    records = generation.get('planar_splits', [])
    by_parent = {record['parent']['id']: record for record in records}
    observations = {o['id']: o for o in generation['observations']}
    changed, visited = set(changed_ids), set()
    core = _core()

    def visit(record, depth=0):
        identifier = record['id']
        if identifier in visited or depth >= 16:
            return
        visited.add(identifier)
        for member in record['members']:
            if member['observation_id'] in by_parent:
                visit(by_parent[member['observation_id']], depth + 1)
        if not changed.intersection(m['observation_id'] for m in record['members']):
            return
        parent = record['parent']
        context = generation['current_sources'].get(parent['source']['sheet_id'])
        if context is None:
            context = _selected_source_snapshot(generation).get(parent['source']['sheet_id'])
        if context is None:
            return
        geom = context['geometry']
        children = [observations.get(member['observation_id'],
                    by_parent.get(member['observation_id'], {}).get('parent')) for member in record['members']]
        try:
            if record.get('schema') == 'duct-arc-split-1':
                if (record['parent_fingerprint'] != _observation_fingerprint(parent) or
                        any(child is None for child in children) or
                        any(member['observation_id'] not in changed and
                            member['observation_fingerprint'] != _observation_fingerprint(child)
                            for member, child in zip(record['members'], children))):
                    return
                core.prove_split_partition(parent, children, geom, record['cuts'], arc=True)
                record['members'] = [{'observation_id': child['id'],
                    'observation_fingerprint': _observation_fingerprint(child)} for child in children]
                changed.add(parent['id'])
                return
            if any(child is None or child['source'] != parent['source'] or child['geometry']['kind'] != 'planar'
                   for child in children):
                return
            paths = [core.validate_planar_path(child['geometry']['points'], geom)['pdf_points'] for child in children]
            if any(left[-1] != right[0] for left, right in zip(paths, paths[1:])):
                return
            partition = core.partition_planar_path(parent['geometry']['points'], geom, record['cuts'])
            replacement = copy.deepcopy(record)
            if paths != partition['pdf_paths']:
                joined, cuts = copy.deepcopy(paths[0]), []
                for path, old_cut in zip(paths[1:], record['cuts']):
                    cuts.append({'edge_index': len(joined) - 2, 'fraction': '1',
                                 'evidence_ids': copy.deepcopy(old_cut['evidence_ids'])})
                    joined.extend(path[1:])
                points = [geometry.pdf_to_display(point, geom) for point in joined]
                proposed = core.partition_planar_path(points, geom, cuts)
                if proposed['pdf_paths'] != paths:
                    return
                replacement['parent']['geometry']['points'] = points
                replacement['cuts'] = cuts
            replacement['members'] = [{'observation_id': child['id'], 'observation_fingerprint': _observation_fingerprint(child)}
                                      for child in children]
            replacement['parent_fingerprint'] = _observation_fingerprint(replacement['parent'])
        except ValueError:
            return
        record.clear()
        record.update(replacement)
        changed.add(parent['id'])

    for record in records:
        visit(record)


def _preserve_span_geometry(previous, replacement):
    old, new = previous['geometry'], replacement['geometry']
    if old['kind'] == 'circular_arc_span':
        if (replacement['schema'] != 'duct-observation-3' or new['kind'] != 'circular_arc_span' or
                any(old[key] != new[key] for key in ('base_points', 'start_ray', 'end_ray'))):
            _fail('duct_arc_span_geometry_immutable', 'Retain this derived circle and interval; use a complete-family replacement to move saved boundaries.')
    elif new['kind'] == 'circular_arc_span':
        _fail('duct_arc_span_lineage', 'Create derived spans through an explicit source-backed split.')


def correct(workspace, data, sheets, values, actor, reason):
    _exact(values, {'segment_id', 'segment_fingerprint', 'changes', 'supersedes'})
    generation = _saved(data)
    if generation is None:
        _fail('duct_required', 'Find duct observations before correcting a segment.')
    observation = next((o for o in generation['observations'] if o['id'] == values['segment_id']), None)
    if (observation is None or _observation_fingerprint(observation) != values['segment_fingerprint'] or
            values['supersedes'] != _latest_decision(data, values['segment_id'])):
        _fail('duct_stale_segment', 'The duct segment or its decision changed. Refresh before correcting it.')
    changes = values['changes']
    _exact(changes, {'observation', 'state', 'role', 'evidence_ids'})
    replacement = _core().validate_observation(changes['observation'])
    if replacement['id'] != observation['id'] or replacement['source'] != observation['source']:
        _fail('duct_correction_source', 'A correction must retain its original segment and source identity.')
    _preserve_span_geometry(observation, replacement)
    if replacement['geometry']['kind'] == 'circular_arc' and any(
            member['observation_id'] == observation['id']
            for record in generation.get('planar_splits', []) for member in record['members']):
        _fail('duct_arc_split_unsupported', 'A retained planar split member cannot be replaced by a circular arc.')
    _source_context(data, sheets, replacement['source'])
    result = copy.deepcopy(generation)
    _register_evidence(workspace, data, sheets, result, replacement, changes['evidence_ids'])
    result['observations'] = [replacement if o['id'] == replacement['id'] else o for o in result['observations']]
    _rebind_splits(result, [replacement['id']])
    result['admissions'].append(_admission(replacement, _rule_binding(), changes['state'], changes['role'],
                                         'user_review', changes['evidence_ids']))
    result['coverage'] = _coverage(result['observations'], result['current_sources'], ['A correction needs renewed coverage review.'])
    event = {'id': uuid.uuid4().hex, 'segment_id': observation['id'], 'supersedes': values['supersedes'],
        'before': _observation_fingerprint(observation), 'after': _observation_fingerprint(replacement), 'actor': actor, 'reason': reason,
        'changes': copy.deepcopy(changes), 'generation_id': generation['id']}
    result = _retain(workspace, data, sheets, result, 'correction', actor, reason, [observation['id']])
    event['replacement_generation_id'] = result['id']
    data['duct_decisions'].append(event)
    return result


def recalculate(workspace, data, sheets, values, actor, reason):
    _exact(values, {'generation_id', 'calibration_ids'})
    generation = _generation(data, values['generation_id'])
    if not isinstance(values['calibration_ids'], dict):
        _fail('duct_calibration_map', 'Choose scale facts by saved duct segment identity.')
    if set(values['calibration_ids']) - {o['id'] for o in generation['observations']}:
        _fail('duct_segment_missing', 'A selected duct segment is not in the current generation.')
    result = copy.deepcopy(generation)
    rule = _rule_binding()
    for observation in result['observations']:
        if observation['id'] not in values['calibration_ids']:
            continue
        shape = observation['geometry']
        target = shape if shape['kind'] in ('planar', 'circular_arc', 'circular_arc_span') else shape.get('projection', {})
        if 'scale_fact_id' not in target:
            _fail('duct_scale_not_used', 'This segment has no scaled component to recalculate.')
        target['scale_fact_id'] = values['calibration_ids'][observation['id']]
        latest = next(a for a in reversed(result['admissions']) if a['observation_id'] == observation['id'])
        result['admissions'].append(_admission(observation, rule, latest['state'], latest['role'], latest['method'], latest['evidence_ids']))
    result['coverage'] = _coverage(result['observations'], result['current_sources'], ['Recalculated portions need renewed coverage review.'])
    selected = list(values['calibration_ids']) or [o['id'] for o in result['observations']]
    _rebind_splits(result, selected)
    return _retain(workspace, data, sheets, result, 'recalculation', actor, reason, selected)


def review_context(workspace, data, sheets, values, actor, reason):
    _exact(values, {'generation_id', 'coverage', 'correspondences'})
    generation = _generation(data, values['generation_id'])
    fresh, _ = _fresh(workspace, data, sheets, generation, strict=True)
    if fresh['dependencies'] != generation['result']['dependencies']:
        _fail('duct_stale_context', 'Duct dependencies changed. Recalculate before reviewing coverage or correspondence.')
    coverage = values['coverage']
    expected_observations = {o['id']: _observation_fingerprint(o) for o in generation['observations']}
    current_sources, unused = _current_sources(workspace, data, sheets, generation, strict=True)
    expected_sources = {key: value['geometry']['fingerprint'] for key, value in current_sources.items()}
    if (not isinstance(coverage, dict) or coverage.get('observation_fingerprints') != expected_observations or
            coverage.get('source_fingerprints') != expected_sources):
        _fail('duct_stale_coverage', 'Coverage must refer to every current observation and selected source identity.')
    result = copy.deepcopy(generation)
    result['coverage'] = copy.deepcopy(coverage)
    result['correspondences'] = copy.deepcopy(values['correspondences'])
    if not isinstance(result['correspondences'], list):
        _fail('duct_correspondence_fields', 'Correspondence review must be a list of source-bound records.')
    for record in result['correspondences']:
        _exact(record, {'id', 'members', 'primary_observation_id', 'state', 'evidence_ids'})
        if not isinstance(record['id'], str) or not isinstance(record['members'], dict):
            _fail('duct_correspondence_fields', 'Correspondence identities and member bindings are invalid.')
    old_relations = {record['id']: record for record in generation['correspondences']}
    new_relations = {record['id']: record for record in result['correspondences']}
    selected = set()
    for key in set(old_relations) | set(new_relations):
        if old_relations.get(key) != new_relations.get(key):
            for records in (old_relations, new_relations):
                if key in records:
                    selected.update(records[key]['members'])
    result = _retain(workspace, data, sheets, result, 'context_review', actor, reason, selected)
    data['duct_decisions'].append({'id': uuid.uuid4().hex, 'kind': 'context_review', 'actor': actor,
        'reason': reason, 'generation_id': generation['id'], 'replacement_generation_id': result['id'],
        'coverage': copy.deepcopy(coverage), 'correspondences': copy.deepcopy(values['correspondences'])})
    return result


def _points(observation):
    shape = observation['geometry']
    if shape['kind'] in ('circular_arc', 'circular_arc_span'):
        return []  # Arc controls are not a straight-segment display path.
    return copy.deepcopy(shape.get('points', shape.get('projection', {}).get('points', [])))


def view(workspace, data, sheets, documents=None):
    _module('project_duct_arc_repartition').verify_events(data)
    generation = _saved(data)
    output = {'available': generation is not None, 'current_generation_id': data['duct_current'],
        'generations': copy.deepcopy(data['duct_generations']), 'decisions': copy.deepcopy(data['duct_decisions']),
        'segments': [], 'groups': [], 'issues': [], 'coverage': None, 'correspondences': [],
        'state_fingerprint': None, 'complete': False, 'known_subtotal_ft': None, 'total_ft': None,
        'current_sources': {}, 'coverage_review_template': None}
    output.update(arc_families=[], arc_repartition_events=copy.deepcopy(data['duct_arc_repartition_events']),
        revision_checks=[], revision_check_events=copy.deepcopy(data['duct_revision_check_events']))
    output.update(available_evidence=[], last_capture_id=data['duct_last_capture_id'],
                  capture_ack=copy.deepcopy(data['duct_capture_ack']),
                  parent_reading_events=copy.deepcopy(data['duct_parent_reading_events']),
                  parent_graphic_events=copy.deepcopy(data['duct_parent_graphic_events']))
    output.update(refreshes=_module('project_duct_refresh').view(workspace, data, sheets),
                  refresh_decisions=copy.deepcopy(data['duct_refresh_decisions']),
                  revisions=_module('project_duct_revision').view(workspace, data, sheets),
                  revision_decisions=copy.deepcopy(data['duct_revision_decisions']))
    for record in data['duct_evidence']:
        entry = copy.deepcopy(record)
        try:
            _capture_record(workspace, data, sheets, record)
            entry.update(validity='current', issues=[])
        except (ValueError, OSError, KeyError) as error:
            entry.update(validity='stale', issues=[getattr(error, 'message', 'Captured evidence is unavailable or changed.')])
        output['available_evidence'].append(entry)
    if generation is None:
        return output
    output['revision_checks'] = _module('project_duct_revision_checks').view(workspace,data,sheets,generation)
    output['arc_families'] = _module('project_duct_arc_repartition').view(workspace, data, sheets, generation)
    output.update(_module('project_duct_reading_links').view(workspace, data, sheets, generation))
    fresh, source_problems = _fresh(workspace, data, sheets, generation)
    current = _core().result_view(generation['result'], fresh['dependencies'])
    rows = {row['id']: row for row in current['rows']}
    sources, _ = _current_sources(workspace, data, sheets, generation)
    all_evidence = {e['id']: e for context in generation['current_sources'].values() for e in context['evidence']}
    for observation in generation['observations']:
        row = rows.get(observation['id'])
        if row is None:
            row = next((value for value in current['rows'] if observation['id'] in value['observation_ids']), None)
            if row:
                row = dict(row, status='duplicate', meters=None, feet=None, stored_meters=None)
        if row is None:
            _fail('duct_row_missing', 'A saved duct observation has no retained calculation row.')
        latest = next(a for a in reversed(generation['admissions']) if a['observation_id'] == observation['id'])
        output['segments'].append({'id': observation['id'], 'observation': copy.deepcopy(observation),
            'fingerprint': _observation_fingerprint(observation), 'observation_fingerprint': _observation_fingerprint(observation),
            'status': row['status'], 'meters': row['meters'], 'feet': row['feet'],
            'stored_meters': row.get('stored_meters'), 'issues': copy.deepcopy(row['issues']),
            'source': copy.deepcopy(observation['source']), 'points': _points(observation),
            'latest_decision_id': _latest_decision(data, observation['id']),
            'dependency_fingerprint': row['dependency_fingerprint'], 'state': latest['state'],
            'role': latest['role'], 'evidence_ids': copy.deepcopy(latest['evidence_ids']),
            'evidence': [copy.deepcopy(all_evidence[eid]) for eid in observation['evidence_ids'] if eid in all_evidence]})
        if observation['geometry']['kind'] in ('circular_arc', 'circular_arc_span'):
            output['segments'][-1]['geometry'] = copy.deepcopy(observation['geometry'])
    output.update(groups=current['groups'], coverage=copy.deepcopy(generation['coverage']),
        correspondences=copy.deepcopy(generation['correspondences']), complete=current['complete'],
        known_subtotal_ft=current['known_subtotal_ft'], total_ft=current['total_ft'], current_sources=sources,
        coverage_review_template=_coverage(generation['observations'], sources))
    output['issues'] = source_problems + [{'code': 'duct_coverage', 'message': str(issue), 'source': None}
                                         for issue in current['coverage_issues']]
    for segment in output['segments']:
        for issue in segment['issues']:
            source = dict(segment['source'], points=segment['points'])
            if 'geometry' in segment:
                source['geometry'] = copy.deepcopy(segment['geometry'])
            output['issues'].append({'code': 'duct_segment', 'message': segment['id'] + ': ' + str(issue),
                                    'source': source})
    output['state_fingerprint'] = _digest({'dependencies': fresh['dependencies'], 'current': current,
        'source_problems': source_problems, 'generation_id': generation['id']})
    if output['refreshes']:
        output['state_fingerprint'] = _digest({'calculation_state': output['state_fingerprint'],
            'refreshes': output['refreshes'], 'decisions': output['refresh_decisions']})
        for refresh in output['refreshes']:
            if refresh['state'] == 'pending':
                output['issues'].append({'code': 'duct_refresh_pending',
                    'message': 'A saved source reread awaits explicit review: ' + refresh['id'], 'source': None})
    if output['revisions']:
        output['state_fingerprint'] = _digest({'calculation_state': output['state_fingerprint'],
            'revisions': output['revisions'], 'decisions': output['revision_decisions']})
        for revision in output['revisions']:
            if revision['state'] == 'pending':
                output['issues'].append({'code': 'duct_revision_pending',
                    'message': 'A new source awaits explicit scope or revision review: ' + revision['id'], 'source': None})
    return output


def issues(result):
    return [{'id': 'duct:' + str(index) + ':' + issue['code'], 'message': issue['message'],
             'source': issue.get('source'), 'state': 'open', 'automatic': True}
            for index, issue in enumerate(result['issues'])]


def verify_export(workspace, data, sheets):
    _module('project_duct_revision_checks').verify_lineage(data)
    _module('project_duct_arc_repartition').verify_events(data)
    for record in data['duct_evidence']:
        _capture_record(workspace, data, sheets, record, current=False)
    for generation in data['duct_generations']:
        for reference in generation.get('user_evidence_refs', []):
            record = reference['record']
            if (_digest(record) != reference['sha256'] or
                    record not in data['duct_evidence']):
                _fail('duct_evidence_changed', 'Export cannot verify retained user evidence.')
    records = {}
    for generation in data['duct_generations']:
        for reference in generation['source_records']:
            record = _producer(workspace, reference['id'])
            if _digest(record) != reference['sha256']:
                _fail('duct_producer_changed', 'Export cannot verify the retained duct producer evidence.')
            records[record['id']] = record
    for record in _module('project_duct_refresh').verify_export(workspace, data):
        records[record['id']] = record
    for record in _module('project_duct_revision').verify_export(workspace, data):
        records[record['id']] = record
    return [records[key] for key in sorted(records)]
