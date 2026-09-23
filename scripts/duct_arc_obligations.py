"""Check-only arc scopes: source constraints, never additional quantities."""
import copy


def family_index(core, observations, splits, geometries):
    active = {o['id']: o for o in observations}
    parents, owners = {}, {}
    for split in splits:
        if split.get('schema') != 'duct-arc-split-1':
            continue
        parent = split['parent']
        if parent['id'] in parents or parent['id'] in active:
            core._fail('arc_family_identity', 'A circular family has conflicting parent identities.')
        parents[parent['id']] = split
        for member in split['members']:
            key = member['observation_id']
            if key in owners:
                core._fail('arc_family_identity', 'A circular portion has multiple parents.')
            owners[key] = parent['id']
    nodes = dict(active, **{k: v['parent'] for k, v in parents.items()})
    visited = set()

    def walk(key, ancestry):
        if key in ancestry or len(ancestry) >= 16 or key not in nodes:
            core._fail('arc_family_lineage', 'A saved circular family is cyclic, too deep or missing a member.')
        visited.add(key)
        if key not in parents:
            return [nodes[key]], [], []
        split = parents[key]
        parent = nodes[key]
        members = [nodes.get(m['observation_id']) for m in split['members']]
        if (split['parent_fingerprint'] != core.fingerprint(parent) or
                any(o is None or core.fingerprint(o) != m['observation_fingerprint']
                    for o, m in zip(members, split['members']))):
            core._fail('arc_family_lineage', 'The retained circular family fingerprints changed.')
        geometry = geometries.get(parent['source']['sheet_id'])
        if geometry is None:
            core._fail('arc_family_source', 'The retained family source geometry is missing.')
        core.prove_split_partition(parent, members, geometry, split['cuts'], arc=True)
        leaves, retained, cuts = [], [parent], []
        for index, member in enumerate(members):
            child_leaves, child_parents, child_cuts = walk(member['id'], ancestry + [key])
            leaves.extend(child_leaves); retained.extend(child_parents); cuts.extend(child_cuts)
            if index < len(split['cuts']):
                cuts.append(copy.deepcopy(split['cuts'][index]))
        return leaves, retained, cuts

    result = []
    roots = [o for o in nodes.values() if o['geometry']['kind'] == 'circular_arc' and o['id'] not in owners]
    for root in roots:
        leaves, retained, cuts = walk(root['id'], [])
        result.append({'root_id': root['id'], 'root': root, 'source': root['source'],
                       'leaves': leaves, 'leaf_ids': [o['id'] for o in leaves], 'parents': retained, 'cuts': cuts})
    if set(parents) - visited or set(owners) - visited:
        core._fail('arc_family_lineage', 'A retained circular family has no original circular root.')
    return result


