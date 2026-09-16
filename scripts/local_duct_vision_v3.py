"""Explicit source-image path-pair proposals; never inferred quantity authority."""
import copy
import importlib.util
from pathlib import Path


def _load(name):
    spec = importlib.util.spec_from_file_location('duct_vision_v3_' + name,
                                                 Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


legacy = _load('local_duct_vision_v2')
topology = _load('duct_topology')
transport = legacy.transport
VisionError = legacy.VisionError
SCHEMA = copy.deepcopy(legacy.SCHEMA)
_ID = legacy.legacy._ID
SCHEMA['properties']['portions']['items']['properties']['id'] = _ID
SCHEMA['properties']['portions']['items']['required'].append('id')
SCHEMA['properties']['connections'] = {'type': 'array', 'maxItems': 1000,
    'items': legacy.legacy._object({'id': _ID,
        'members': {'type': 'array', 'minItems': 2, 'maxItems': 2, 'items': _ID},
        'relation': {'enum': ['connected', 'not_connected', 'uncertain']},
        'evidence_ids': dict(legacy.legacy._IDS, minItems=1)})}
SCHEMA['required'].append('connections')
PROMPT = legacy.PROMPT + (
    ' Give each portion a unique local id. In connections, explicitly describe '
    'visible relationships between two distinct portion ids in members. A '
    'connected relation needs visible junction or continuation evidence; '
    'not_connected needs visible separation or crossover evidence. Proximity '
    'and geometric crossing alone do not prove a physical connection. Use '
    'uncertain when the visible region is ambiguous, or omit the pair. Every '
    'assertion has a unique id and nonempty evidence_ids citing its visible '
    'region or note. Never repeat an unordered pair or infer transitive '
    'connections. These are same-image undirected path-pair proposals only; '
    'do not add lengths, fittings, cross-page joins or completeness claims. '
    'Unreadable images must also have empty connections.')


def without_connections(value):
    """Return v2-shaped portions while preserving their original order."""
    result = copy.deepcopy(value)
    result.pop('connections')
    for portion in result['portions']:
        portion.pop('id')
    return result


def validate_proposal(value):
    legacy.legacy._fields(value, ('unreadable', 'portions', 'evidence', 'connections'))
    if not isinstance(value['portions'], list) or len(value['portions']) > legacy.MAX_PORTIONS:
        raise ValueError('bounded portions required')
    ids = []
    for portion in value['portions']:
        if not isinstance(portion, dict) or 'id' not in portion:
            raise ValueError('explicit local portion id required')
        ids.append(legacy.legacy._text(portion['id'], 120, True))
    legacy.validate_proposal(without_connections(value))
    topology.normalize_assertions(value['connections'], ids,
        [e['id'] for e in value['evidence']])
    if value['unreadable'] and value['connections']:
        raise ValueError('unreadable image cannot assert connections')
    return copy.deepcopy(value)


class DuctVision(transport.MechanicalVision):
    PROMPT = PROMPT
    SCHEMA = SCHEMA
    KIND = 'local_duct_vision_v3'

    def _observations(self, response, image):
        envelope = copy.deepcopy(response)
        message = envelope.get('message')
        if not isinstance(message, dict) or not isinstance(message.get('content'), str):
            raise ValueError('completion message required')
        content = message['content']
        message['content'] = '{"objects":[],"unreadable":false}'
        super()._observations(envelope, image)
        return validate_proposal(transport._decode(content))


def parse_response(raw, model, image):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= transport.MAX_RESPONSE_BYTES:
        raise ValueError('bounded raw completion required')
    parser = object.__new__(DuctVision)
    parser.model = model
    return parser._observations(transport._decode(raw), image)
