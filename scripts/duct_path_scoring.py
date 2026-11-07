"""Bounded continuous-path diagnostics over independently annotated duct data.

This scorer never executes a model, invents truth or admits project quantities.
Paper distances use exact rationals and outward square-root bounds. Candidate
lengths call the unchanged duct measurement helper with frozen source evidence.
"""
import builtins
import copy
from decimal import Decimal, localcontext
from fractions import Fraction
import hashlib
import importlib.util
import json
from math import isfinite, isqrt
from pathlib import Path
import re
from types import SimpleNamespace

TASK = 'duct-evaluation-1'
VERSION = 'duct-path-scoring-3'
MAX_SAMPLES = 200
MAX_OBJECTS = 250
MAX_VERTICES = 128
MAX_GENERATED_POINTS = 20000
MAX_DISTANCE_OPERATIONS = 1000000
MAX_CURVE_DISTANCE_QUERIES = 20000
MAX_COVER_WORK = 1000000
MAX_MATCHING_OPERATIONS = 5000000
MAX_PAIRS = 40000
MAX_TOTAL_VERTICES = 20000
MAX_BYTES = 16 * 1024 * 1024
SQRT_SCALE = 10 ** 12
_ROOT = Path(__file__).resolve().parent
_DEPENDENCIES = {'duct_calculation.py', 'duct_arc_geometry.py', 'duct_arc_obligations.py',
                 'duct_arc_cover.py', 'duct_curve_distance.py', 'sheet_scale.py', 'sheet_geometry.py',
                 'project_duct_reading_links.py', 'duct_topology.py'}
_SUPPORTED_GEOMETRY = ('planar', 'circular_arc', 'circular_arc_span')
_SOURCE_KEYS = {'revision_id', 'index', 'sheet_id', 'geometry_fingerprint'}
_SAMPLE_KEYS = {'id', 'source_id', 'input_sha256', 'group_id', 'split', 'objects', 'context'}
_CRITERIA_KEYS = {'pairing_tolerance_pt', 'sampling_spacing_pt', 'minimum_precision',
    'minimum_recall', 'minimum_size_accuracy', 'minimum_work_status_accuracy',
    'minimum_length_coverage', 'maximum_absolute_length_error_ft'}
_RESULT_KEYS = {'id', 'project_id', 'state', 'source', 'geometry', 'role', 'input',
    'model_identity', 'producer_identity', 'observations', 'current_sources',
    'source_refs', 'unreadable', 'error', 'response', 'actor', 'reason', 'scale_context'}
_PREPARED_ROLES = ('plan', 'schedule', 'specification', 'detail', 'riser', 'legend',
                   'addendum', 'excluded')


class ScoringError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _fail(code, message):
    raise ScoringError(code, message)


def _json(value, depth=0, count=None):
    count = [0] if count is None else count
    count[0] += 1
    if depth > 24 or count[0] > 500000:
        _fail('size_limit', 'Structured evaluation input exceeds its bound.')
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if value.bit_length() > 256:
            _fail('size_limit', 'Integer precision exceeds the evaluation bound.')
    elif type(value) is float:
        if not isfinite(value):
            _fail('invalid_number', 'Numbers must be finite.')
    elif isinstance(value, str):
        if len(value) > 32768 or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            _fail('size_limit', 'Text exceeds the bounded Unicode contract.')
    elif isinstance(value, (list, dict)):
        if len(value) > 20000:
            _fail('size_limit', 'Collection exceeds its evaluation bound.')
        if isinstance(value, dict):
            if any(not isinstance(k, str) or not k or len(k) > 256 for k in value):
                _fail('invalid_schema', 'JSON keys must be bounded nonempty text.')
            value = value.values()
        for child in value:
            _json(child, depth + 1, count)
    else:
        _fail('invalid_schema', 'Only JSON values are supported.')


def packed(value):
    _json(value)
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False,
                     allow_nan=False, separators=(',', ':')).encode('utf-8')
    if len(raw) > MAX_BYTES:
        _fail('size_limit', 'Evaluation bytes exceed the saved record bound.')
    return raw


def _digest(value):
    return hashlib.sha256(packed(value)).hexdigest()


# Isolated loaders compile every transitive measurement dependency from captured
# source bytes. They never mutate process-global importlib or consult stale pyc.
_loaded_bytes = {}
_real_import = builtins.__import__


class _SourceLoader:
    def __init__(self, path):
        self.path = Path(path).resolve()
        if self.path.parent != _ROOT or self.path.name not in _DEPENDENCIES:
            _fail('scorer_dependency', 'Unsupported measurement dependency.')

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        raw = self.path.read_bytes()
        prior = _loaded_bytes.setdefault(self.path.name, raw)
        if prior != raw:
            _fail('scorer_changed', 'Measurement bytes changed while loading.')
        module.__dict__['__builtins__'] = dict(vars(builtins), __import__=_source_import)
        exec(compile(raw, str(self.path), 'exec'), module.__dict__)


def _source_spec(name, location, **unused):
    return importlib.util.spec_from_file_location(name, location, loader=_SourceLoader(location))


def _source_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == 'importlib.util':
        return SimpleNamespace(util=SimpleNamespace(spec_from_file_location=_source_spec,
                                                    module_from_spec=importlib.util.module_from_spec))
    return _real_import(name, globals, locals, fromlist, level)


_spec = _source_spec('duct_evaluation_measurement', _ROOT / 'duct_calculation.py')
_CORE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_CORE)
_spec = _source_spec('duct_evaluation_cover', _ROOT / 'duct_arc_cover.py')
_COVER = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_COVER)
_spec = _source_spec('duct_evaluation_curve_distance', _ROOT / 'duct_curve_distance.py')
_CURVE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_CURVE)
_spec = _source_spec('duct_evaluation_topology', _ROOT / 'duct_topology.py')
_TOPOLOGY = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_TOPOLOGY)
SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
DEPENDENCY_SHA256 = {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(_loaded_bytes.items())}
IMPLEMENTATION_SHA256 = _digest({'version': VERSION, 'source_sha256': SOURCE_SHA256,
                                 'dependencies': DEPENDENCY_SHA256})