def validate(core, records):
    records = [] if records is None else core._array(records, 2000)
    seen = set()
    for record in records:
        versioned = record.get('schema') == 'duct-arc-revision-check-1'
        fields = 'id family_root_id origin state observation retained_observation evidence_ids reason'
        core._exact(record, fields + (' schema current_source affected_observation_ids revision_binding' if versioned else ''))
        core._plain(record['id'], 160)
        if not versioned or record['state'] == 'active':
            core._plain(record['family_root_id'], 160)
        if versioned:
            core._ids(record['affected_observation_ids'], minimum=1)
            if record['current_source'] is not None:
                core._source(record['current_source'])
            binding = record['revision_binding']
            core._exact(binding, 'revision_id revision_fingerprint base_generation_id previous_check_fingerprint decision_id')
            for value in binding.values():
                core._plain(value, 160)
            if record['state'] == 'not_applicable' or (record['state'] == 'unresolved' and record['family_root_id'] is not None):
                core._fail('arc_check_state', 'Retired revision checks belong in history; pending checks have explicit targets.')
            if record['state'] == 'active' and record['affected_observation_ids'] != [record['family_root_id']]:
                core._fail('arc_check_scope', 'An active revision check binds exactly one circular root.')
        if record['id'] in seen:
            core._fail('arc_check_identity', 'Each retained circular check needs one identity.')
        seen.add(record['id'])
        core._enum(record['state'], {'active', 'unresolved', 'not_applicable'})
        core._ids(record['evidence_ids'], minimum=1); core._plain(record['reason'], 2000)
        origin = record['origin']
        core._exact(origin, 'target_kind target_id target_fingerprint reading_id reading_fingerprint role observation')
        core._enum(origin['target_kind'], {'observation', 'split_parent'})
        core._enum(origin['role'], {'dimension_check_ids', 'radius_check_ids'})
        original = core.validate_observation(origin['observation'])
        reading = next((r for r in original['readings'] if r['id'] == origin['reading_id']), None)
        if (original['id'] != origin['target_id'] or core.fingerprint(original) != origin['target_fingerprint'] or
                original['geometry']['kind'] not in ('circular_arc', 'circular_arc_span') or
                origin['reading_id'] not in original['geometry'][origin['role']] or
                reading is None or core.fingerprint(reading) != origin['reading_fingerprint']):
            core._fail('arc_check_origin', 'The original circular reading identity changed.')
        retained = core.validate_observation(record['retained_observation'])
        if ((not versioned and retained['source'] != original['source']) or retained['geometry']['kind'] not in ('circular_arc', 'circular_arc_span') or
                origin['reading_id'] not in retained['geometry'][origin['role']]):
            core._fail('arc_check_origin', 'The retained previous scope must preserve this original reading role and source.')
        if record['state'] != 'active':
            if record['observation'] is not None:
                core._fail('arc_check_state', 'Unresolved or inapplicable checks have no active measurement scope.')
            continue
        observation = core.validate_observation(record['observation'])
        role = origin['role']
        other = 'radius_check_ids' if role == 'dimension_check_ids' else 'dimension_check_ids'
        if (observation['id'] != record['id'] or observation['source'] != (record['current_source'] if versioned else original['source']) or
                observation['geometry']['kind'] not in ('circular_arc', 'circular_arc_span') or
                len(observation['readings']) != 1 or observation['readings'][0]['id'] != origin['reading_id'] or
                observation['geometry'][role] != [origin['reading_id']] or observation['geometry'][other]):
            core._fail('arc_check_scope', 'A check-only scope retains its source, reading identity and semantic role.')
    return records


