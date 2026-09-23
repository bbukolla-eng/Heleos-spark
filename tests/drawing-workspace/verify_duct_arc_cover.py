"""Controller oracle: algebraic circle points and certificate checks, no trig.

Dense rational probes support, but do not replace, the independent proof review
of whole-curve coverage. Runs against a named candidate scripts directory.
"""
from fractions import Fraction as F
import importlib.util
import json
from math import isqrt
from pathlib import Path
import sys


def load(directory):
    spec = importlib.util.spec_from_file_location('controller_arc_cover', directory / 'duct_arc_cover.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def inside(p, start, end, direction):
    a, b = direction * cross(start, p), direction * cross(p, end)
    sweep = direction * cross(start, end)
    return (a >= 0 or b >= 0) if sweep < 0 else (a >= 0 and b >= 0)


def sqrt_bounds(value):
    scale = 1 << 160
    n = isqrt((value * scale * scale).numerator // (value * scale * scale).denominator)
    lower = F(n, scale)
    return lower, lower if lower * lower == value else F(n + 1, scale)


def endpoint_box(center, radius_squared, ray):
    lo, hi = sqrt_bounds(radius_squared / dot(ray, ray))
    return [(c + min(v * lo, v * hi), c + max(v * lo, v * hi)) for c, v in zip(center, ray)]


def verify_case(module, controls, center, direction, spacing, rays=None):
    args = {} if rays is None else dict(start_ray=rays[0], end_ray=rays[1])
    result = module.arc_cover(controls, spacing=spacing, **args)
    assert set(result) == {'points', 'point_error', 'cover_radius', 'method', 'work_units'}
    assert result['method'] == 'circular_arc_cover_rational_v1'
    points, delta, cover = result['points'], result['point_error'], result['cover_radius']
    assert type(points) is tuple and 2 <= len(points) <= 20000
    assert type(delta) is F and type(cover) is F
    assert 0 <= delta <= min(spacing / 16, F(1, 1 << 80))
    assert 0 <= cover <= spacing
    assert type(result['work_units']) is int and 0 < result['work_units'] <= 1000000
    vectors = [tuple(F(v) - c for v, c in zip(p, center)) for p in controls]
    start, end = rays or (vectors[0], vectors[-1])
    radius_squared = dot(vectors[0], vectors[0])
    rlo, rhi = sqrt_bounds(radius_squared)
    for point in points:
        assert type(point) is tuple and all(type(v) is F for v in point)
        vector = tuple(v - c for v, c in zip(point, center))
        assert inside(vector, start, end, direction), 'Sample direction outside selected curve'
        assert max(F(0), rlo - delta) ** 2 <= dot(vector, vector) <= (rhi + delta) ** 2
    for point, ray in ((points[0], start), (points[-1], end)):
        box = endpoint_box(center, radius_squared, ray)
        # Interval width of the separate oracle is explicit and tiny.
        assert sum(max(abs(v - a), abs(v - b)) ** 2 for v, (a, b) in zip(point, box)) <= (delta + F(1, 1 << 140)) ** 2
    probes = 0
    # Exact rational rotation matrices produce known true circle points.
    for k in range(-64, 65):
        t = F(k, 16)
        co, si = (1 - t * t) / (1 + t * t), 2 * t / (1 + t * t)
        for sign in (1, -1):
            a, b = vectors[0]
            vector = (sign * (co * a - si * b), sign * (si * a + co * b))
            if not inside(vector, start, end, direction):
                continue
            exact = tuple(v + c for v, c in zip(vector, center))
            assert dot(vector, vector) == radius_squared
            assert min(sum((x - y) ** 2 for x, y in zip(exact, p)) for p in points) <= cover ** 2
            probes += 1
    assert probes > 0
    return len(points), probes


def verify(directory):
    module = load(directory)
    cases = [
        (((5, 0), (4, 3), (0, 5)), (0, 0), 1, None),
        (((0, 5), (4, 3), (5, 0)), (0, 0), -1, None),
        (((5, 0), (0, 5), (-5, 0)), (0, 0), 1, None),
        (((5, 0), (0, -5), (0, 5)), (0, 0), -1, None),
        (((4, 3), (-4, 3), (3, 4)), (0, 0), -1, ((3, 2), (1, 2))),
        (((5, 0), (0, 5), (-5, 0)), (0, 0), 1, ((1000, 1), (1, 1))),
        (((5, 0), (0, 5), (-5, 0)), (0, 0), 1, ((1, 1), (-2, 1))),
        (((0, 0), (0, 1), (1, 1)), (F(1, 2), F(1, 2)), -1, None),
    ]
    counts = probes = 0
    for controls, center, direction, rays in cases:
        for translate in ((0, 0), (123, -456)):
            shifted = tuple(tuple(x + dx for x, dx in zip(p, translate)) for p in controls)
            origin = tuple(F(x + dx) for x, dx in zip(center, translate))
            prior = 0
            for spacing in (F(2), F(1, 2)):
                count, checked = verify_case(module, shifted, origin, direction, spacing, rays)
                assert count >= prior
                prior = count
                counts += 1; probes += checked
    rejected = 0
    for kwargs, code in [
            ({'spacing': True}, 'arc_cover_invalid'), ({'spacing': .5}, 'arc_cover_invalid'),
            ({'spacing': F(0)}, 'arc_cover_invalid'), ({'max_samples': 1}, 'arc_cover_invalid'),
            ({'operation_limit': True}, 'arc_cover_invalid'), ({'bit_limit': 63}, 'arc_cover_invalid'),
            ({'start_ray': (2, 2), 'end_ray': (0, 1)}, 'arc_cover_invalid'),
            ({'start_ray': (1, 1)}, 'arc_cover_invalid'),
            ({'start_ray': (0, -1), 'end_ray': (0, 1)}, 'arc_cover_invalid'),
            ({'max_samples': 2}, 'arc_cover_resource_indeterminate'),
            ({'operation_limit': 1}, 'arc_cover_resource_indeterminate'),
            ({'bit_limit': 64}, 'arc_cover_resource_indeterminate')]:
        opts = {'spacing': F(1, 10)}; opts.update(kwargs)
        try:
            module.arc_cover(((5, 0), (4, 3), (0, 5)), **opts)
        except module.ArcCoverError as error:
            assert error.code == code, (kwargs, error.code)
            rejected += 1
        else:
            raise AssertionError(('accepted invalid/exhausted request', kwargs))
    return {'cases': counts, 'exact_circle_probes': probes, 'rejections': rejected,
            'scope': 'finite algebraic oracle plus independently reviewed coverage proof'}


if __name__ == '__main__':
    directory = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / 'scripts'
    print(json.dumps(verify(directory), sort_keys=True))
