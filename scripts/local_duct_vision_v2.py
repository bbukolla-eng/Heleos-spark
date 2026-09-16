"""Versioned circular-arc proposals over the preserved v1 transport contract.

The proposal supplies three ordered visible controls and typed source readings.
Canonical geometry, scale, full-path support and quantities remain downstream.
Python 3.9+, standard library only.
"""
import copy
import importlib.util
from pathlib import Path


_spec = importlib.util.spec_from_file_location(
    'duct_vision_v2_legacy', Path(__file__).with_name('local_duct_vision.py'))
legacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(legacy)
transport = legacy.transport
VisionError = legacy.VisionError
MAX_PORTIONS = legacy.MAX_PORTIONS
MAX_EVIDENCE = legacy.MAX_EVIDENCE

SCHEMA = copy.deepcopy(legacy.SCHEMA)
SCHEMA['properties']['portions']['items']['properties']['geometry']['anyOf'].append(
    legacy._object({'kind': {'const': 'circular_arc'},
        'points': dict(legacy._POINTS, minItems=3, maxItems=3),
        'dimension_check_ids': legacy._IDS, 'radius_check_ids': legacy._IDS}))
PROMPT = legacy.PROMPT.replace(
    'Curves, flex, hidden or ambiguous portions use unsupported geometry and explain the reason.',
    'A visibly supported simple planar circular arc uses circular_arc geometry with '
    'exactly three ordered centerline points: start, through, end. The through point '
    'selects the actual drawn sweep, including a major arc; do not assume the shorter '
    'sweep or substitute a chord or polyline. Cite null-text graphic regions for '
    'the entire curved path, including its bulge between controls. Never supply a '
    'calculated center, radius, angle or length. dimension_check_ids refer only to '
    'explicit whole-arc length readings. radius_check_ids refer only to explicitly '
    'supported CENTERLINE radius readings. These lists are disjoint and both use '
    'existing kind=length readings with their original value, unit and visible text '
    'evidence. Empty lists are allowed when no such reading is supported. Untyped R, '
    'inside/outside radius, diameter and chord annotations cannot be reinterpreted '
    'as centerline radius or whole-arc length. Keep ambiguous annotations as '
    'unconsumed evidence and an issue. Full circles, arbitrary curves, nonplanar '
    'curves, flex, hidden or ambiguous portions use unsupported geometry and explain '
    'the reason. Existing straight/polyline bends remain planar geometry.')


def validate_proposal(value):
    """Validate v2 additions while preserving every v1 shape and reading rule."""
    legacy._fields(value, ('unreadable', 'portions', 'evidence'))
    if (not isinstance(value['portions'], list) or len(value['portions']) > MAX_PORTIONS
            or not isinstance(value['evidence'], list) or len(value['evidence']) > MAX_EVIDENCE):
        raise ValueError('invalid page observation')
    translated = copy.deepcopy(value)
    arc_indices = []
    for index, portion in enumerate(translated['portions']):
        geometry = portion.get('geometry') if isinstance(portion, dict) else None
        if not isinstance(geometry, dict) or geometry.get('kind') != 'circular_arc':
            continue
        legacy._fields(geometry, ('kind', 'points', 'dimension_check_ids', 'radius_check_ids'))
        legacy._points(geometry['points'])
        if len(geometry['points']) != 3 or len({tuple(p) for p in geometry['points']}) != 3:
            raise ValueError('circular arc requires three distinct ordered controls')
        whole = legacy._ids(geometry['dimension_check_ids'])
        radius = legacy._ids(geometry['radius_check_ids'])
        if whole & radius:
            raise ValueError('arc radius and whole-length reading roles must be disjoint')
        # Reuse v1 structural validation, not its planar measurement semantics.
        # The original arc is returned below and never published as a polyline.
        portion['geometry'] = {'kind': 'planar', 'points': geometry['points'],
            'dimension_check_ids': geometry['dimension_check_ids'] + geometry['radius_check_ids']}
        arc_indices.append(index)
    legacy.validate_proposal(translated)
    evidence = {region['id']: region for region in value['evidence']}
    for index in arc_indices:
        portion = value['portions'][index]
        readings = {reading['id']: reading for reading in portion['readings']}
        for ref in portion['geometry']['radius_check_ids']:
            if not any(isinstance(evidence[eid]['text'], str) and evidence[eid]['text'].strip()
                       for eid in readings[ref]['evidence_ids']):
                raise ValueError('centerline radius reading requires visible text evidence')
    return copy.deepcopy(value)


class DuctVision(transport.MechanicalVision):
    PROMPT = PROMPT
    SCHEMA = SCHEMA
    KIND = 'local_duct_vision_v2'

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
    """Replay original v2 bytes without creating a runtime or executing I/O."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= transport.MAX_RESPONSE_BYTES:
        raise ValueError('bounded raw completion required')
    parser = object.__new__(DuctVision)
    parser.model = model
    return parser._observations(transport._decode(raw), image)