def apply(core, records, observations, splits, rows, dependencies, geometries, evidence,
          facts, decisions, rule, relationships, link_core):
    if not records:
        return [], []
    validate(core, records)
    # Current family topology was validated independently by the ordinary split
    # checks. If a later edit makes the topology invalid, retain the obligation
    # as a blocking diagnostic rather than hiding the entire saved project.
    try:
        families = {f['root_id']: f for f in family_index(core, list(observations.values()), splits, geometries)}
    except ValueError as exc:
        families = {}
    split_members = {s['parent']['id']:[m['observation_id'] for m in s['members']] for s in splits}
    def leaves(key, ancestry=()):
        if key in ancestry or len(ancestry) >= 16:
            core._fail('arc_check_target_missing', 'A pending check has invalid split lineage.')
        if key in split_members:
            return set().union(*(leaves(child, ancestry+(key,)) for child in split_members[key]))
        if key not in observations:
            core._fail('arc_check_target_missing', 'A pending check lost a current target.')
        return {key}
    split_closures = []
    for key in split_members:
        try:
            split_closures.append(leaves(key))
        except ValueError:
            pass  # Ordinary split diagnostics retain invalid non-target topology.
    snapshots = copy.deepcopy(rows)
    coverage_issues, bindings = [], []
    for record in records:
        family = families.get(record['family_root_id'])
        issues, measurement, scope_dependency = [], None, None
        versioned = record.get('schema') == 'duct-arc-revision-check-1'
        source = record['current_source'] if versioned else record['origin']['observation']['source']
        required = set(record['evidence_ids'])
        if not family and not (versioned and record['state'] == 'unresolved'):
            issues.append('arc_check_family_missing')
        elif family and family['source'] != source:
            issues.append('arc_check_source_changed')
        if any(eid not in evidence or (source is not None and evidence[eid]['source'] != source) for eid in required):
            issues.append('arc_check_evidence_changed')
        if record['state'] == 'unresolved':
            issues.append('arc_check_unresolved')
        elif record['state'] == 'active' and family and not issues:
            scope, root = record['observation'], family['root']
            geometry = geometries.get(source['sheet_id'])
            try:
                if geometry is None:
                    core._fail('arc_check_source_changed', 'The circular check source is unavailable.')
                root_span = core.arc_span(root['geometry'], geometry)
                span = core.arc_span(scope['geometry'], geometry)
                if (span.center != root_span.center or span.radius_squared != root_span.radius_squared or
                        span.direction != root_span.direction):
                    core._fail('arc_check_scope_changed', 'The retained check belongs to another circular geometry.')
                core.sheet_scale.arc_geometry.make_span(root_span.root, span.start_ray, span.end_ray)
                admission = {'id': record['id'], 'observation_id': scope['id'],
                    'observation_fingerprint': core.fingerprint(scope), 'state': 'included', 'role': 'duct',
                    'evidence_ids': [eid for eid in scope['evidence_ids'] if eid in evidence and evidence[eid]['source']==source], 'method': 'user_review', 'rule_fingerprint': rule['fingerprint']}
                check, scope_dependency, unused = core._prepare(scope, {scope['id']: admission},
                    geometries, evidence, facts, decisions, rule, relationships, link_core)
                issues.extend(check['issues']); measurement = check['components'].get('measurement')
                if measurement:
                    for key in family['leaf_ids']:
                        leaf_measurement = snapshots[key]['components'].get('measurement')
                        if leaf_measurement and leaf_measurement['scale_binding'] != measurement['scale_binding']:
                            issues.append('arc_check_scale_conflict')
            except ValueError as exc:
                issues.append(getattr(exc, 'code', 'arc_check_scope_changed'))
        affected = set(family['leaf_ids']) if family else ({record['family_root_id']} & set(rows))
        if versioned and record['state'] == 'unresolved':
            affected = set()
            for target in record['affected_observation_ids']:
                affected.update(leaves(target))
        if not family and not affected:
            core._fail('arc_check_family_missing', 'A retained circular check has no current family; recover its source-bound generation before calculating.')
        while True:
            expanded = affected | {key for row in snapshots.values() if affected.intersection(row['observation_ids']) for key in row['observation_ids']}
            for split_closure in split_closures:
                if affected.intersection(split_closure):
                    expanded.update(split_closure)
            if expanded == affected:
                break
            affected = expanded
        diagnostic = {'check_id': record['id'], 'state': record['state'], 'role': record['origin']['role'],
            'leaf_ids': sorted(affected), 'issues': sorted(set(issues)), 'measurement': measurement}
        binding = {'record': record, 'evidence': {key: evidence.get(key) for key in sorted(required)},
                   'scope': scope_dependency, 'family': family, 'diagnostic': diagnostic}
        diagnostic['fingerprint'] = core.fingerprint(binding)
        bindings.append(diagnostic['fingerprint'])
        if not affected:
            coverage_issues.extend(issues or ['arc_check_family_missing'])
        for key in affected:
            if key not in rows:
                continue
            rows[key]['components'].setdefault('arc_obligations', []).append(copy.deepcopy(diagnostic))
            dependencies[key].setdefault('arc_obligations', []).append(diagnostic['fingerprint'])
            if issues:
                rows[key].update(status='unresolved', meters=None, feet=None)
                rows[key]['issues'].extend(issues)
    return coverage_issues, bindings