def identity():
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != SOURCE_SHA256:
        _fail('scorer_changed', 'Reload the changed scorer before using it.')
    for name, expected in DEPENDENCY_SHA256.items():
        if hashlib.sha256((_ROOT / name).read_bytes()).hexdigest() != expected:
            _fail('scorer_changed', 'Reload changed measurement dependencies before scoring.')
    return {'version': VERSION, 'sha256': IMPLEMENTATION_SHA256}


def _exact(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        _fail('invalid_schema', label + ' has missing or unsupported fields.')


def _text(value, label='identity', maximum=512, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > maximum:
        _fail('invalid_input', label + ' must be bounded nonempty exact text.')


def _hash(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        _fail('invalid_identity', 'A complete lowercase SHA-256 identity is required.')


def _array(value, label, maximum, minimum=0):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        _fail('size_limit', label + ' has an unsupported item count.')


def _decimal(value, positive=False, maximum=10 ** 12, text=True):
    if ((text and not isinstance(value, str)) or
            (not text and type(value) not in (int, float)) or isinstance(value, bool)):
        _fail('invalid_number', 'Use the declared decimal-string or JSON-number type.')
    representation = str(value)
    if len(representation) > 40 or (text and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', representation) is None):
        _fail('invalid_number', 'Numeric precision exceeds the evaluation bound.')
    try:
        number = Decimal(representation)
        if (not number.is_finite() or number < 0 or number > maximum
                or abs(number.as_tuple().exponent) > 18 or (positive and number == 0)):
            raise ValueError()
    except (ValueError, ArithmeticError):
        _fail('invalid_number', 'Use finite nonnegative bounded decimal precision.')
    return Fraction(number)


def _meters(value):
    if value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r'(?:0|[1-9][0-9]*)\.[0-9]{6}', value) is None:
        _fail('invalid_number', 'Expected meters retain positive six-decimal text or null.')
    return _decimal(value, positive=True)


def _source(value):
    _exact(value, _SOURCE_KEYS, 'source')
    for key in _SOURCE_KEYS - {'index'}:
        _hash(value[key])
    if type(value['index']) is not int or not 0 <= value['index'] <= 4294967295:
        _fail('invalid_identity', 'Source page index must be a bounded integer.')


def _geometry(value, source):
    _source(source)
    try:
        actual = _CORE.sheet_scale.geometry_module._geometry(value)
    except ValueError as error:
        _fail(getattr(error, 'code', 'geometry_invalid'), str(error))
    expected = {k: actual[k] for k in ('revision_id', 'index', 'sheet_id')}
    expected['geometry_fingerprint'] = actual['fingerprint']
    if actual != value or expected != source:
        _fail('source_mismatch', 'Full source and canonical page geometry must agree.')


def project_scale_context(scale_context, source, geometry):
    """Project a retained global history to this exact page/geometry, in order."""
    _json(scale_context)
    _geometry(geometry, source)
    _exact(scale_context, {'facts', 'decisions'}, 'scale_context')
    for key in ('facts', 'decisions'):
        _array(scale_context[key], key, 2000)
    for key in ('facts', 'decisions'):
        ids = []
        for item in scale_context[key]:
            if not isinstance(item, dict):
                _fail('invalid_schema', 'Scale history entries must be records.')
            _text(item.get('id'))
            ids.append(item['id'])
        if len(ids) != len(set(ids)):
            _fail('duplicate_identity', 'Scale history identities must be unique.')
    source3 = {k: source[k] for k in ('revision_id', 'index', 'sheet_id')}
    facts = [fact for fact in scale_context['facts'] if fact.get('source') == source3
             and fact.get('geometry_fingerprint') == source['geometry_fingerprint']]
    for fact in facts:
        try:
            _CORE.sheet_scale._integrity(fact)
        except ValueError as error:
            _fail(getattr(error, 'code', 'scale_invalid'), str(error))
        if fact['geometry'] != geometry:
            _fail('source_mismatch', 'Scale geometry must match this exact frozen page.')
    ids = {fact['id'] for fact in facts}
    decisions = [d for d in scale_context['decisions'] if d.get('fact_id') in ids]
    for decision in decisions:
        _hash(decision.get('fact_fingerprint'))
        if decision.get('geometry_fingerprint') is not None:
            _hash(decision['geometry_fingerprint'])
        if decision.get('state') not in _CORE.sheet_scale.STATES:
            _fail('invalid_schema', 'Scale decision state is unsupported.')
    return copy.deepcopy({'facts': facts, 'decisions': decisions})


def normalize_context(context):
    _json(context)
    _exact(context, {'source', 'geometry', 'scale_context'}, 'context')
    projected = project_scale_context(context['scale_context'], context['source'], context['geometry'])
    if projected != context['scale_context']:
        _fail('source_mismatch', 'Sample scale context must contain only this exact page and geometry.')
    return copy.deepcopy(context)


def _points(points):
    _array(points, 'points', MAX_VERTICES, 2)
    for point in points:
        _array(point, 'point', 2, 2)
        for value in point:
            _decimal(value, maximum=1, text=False)
    if any(a == b for a, b in zip(points, points[1:])):
        _fail('invalid_path', 'Consecutive path vertices must be distinct.')


def _group(group):
    _exact(group, {'system', 'material', 'work_status', 'size'}, 'group')
    for key in ('system', 'material'):
        _text(group[key], key, 200, nullable=True)
    if group['work_status'] not in (None, 'new_install', 'existing_to_remain', 'demolition'):
        _fail('invalid_input', 'Work status is unsupported.')
    size = group['size']
    if size is not None:
        _exact(size, {'shape', 'dimensions', 'unit', 'original_text'}, 'size')
        if (size['shape'] not in ('rectangular', 'round', 'oval')
                or not isinstance(size['unit'], str) or size['unit'] not in _CORE.sheet_scale.UNITS):
            _fail('invalid_input', 'Size shape or unit is unsupported.')
        count = 1 if size['shape'] == 'round' else 2
        _array(size['dimensions'], 'size dimensions', count, count)
        for dimension in size['dimensions']:
            _decimal(dimension, positive=True)
        _text(size['original_text'], 'original size text', 200)


def _truth_shape(obj):
    """Legacy planar truth stays byte-identical; explicit v2 adds curve shapes."""
    if not isinstance(obj, dict):
        _fail('invalid_schema', 'A truth object must be a JSON object.')
    if 'schema' not in obj:
        _exact(obj, {'id', 'category', 'points', 'group', 'expected_meters'}, 'truth object')
        return {'kind': 'planar', 'points': obj['points']}
    _exact(obj, {'schema', 'id', 'category', 'geometry', 'group', 'expected_meters'}, 'truth object')
    if obj['schema'] != 'duct-path-truth-2':
        _fail('invalid_schema', 'Unsupported duct truth version.')
    shape = obj['geometry']
    if not isinstance(shape, dict) or shape.get('kind') not in _SUPPORTED_GEOMETRY:
        _fail('invalid_schema', 'Truth requires a supported centerline geometry.')
    keys = ({'kind', 'base_points', 'start_ray', 'end_ray'} if shape['kind'] == 'circular_arc_span'
            else {'kind', 'points'})
    _exact(shape, keys, 'truth geometry')
    return shape


def _canonical_shape(shape, geometry):
    """Use the source transform and its single canonical micropoint rounding."""
    try:
        if shape['kind'] == 'planar':
            _points(shape['points'])
            result = _CORE.validate_planar_path(shape['points'], geometry)
            return {'kind': 'planar', 'path': [tuple(Fraction(v, 1000000) for v in p)
                                             for p in result['pdf_points']]}
        points = shape['base_points'] if shape['kind'] == 'circular_arc_span' else shape['points']
        _points(points)
        span = _CORE.arc_span(shape, geometry)
        return {'kind': shape['kind'], 'controls': span.root.points,
                'start_ray': span.start_ray, 'end_ray': span.end_ray}
    except ValueError as error:
        _fail(getattr(error, 'code', 'invalid_path'), str(error))


def _topology_call(name, *args, **kwargs):
    try:
        return getattr(_TOPOLOGY, name)(*args, **kwargs)
    except ValueError as error:
        _fail(getattr(error, 'code', 'invalid_topology'), str(error))


def _truth_topology(value, object_ids):
    _exact(value, {'schema', 'evidence', 'assertions'}, 'topology truth')
    if value['schema'] != 'duct-topology-truth-1':
        _fail('invalid_schema', 'Unsupported topology truth version.')
    _array(value['evidence'], 'topology evidence', 2000)
    for item in value['evidence']:
        _exact(item, {'id', 'bbox', 'text'}, 'topology evidence region')
        _text(item['id'], maximum=160)
        _array(item['bbox'], 'evidence bounds', 4, 4)
        values = [_decimal(v, maximum=1, text=False) for v in item['bbox']]
        if not values[0] < values[2] or not values[1] < values[3]:
            _fail('invalid_topology', 'Topology evidence needs a nonempty displayed source region.')
        if item['text'] is not None:
            _text(item['text'], 'evidence text', 1000)
    assertions = _topology_call('normalize_assertions', value['assertions'], object_ids,
        [e['id'] for e in value['evidence']], allow_uncertain=False)
    return {'schema': value['schema'], 'evidence': sorted(copy.deepcopy(value['evidence']), key=lambda e: e['id']),
            'assertions': assertions}


def _result_fields(result):
    keys = _RESULT_KEYS
    if isinstance(result, dict) and isinstance(result.get('producer_identity'), dict) and \
            result['producer_identity'].get('version') == 'duct-source-producer-3':
        keys = keys | {'connections'}
    _exact(result, keys, 'producer_result')


def normalize_samples(samples):
    _array(samples, 'samples', MAX_SAMPLES, 1)
    _json(samples)
    ids, inputs = set(), set()
    vertices = 0
    for sample in samples:
        _exact(sample, _SAMPLE_KEYS | ({'topology'} if isinstance(sample, dict) and 'topology' in sample else set()), 'sample')
        for key in ('id', 'source_id', 'group_id'):
            _text(sample[key], key)
        _hash(sample['input_sha256'])
        if sample['id'] in ids or sample['input_sha256'] in inputs:
            _fail('duplicate_identity', 'Sample IDs and PNG inputs must be unique.')
        ids.add(sample['id']); inputs.add(sample['input_sha256'])
        if sample['split'] not in ('train', 'validation', 'test'):
            _fail('invalid_input', 'Sample split is unsupported.')
        normalize_context(sample['context'])
        _array(sample['objects'], 'truth objects', MAX_OBJECTS)
        object_ids = set()
        for obj in sample['objects']:
            shape = _truth_shape(obj)
            _text(obj['id'], maximum=160)
            if obj['id'] in object_ids:
                _fail('duplicate_identity', 'Truth IDs must be unique within a sample.')
            object_ids.add(obj['id'])
            if obj['category'] != 'ductwork':
                _fail('invalid_input', 'This task evaluates ductwork centerline truth only.')
            _group(obj['group']); _meters(obj['expected_meters'])
            _canonical_shape(shape, sample['context']['geometry'])
            vertices += len(shape.get('base_points', shape.get('points', [])))
            if vertices > MAX_TOTAL_VERTICES:
                _fail('work_limit', 'Truth vertex count exceeds the bounded evaluation work.')
    result = copy.deepcopy(samples)
    for sample in result:
        if 'topology' in sample:
            sample['topology'] = _truth_topology(sample['topology'], [o['id'] for o in sample['objects']])
        sample['objects'].sort(key=lambda obj: obj['id'])
    result.sort(key=lambda sample: sample['id'])
    packed(result)
    return result


def normalize_criteria(criteria):
    _exact(criteria, _CRITERIA_KEYS, 'criteria')
    _json(criteria)
    tolerance = _decimal(criteria['pairing_tolerance_pt'], positive=True, maximum=1000000)
    spacing = _decimal(criteria['sampling_spacing_pt'], positive=True, maximum=1000000)
    if spacing > tolerance:
        _fail('invalid_number', 'Sampling spacing must not exceed pairing tolerance.')
    _decimal(criteria['maximum_absolute_length_error_ft'])
    for key in _CRITERIA_KEYS - {'pairing_tolerance_pt', 'sampling_spacing_pt', 'maximum_absolute_length_error_ft'}:
        _decimal(criteria[key], maximum=1, text=False)
    return copy.deepcopy(criteria)


def _producer(result):
    _result_fields(result)
    for key in ('id', 'project_id', 'actor', 'reason'):
        _text(result[key])
    if (result['state'] != 'completed' or result['error'] is not None
            or result['role'] not in _PREPARED_ROLES):
        _fail('producer_invalid', 'Use a completed uncorrected result with a supported prepared-page role.')
    if type(result['unreadable']) is not bool:
        _fail('producer_invalid', 'Producer unreadability must be explicit.')
    _geometry(result['geometry'], result['source'])
    if result['source_refs'] != [result['source']]:
        _fail('source_mismatch', 'Producer source references must be exact.')
    project_scale_context(result['scale_context'], result['source'], result['geometry'])
    prepared = result['input']
    _exact(prepared, {'id', 'project_id', 'source_id', 'input_sha256', 'revision_id', 'index',
                     'sheet_id', 'image', 'renderer', 'role', 'actor', 'reason'}, 'prepared input')
    for key in ('id', 'project_id', 'source_id'):
        _text(prepared[key])
    for key in ('actor', 'reason'):
        _text(prepared[key], key, 1000)
    _hash(prepared['input_sha256'])
    if (any(prepared[k] != result['source'][k] for k in ('revision_id', 'index', 'sheet_id'))
            or prepared['project_id'] != result['project_id'] or prepared['role'] != result['role']):
        _fail('source_mismatch', 'Producer input does not belong to its exact page.')
    _exact(prepared['image'], {'sha256', 'bytes', 'width', 'height'}, 'input image')
    if prepared['image']['sha256'] != prepared['input_sha256'] or any(
            type(prepared['image'][k]) is not int or not 0 < prepared['image'][k] <= 2 ** 53 - 1
            for k in ('bytes', 'width', 'height')):
        _fail('producer_invalid', 'Prepared image metadata is invalid.')
    renderer = prepared['renderer']
    _exact(renderer, {'argv', 'artifacts'}, 'renderer')
    _array(renderer['argv'], 'renderer arguments', 100, 1)
    for argument in renderer['argv']:
        _text(argument, 'renderer argument', 1000)
    _array(renderer['artifacts'], 'renderer artifacts', 100)
    for artifact in renderer['artifacts']:
        _exact(artifact, {'path', 'sha256', 'bytes'}, 'renderer artifact')
        _text(artifact['path'], 'renderer path', 1000)
        _hash(artifact['sha256'])
        if type(artifact['bytes']) is not int or not 0 <= artifact['bytes'] <= 2 ** 53 - 1:
            _fail('producer_invalid', 'Renderer artifact byte count is invalid.')
    _exact(result['response'], {'key', 'sha256', 'bytes'}, 'response reference')
    _hash(result['response']['sha256'])
    if (result['response']['key'] != 'responses/' + result['response']['sha256'] + '.response'
            or type(result['response']['bytes']) is not int or not 0 < result['response']['bytes'] <= MAX_BYTES):
        _fail('producer_invalid', 'Original response reference is invalid.')
    _exact(result['model_identity'], {'kind', 'model', 'model_sha256', 'runtime', 'runtime_version',
        'runtime_sha256', 'prompt_sha256', 'schema_sha256', 'image_max_dimension', 'temperature', 'seed'}, 'model identity')
    for key in ('model_sha256', 'runtime_sha256', 'prompt_sha256', 'schema_sha256'):
        _hash(result['model_identity'][key])
    for key in ('model', 'runtime', 'runtime_version'):
        _text(result['model_identity'][key], key)
    model = result['model_identity']
    if (type(model['image_max_dimension']) is not int or not 0 < model['image_max_dimension'] <= 1000000
            or type(model['seed']) is not int or not 0 <= model['seed'] <= 2 ** 31 - 1):
        _fail('producer_invalid', 'Model image bound or seed is invalid.')
    _decimal(model['temperature'], maximum=100, text=False)
    if result['model_identity']['kind'] not in ('local_duct_vision', 'local_duct_vision_v2', 'local_duct_vision_v3'):
        _fail('producer_invalid', 'Only retained duct producer output belongs to this task.')
    producer_identity = result['producer_identity']
    if not isinstance(producer_identity, dict) or producer_identity.get('version') not in ('duct-source-producer-1', 'duct-source-producer-2', 'duct-source-producer-3'):
        _fail('producer_invalid', 'Unsupported producer schema.')
    identity_fields = {'version', 'sha256', 'adapter_sha256', 'transport_sha256'}
    if producer_identity['version'] in ('duct-source-producer-2', 'duct-source-producer-3'):
        identity_fields.add('legacy_adapter_sha256')
    if producer_identity['version'] == 'duct-source-producer-3':
        identity_fields.update({'arc_adapter_sha256', 'topology_sha256'})
    _exact(producer_identity, identity_fields, 'producer identity')
    for key in identity_fields - {'version'}:
        _hash(producer_identity[key])
    if result['model_identity']['kind'] != {'duct-source-producer-1': 'local_duct_vision',
            'duct-source-producer-2': 'local_duct_vision_v2',
            'duct-source-producer-3': 'local_duct_vision_v3'}[result['producer_identity']['version']]:
        _fail('producer_invalid', 'Producer and adapter versions disagree.')
    try:
        geometries, evidence = _CORE._current_context(result['current_sources'])
    except ValueError as error:
        _fail(getattr(error, 'code', 'producer_invalid'), str(error))
    if (geometries != {result['source']['sheet_id']: result['geometry']}
            or any(e['artifact_sha256'] != prepared['input_sha256'] for e in evidence.values())):
        _fail('source_mismatch', 'Producer graphics must bind this exact source and PNG.')
    _array(result['observations'], 'producer observations', MAX_OBJECTS)
    ids = set()
    for observation in result['observations']:
        try:
            _CORE.validate_observation(observation)
        except ValueError as error:
            _fail(getattr(error, 'code', 'producer_invalid'), str(error))
        if (result['producer_identity']['version'] == 'duct-source-producer-1'
                and observation['schema'] != 'duct-observation-1'):
            _fail('producer_invalid', 'Legacy producer output cannot contain a later observation schema.')
        if observation['id'] in ids:
            _fail('duplicate_identity', 'Producer observation IDs must be unique.')
        ids.add(observation['id'])
        if observation['source'] != result['source']:
            _fail('source_mismatch', 'Producer observation source differs.')
        referenced = set(observation['evidence_ids']) | set(observation['group']['evidence_ids'])
        for reading in observation['readings']:
            referenced.update(reading['evidence_ids'])
        referenced.update(observation['geometry'].get('support_ids', []))
        if not referenced <= set(evidence):
            _fail('producer_invalid', 'Producer refers to missing original evidence.')
        # Retained producer records keep their existing validation contract.
        # Canonical whole-path validation belongs to the versioned score preflight.
        if observation['geometry']['kind'] == 'planar':
            _points(observation['geometry']['points'])
    if producer_identity['version'] == 'duct-source-producer-3':
        _topology_call('normalize_assertions', result['connections'], sorted(ids), sorted(evidence))
    if result['unreadable'] and (result['observations'] or evidence or result.get('connections')):
        _fail('producer_invalid', 'Unreadable producer output must have no observations or evidence.')


def normalize_predictions(predictions, samples=None):
    _array(predictions, 'predictions', MAX_SAMPLES, 1)
    _json(predictions)
    ids = set()
    by_sample = None if samples is None else {sample['id']: sample for sample in samples}
    for prediction in predictions:
        _exact(prediction, {'sample_id', 'producer_id', 'producer_result', 'producer_result_sha256'}, 'prediction')
        _text(prediction['sample_id']); _text(prediction['producer_id'])
        _hash(prediction['producer_result_sha256'])
        if prediction['sample_id'] in ids:
            _fail('duplicate_prediction', 'Exactly one producer result is required per sample.')
        ids.add(prediction['sample_id'])
        result = prediction['producer_result']
        _result_fields(result)
        if _digest(result) != prediction['producer_result_sha256'] or result.get('id') != prediction['producer_id']:
            _fail('producer_changed', 'Expanded producer result does not match its retained digest.')
        _producer(result)
        if by_sample is not None:
            sample = by_sample.get(prediction['sample_id'])
            if sample is None:
                _fail('sample_mismatch', 'Prediction sample is outside the frozen selection.')
            context = sample['context']
            if (result['input']['source_id'] != sample['source_id']
                    or result['input']['input_sha256'] != sample['input_sha256']
                    or result['source'] != context['source'] or result['geometry'] != context['geometry']
                    or project_scale_context(result['scale_context'], result['source'], result['geometry']) != context['scale_context']):
                _fail('source_mismatch', 'Producer page, PNG or scale context differs from frozen truth.')
    if by_sample is not None and ids != set(by_sample):
        _fail('sample_mismatch', 'Predictions must cover every selected sample exactly.')
    result = sorted(copy.deepcopy(predictions), key=lambda item: item['sample_id'])
    packed(result)
    return result


validate_samples = normalize_samples
validate_context = normalize_context
validate_criteria = normalize_criteria
validate_predictions = normalize_predictions


def _sqrt_bounds(value):
    scaled = value * SQRT_SCALE ** 2
    lower_integer = isqrt(scaled.numerator // scaled.denominator)
    lower = Fraction(lower_integer, SQRT_SCALE)
    upper = lower if lower * lower == value else Fraction(lower_integer + 1, SQRT_SCALE)
    return lower, upper


def _paper_points(points, geometry):
    width = Fraction(geometry['width_micropoints'], 1000000)
    height = Fraction(geometry['height_micropoints'], 1000000)
    return [(Fraction(str(x)) * width, Fraction(str(y)) * height) for x, y in points]


def _sampling_plan(path, spacing):
    counts, maximum_gap = [], Fraction(0)
    for a, b in zip(path, path[1:]):
        squared = sum((x - y) ** 2 for x, y in zip(a, b))
        upper = _sqrt_bounds(squared)[1]
        ratio = upper / spacing
        count = max(1, (ratio.numerator + ratio.denominator - 1) // ratio.denominator)
        counts.append(count)
        maximum_gap = max(maximum_gap, upper / count)
    return counts, maximum_gap


def _sample_path(path, counts):
    points = [path[0]]
    for start, end, count in zip(path, path[1:], counts):
        points.extend(tuple(a + (b - a) * Fraction(i, count) for a, b in zip(start, end))
                      for i in range(1, count + 1))
    return points


def _point_segment_squared(point, start, end):
    vector = tuple(b - a for a, b in zip(start, end))
    norm = sum(v * v for v in vector)
    if norm == 0:
        return sum((p - a) ** 2 for p, a in zip(point, start))
    offset = tuple(p - a for p, a in zip(point, start))
    fraction = max(Fraction(0), min(Fraction(1), sum(a * b for a, b in zip(offset, vector)) / norm))
    return sum((p - a - fraction * v) ** 2 for p, a, v in zip(point, start, vector))


def _directed_bounds(sampled, full_path, gap):
    squared = max(min(_point_segment_squared(point, start, end)
                      for start, end in zip(full_path, full_path[1:])) for point in sampled)
    lower, upper = _sqrt_bounds(squared)
    return lower, upper + gap / 2


def _fixed(value, places=12, outward=None):
    factor = 10 ** places
    scaled = value * factor
    quotient, remainder = divmod(scaled.numerator, scaled.denominator)
    if outward == 'upper' and remainder:
        quotient += 1
    elif outward is None and (2 * remainder > scaled.denominator or
                              2 * remainder == scaled.denominator and quotient % 2):
        quotient += 1
    return str(quotient // factor) + '.' + str(quotient % factor).zfill(places)


def _matching(truth_ids, adjacency, removed=None):
    assigned = {}
    def augment(tid, visited):
        for pid in adjacency.get(tid, []):
            if (tid, pid) == removed or pid in visited:
                continue
            visited.add(pid)
            if pid not in assigned or augment(assigned[pid], visited):
                assigned[pid] = tid
                return True
        return False
    for tid in truth_ids:
        augment(tid, set())
    return sorted((tid, pid) for pid, tid in assigned.items())


def _preflight(samples, predictions, spacing):
    plans = {}
    generated = operations = pairs = matching_work = vertices = cover_work = curve_queries = 0
    by_prediction = {p['sample_id']: p for p in predictions}
    for sample in samples:
        candidates = [o for o in by_prediction[sample['id']]['producer_result']['observations']
                      if o['geometry']['kind'] in _SUPPORTED_GEOMETRY]
        groups = [sample['objects'], candidates]
        records = []
        for index, objects in enumerate(groups):
            rows = {}
            for obj in objects:
                shape = _truth_shape(obj) if index == 0 else obj['geometry']
                row = _canonical_shape(shape, sample['context']['geometry'])
                vertices += len(shape.get('base_points', shape.get('points', [])))
                if row['kind'] == 'planar':
                    counts, gap = _sampling_plan(row['path'], spacing)
                    count = sum(counts) + 1
                    row.update(counts=counts, gap=gap, point_error=Fraction(0), cover_radius=gap / 2,
                               target_cost=len(row['path']) - 1)
                else:
                    remaining = MAX_GENERATED_POINTS - generated
                    remaining_work = MAX_COVER_WORK - cover_work
                    if remaining < 2 or remaining_work < 1:
                        _fail('work_limit', 'Circular cover work exceeds the evaluation bound.')
                    try:
                        cover = _COVER.arc_cover(row['controls'], start_ray=row['start_ray'],
                            end_ray=row['end_ray'], spacing=spacing * 1000000,
                            max_samples=remaining, operation_limit=remaining_work)
                    except ValueError as error:
                        _fail('work_limit' if getattr(error, 'code', '').endswith('resource_indeterminate')
                              else 'invalid_path', str(error))
                    cover_work += cover['work_units']
                    # Helpers use canonical micropoints; scorer/report use PDF points.
                    row.update(sampled=[tuple(v / 1000000 for v in p) for p in cover['points']],
                               point_error=cover['point_error'] / 1000000,
                               cover_radius=cover['cover_radius'] / 1000000, target_cost=1)
                    count = len(row['sampled'])
                generated += count
                if generated > MAX_GENERATED_POINTS or vertices > MAX_TOTAL_VERTICES:
                    _fail('work_limit', 'Generated path samples exceed the preflight work bound.')
                row['count'] = count
                rows[obj['id']] = row
            records.append(rows)
        expected, observed = records
        pair_count = len(expected) * len(observed)
        pairs += pair_count
        matching_work += (len(expected) + 1) ** 2 * pair_count * 2
        for source, target in ((expected, observed), (observed, expected)):
            count = sum(r['count'] for r in source.values())
            operations += count * sum(r['target_cost'] for r in target.values())
            curve_queries += count * sum(r['kind'] != 'planar' for r in target.values())
        if (pairs > MAX_PAIRS or operations > MAX_DISTANCE_OPERATIONS
                or curve_queries > MAX_CURVE_DISTANCE_QUERIES or matching_work > MAX_MATCHING_OPERATIONS):
            _fail('work_limit', 'Distance or matching operations exceed the preflight work bound.')
        plans[sample['id']] = records
    return plans


def _continuous_bounds(source, target):
    """Distance-to-set is 1-Lipschitz; retain sample and entire-cover errors."""
    if source['kind'] == target['kind'] == 'planar':
        return _directed_bounds(source['sampled'], target['path'], source['gap'])
    lower = upper = Fraction(0)
    for point in source['sampled']:
        if target['kind'] == 'planar':
            lo, hi = _sqrt_bounds(min(_point_segment_squared(point, a, b)
                                     for a, b in zip(target['path'], target['path'][1:])))
        else:
            try:
                bound = _CURVE.point_arc_distance(tuple(v * 1000000 for v in point),
                    controls=target['controls'], start_ray=target['start_ray'], end_ray=target['end_ray'])
            except ValueError as error:
                _fail('work_limit' if getattr(error, 'code', '').endswith('resource_indeterminate')
                      else 'invalid_path', str(error))
            lo, hi = bound['lower'] / 1000000, bound['upper'] / 1000000
        lower, upper = max(lower, lo), max(upper, hi)
    return max(Fraction(0), lower - source['point_error']), upper + source['cover_radius']


def _ratio(numerator, denominator):
    return None if denominator == 0 else Fraction(numerator, denominator)


def _display_ratio(value):
    return None if value is None else float(value)


def _attribute(expected, observed):
    return {'state': 'unscored' if expected is None else 'correct' if expected == observed else 'incorrect',
            'expected': expected, 'observed': observed}


def _normalized_group(group):
    canonical = _CORE._group(dict(group, scope='ductwork'))
    for key in ('system', 'material'):
        if canonical[key] is not None:
            canonical[key] = ' '.join(canonical[key].split()).casefold()
    return canonical


def _length(expected, observation, producer):
    issues, measured = [], None
    if observation['issues']:
        issues = [{'code': 'observation_issue', 'message': issue} for issue in observation['issues']]
    else:
        evidence = {e['id']: e for c in producer['current_sources'].values() for e in c['evidence']}
        scale_context = project_scale_context(producer['scale_context'], producer['source'], producer['geometry'])
        try:
            measured, unused_method, unused_components = _CORE._measure(observation, producer['geometry'],
                scale_context['facts'], scale_context['decisions'], evidence)
        except ValueError as error:
            issues.append({'code': getattr(error, 'code', 'measurement_unavailable'), 'message': str(error)})
    error = None if expected is None or measured is None else abs(Fraction(expected) - Fraction(measured)) / Fraction('0.3048')
    return {'expected_meters': expected, 'measured_meters': measured,
            'absolute_error_ft': None if error is None else _fixed(error),
            'absolute_error_ft_ratio': None if error is None else {'numerator': str(error.numerator), 'denominator': str(error.denominator)},
            'issues': issues}, error


def _topology_report(sample, producer, assignment, matching_state, ambiguous):
    truth = sample.get('topology')
    metadata = {'truth_assertions': copy.deepcopy(truth['assertions'] if truth else []),
        'truth_evidence': copy.deepcopy(truth['evidence'] if truth else []),
        'prediction_assertions': copy.deepcopy(producer.get('connections', [])),
        'prediction_evidence': copy.deepcopy([e for c in producer['current_sources'].values() for e in c['evidence']])}
    if truth is None or 'connections' not in producer:
        return dict(metadata, state='unavailable', truth_count=len(truth['assertions']) if truth else 0,
            reason='no_independent_truth' if truth is None else 'producer_contract_has_no_topology')
    report = _topology_call('compare_assertions', truth['assertions'], producer['connections'],
        [list(pair) for pair in assignment], matching_state, ambiguous)
    report.update(metadata)
    return report


def _topology_summary(rows):
    selected = [r['topology'] for r in rows if r['topology'].get('reason') != 'no_independent_truth']
    if not selected:
        return 'unavailable'
    fields = ('truth_count', 'correct', 'incorrect', 'unresolved', 'unmatched_geometry')
    result = {key: sum(row.get(key, 0) for row in selected) for key in fields}
    result.update(scope='explicit_same_sheet_path_pairs', quantity_authority='none',
        unavailable_samples=sum(row['state'] == 'unavailable' for row in selected),
        indeterminate_samples=sum(row['state'] == 'indeterminate' for row in selected))
    incomplete = result['unavailable_samples'] or result['indeterminate_samples']
    result['state'] = 'incomplete' if incomplete else 'evaluated'
    denominator = result['truth_count']
    result['known_pair_accuracy'] = None if incomplete or not denominator else result['correct'] / denominator
    result['coverage'] = None if incomplete or not denominator else (result['correct'] + result['incorrect']) / denominator
    return result


def score(samples, predictions, criteria):
    frozen_identity = identity()
    samples = normalize_samples(samples)
    criteria = normalize_criteria(criteria)
    predictions = normalize_predictions(predictions, samples)
    packed([samples, predictions, criteria])
    tolerance = _decimal(criteria['pairing_tolerance_pt'])
    plans = _preflight(samples, predictions, _decimal(criteria['sampling_spacing_pt']))
    by_prediction = {p['sample_id']: p for p in predictions}
    sample_rows = []
    total_truth = total_planar = definite = possible = 0
    known_size = correct_size = known_status = correct_status = known_length = measured_length = 0
    errors = []
    any_indeterminate = any_ambiguous = False
    for sample in samples:
        prediction = by_prediction[sample['id']]
        producer = prediction['producer_result']
        truth = {o['id']: o for o in sample['objects']}
        candidates = {o['id']: o for o in producer['observations'] if o['geometry']['kind'] in _SUPPORTED_GEOMETRY}
        expected_plans, observed_plans = plans[sample['id']]
        for rows in (expected_plans, observed_plans):
            for value in rows.values():
                if value['kind'] == 'planar':
                    value['sampled'] = _sample_path(value['path'], value['counts'])
        pairs, adjacency, possible_adjacency = [], {}, {}
        indeterminate = False
        for tid in sorted(truth):
            a = expected_plans[tid]
            for pid in sorted(candidates):
                b = observed_plans[pid]
                first = _continuous_bounds(a, b)
                second = _continuous_bounds(b, a)
                lower, upper = max(first[0], second[0]), max(first[1], second[1])
                state = 'qualified' if upper <= tolerance else 'rejected' if lower > tolerance else 'indeterminate'
                pairs.append({'truth_id': tid, 'prediction_id': pid, 'state': state,
                              'truth_kind': a['kind'], 'prediction_kind': b['kind'],
                              'lower_pt': _fixed(lower, outward='lower'), 'upper_pt': _fixed(upper, outward='upper')})
                if state == 'qualified':
                    adjacency.setdefault(tid, []).append(pid)
                if state != 'rejected':
                    possible_adjacency.setdefault(tid, []).append(pid)
                indeterminate = indeterminate or state == 'indeterminate'
        assignment = _matching(sorted(truth), adjacency)
        possible_assignment = _matching(sorted(truth), possible_adjacency)
        ambiguous = any(len(_matching(sorted(truth), adjacency, pair)) == len(assignment) for pair in assignment)
        matching_state = 'indeterminate' if indeterminate else 'definite'
        qualification = 'indeterminate' if indeterminate else 'assignment_ambiguous' if ambiguous else 'definite'
        pair_by_id = {(p['truth_id'], p['prediction_id']): p for p in pairs}
        matched = []
        for tid, pid in assignment:
            expected = _normalized_group(truth[tid]['group'])
            observed = _normalized_group(candidates[pid]['group'])
            attributes = {k: _attribute(expected[k], observed[k]) for k in ('size', 'work_status', 'system', 'material')}
            known_size += expected['size'] is not None; correct_size += attributes['size']['state'] == 'correct'
            known_status += expected['work_status'] is not None; correct_status += attributes['work_status']['state'] == 'correct'
            length, error = _length(truth[tid]['expected_meters'], candidates[pid], producer)
            if error is not None:
                measured_length += 1; errors.append(error)
            matched.append(dict(pair_by_id[(tid, pid)], matching_state=qualification, **attributes, length=length))
        matched_truth = {t for t, p in assignment}; matched_predictions = {p for t, p in assignment}
        unmatched_predictions = sorted(set(candidates) - matched_predictions)
        duplicates = [pid for pid in unmatched_predictions if any(pid in values for values in adjacency.values())]
        sample_rows.append({'sample_id': sample['id'], 'source': copy.deepcopy(sample['context']['source']),
            'input_sha256': sample['input_sha256'], 'producer_id': prediction['producer_id'],
            'producer_result_sha256': prediction['producer_result_sha256'], 'response': copy.deepcopy(producer['response']),
            'matching_state': matching_state, 'assignment_ambiguous': ambiguous,
            'topology': _topology_report(sample, producer, assignment, matching_state, ambiguous),
            'truth_count': len(truth), 'prediction_count': len(producer['observations']),
            'supported_prediction_count': len(candidates),
            'planar_prediction_count': sum(o['geometry']['kind'] == 'planar' for o in candidates.values()),
            'circular_prediction_count': sum(o['geometry']['kind'] == 'circular_arc' for o in candidates.values()),
            'span_prediction_count': sum(o['geometry']['kind'] == 'circular_arc_span' for o in candidates.values()),
            'definite_matches': len(assignment), 'possible_matches': len(possible_assignment), 'pairs': pairs, 'matched_pairs': matched,
            'unmatched_truth_ids': sorted(set(truth) - matched_truth), 'unmatched_prediction_ids': unmatched_predictions,
            'duplicate_like_prediction_ids': duplicates,
            'unscored_predictions': [{'id': o['id'], 'kind': o['geometry']['kind'],
                'reason': o['geometry'].get('reason', 'Geometry is outside the declared centerline evaluation scope.')}
                for o in sorted(producer['observations'], key=lambda item: item['id']) if o['geometry']['kind'] not in _SUPPORTED_GEOMETRY]})
        total_truth += len(truth); total_planar += len(candidates)
        definite += len(assignment); possible += len(possible_assignment)
        known_length += sum(o['expected_meters'] is not None for o in truth.values())
        any_indeterminate = any_indeterminate or indeterminate
        any_ambiguous = any_ambiguous or ambiguous
    ratios = {'precision': _ratio(definite, total_planar), 'recall': _ratio(definite, total_truth),
              'size_accuracy': _ratio(correct_size, known_size), 'work_status_accuracy': _ratio(correct_status, known_status),
              'length_coverage': _ratio(measured_length, known_length)}
    max_error = max(errors) if errors else None
    metrics = {k: _display_ratio(v) for k, v in ratios.items()}
    metrics.update(matching_state='indeterminate' if any_indeterminate else 'definite', assignment_ambiguous=any_ambiguous,
        true_positive=definite, false_positive=total_planar - definite, false_negative=total_truth - definite,
        definite_matches=definite, possible_matches=possible,
        precision_bounds={'lower': _display_ratio(_ratio(definite, total_planar)), 'upper': _display_ratio(_ratio(possible, total_planar))},
        recall_bounds={'lower': _display_ratio(_ratio(definite, total_truth)), 'upper': _display_ratio(_ratio(possible, total_truth))},
        max_absolute_length_error_ft=None if max_error is None else _fixed(max_error))
    if any_indeterminate:
        metrics.update(precision=None, recall=None)
    thresholds = (not any_indeterminate and not any_ambiguous and all(value is not None and value >=
        _decimal(criteria['minimum_' + name], maximum=1, text=False) for name, value in ratios.items())
        and max_error is not None and max_error <= _decimal(criteria['maximum_absolute_length_error_ft']))
    report = {'version': VERSION, 'task': TASK, 'scorer_sha256': frozen_identity['sha256'],
        'geometry_scope': list(_SUPPORTED_GEOMETRY), 'truth_versions': ['legacy_planar', 'duct-path-truth-2'],
        'distance_method': 'continuous_hausdorff_bounds_rational_v2',
        'coordinate_basis': 'source_pdf_micropoints_half_even',
        'measurement_dependencies': copy.deepcopy(DEPENDENCY_SHA256), 'criteria': criteria, 'metrics': metrics,
        'samples': sample_rows, 'thresholds_met': bool(thresholds), 'quantity_authority': 'none',
        'execution_proof': 'saved_producer_results_only', 'topology': _topology_summary(sample_rows),
        'threshold_scope': 'geometry_attributes_and_length_only', 'resource_use': 'unavailable'}
    report['sha256'] = _digest(report)
    if identity() != frozen_identity:
        _fail('scorer_changed', 'Scorer identity changed during evaluation.')
    return report
