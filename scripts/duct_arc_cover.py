"""Bounded whole-curve rational cover of one canonical circular span.

This primitive returns rational approximations of points of a directed closed
circular span together with two certified bounds: how far each approximation
can lie from its own true curve point, and how far any point of the true curve
can lie from the nearest returned approximation. It establishes no quantity, no
connectivity edge, no model admission and no acceptance threshold, and it never
modifies the canonical circular-arc source that it loads.

Derivation
----------
The selected interval is cut at every cardinal ray strictly inside it, so each
leaf stays inside one closed quadrant, where the circle x and y coordinates are
both monotone. The union of the two endpoint coordinate boxes therefore
encloses the whole leaf image, and the sum of the maximum absolute x and y
differences from the leaf start approximation to that union bounds the
Euclidean distance from every true leaf point to that approximation. A leaf
whose bound exceeds the requested spacing is split at the strict interior ray
primitive(u*norm1(v) + v*norm1(u)), which bisects the L1 normalised directions
and keeps making progress when the two primitive ray magnitudes differ widely.
A major span whose endpoints share one quadrant has four interior cardinals and
five initial leaves, so no four-leaf cap is assumed anywhere below.

Work accounting
---------------
``work_units`` is the exact count of operations charged to this module's own
adaptive budget: every rational construction, addition, subtraction,
multiplication, division and comparison, every square-root enclosure, every
integer ray combination and reduction, every refinement round and every
traversal step. The canonical validation inside ``make_arc``, ``root_span`` and
``make_span`` runs on the geometry module's own fixed budget and is not counted
here; those calls are fixed size because their inputs are bounded canonical
integers and bounded 256-bit primitive rays, so a caller cannot drive them
without bound. Internally generated subdivision rays may exceed that canonical
ray bound, are never offered to the canonical constructors as public spans, and
are charged bit by bit against ``bit_limit``.
"""
from collections import namedtuple
from fractions import Fraction
from functools import cmp_to_key
import importlib.util
from math import gcd, isqrt
from pathlib import Path

METHOD = "circular_arc_cover_rational_v1"
MAX_SAMPLES_RANGE = (2, 20000)
OPERATION_LIMIT_RANGE = (1, 1000000)
BIT_LIMIT_RANGE = (64, 262144)
DEFAULT_MAX_SAMPLES = 20000
DEFAULT_OPERATION_LIMIT = 1000000
DEFAULT_BIT_LIMIT = 16384
POINT_ERROR_BITS = 80
POINT_ERROR_CEILING = Fraction(1, 1 << POINT_ERROR_BITS)
POINT_ERROR_DIVISOR = 16
INITIAL_SQRT_BITS = 96
CARDINAL_RAYS = ((1, 0), (0, 1), (-1, 0), (0, -1))
ZERO = Fraction(0)
HALF = Fraction(1, 2)
_GEOMETRY_SOURCE = Path(__file__).resolve().parent / "duct_arc_geometry.py"
_RESOURCE_CODES = frozenset({"arc_resource_indeterminate", "arc_numeric_indeterminate"})
_INPUT_ERRORS = (TypeError, ZeroDivisionError, OverflowError, AttributeError, IndexError, KeyError)
_Endpoint = namedtuple("_Endpoint", "box point error")


def _load_geometry():
    """Load the canonical sibling by absolute path, whatever the working directory.

    The loaded module is deliberately not registered in sys.modules and no
    import hook or search path is changed: this file only calls its public
    constructors and never rewrites that source.
    """
    spec = importlib.util.spec_from_file_location("duct_arc_geometry_arc_cover", _GEOMETRY_SOURCE)
    if spec is None or spec.loader is None:
        raise ImportError("The canonical circular-arc geometry source could not be loaded.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_arc = _load_geometry()


class ArcCoverError(ValueError):
    """Stable codes: arc_cover_invalid and arc_cover_resource_indeterminate."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _invalid(message):
    raise ArcCoverError("arc_cover_invalid", message)


def _indeterminate(message):
    raise ArcCoverError("arc_cover_resource_indeterminate", message)


def _translated(error):
    code = getattr(error, "code", "")
    if code in _RESOURCE_CODES:
        return ArcCoverError("arc_cover_resource_indeterminate",
                             "The canonical circular-arc construction exhausted its own bounded budget.")
    return ArcCoverError("arc_cover_invalid",
                         "The canonical circular-arc controls or rays were rejected: " + str(code or "arc_invalid"))


class _Work:
    """Bounded rational arithmetic for this module's own derivation and refinement."""

    def __init__(self, bit_limit, operation_limit):
        self.bit_limit, self.operation_limit, self.operations = bit_limit, operation_limit, 0

    def guard(self, *bits):
        if max(bits, default=0) > self.bit_limit:
            _indeterminate("Circular-cover rational bit budget was exhausted.")

    def tick(self):
        self.operations += 1
        if self.operations > self.operation_limit:
            _indeterminate("Circular-cover operation budget was exhausted.")

    def q(self, value, denominator=1):
        self.tick()
        if isinstance(value, Fraction) and denominator == 1:
            self.guard(value.numerator.bit_length(), value.denominator.bit_length())
            return value
        self.guard(value.bit_length(), denominator.bit_length())
        return Fraction(value, denominator)

    def add(self, a, b):
        self.tick()
        self.guard(max(a.numerator.bit_length() + b.denominator.bit_length(),
                       b.numerator.bit_length() + a.denominator.bit_length()) + 1,
                   a.denominator.bit_length() + b.denominator.bit_length())
        return a + b

    def sub(self, a, b):
        return self.add(a, -b)

    def mul(self, a, b):
        self.tick()
        self.guard(a.numerator.bit_length() + b.numerator.bit_length(),
                   a.denominator.bit_length() + b.denominator.bit_length())
        return a * b

    def div(self, a, b):
        self.tick()
        if not b:
            _invalid("Circular-cover exact division has a zero denominator.")
        self.guard(a.numerator.bit_length() + b.denominator.bit_length(),
                   a.denominator.bit_length() + b.numerator.bit_length())
        return a / b

    def cmp(self, a, b):
        self.tick()
        self.guard(a.numerator.bit_length() + b.denominator.bit_length(),
                   b.numerator.bit_length() + a.denominator.bit_length())
        return (a > b) - (a < b)


def _dot(a, b, work):
    return work.add(work.mul(a[0], b[0]), work.mul(a[1], b[1]))


def _cross(a, b, work):
    return work.sub(work.mul(a[0], b[1]), work.mul(a[1], b[0]))


def _minimum(a, b, work):
    return a if work.cmp(a, b) <= 0 else b


def _maximum(a, b, work):
    return a if work.cmp(a, b) >= 0 else b


def _sqrt_bounds(value, bits, work):
    """Outward rational enclosure of a nonnegative square root, exact when possible."""
    work.tick()
    n, d = value.numerator, value.denominator
    work.guard(n.bit_length(), d.bit_length())
    if n < 0:
        _invalid("A circular-cover squared value cannot be negative.")
    if not n:
        return ZERO, ZERO
    root_n, root_d = isqrt(n), isqrt(d)
    if root_n * root_n == n and root_d * root_d == d:
        exact = work.q(root_n, root_d)
        return exact, exact
    work.guard(n.bit_length() + 2 * bits, bits + 1)
    k = isqrt((n << (2 * bits)) // d)
    return work.q(k, 1 << bits), work.q(k + 1, 1 << bits)


def _limit(value, name, bounds):
    if type(value) is not int or not bounds[0] <= value <= bounds[1]:
        _invalid("The " + name + " limit must be an exact integer inside its documented range.")
    return value


def _spacing(value):
    """Exact int or Fraction only: bool, float, Decimal and subclasses are refused."""
    if type(value) is not int and type(value) is not Fraction:
        _invalid("Spacing must be an exact positive int or Fraction of canonical micropoints.")
    if value <= 0:
        _invalid("Spacing must be a positive canonical micropoint distance.")
    return value


def _span(controls, start_ray, end_ray):
    """Rebuild the canonical circle and directed interval; forged objects are refused."""
    try:
        root = _arc.make_arc(controls)
        if start_ray is None and end_ray is None:
            return _arc.root_span(root)
        if start_ray is None or end_ray is None:
            _invalid("A partial span selection needs both its start and end rays.")
        return _arc.make_span(root, start_ray, end_ray)
    except _arc.ArcError as error:
        raise _translated(error) from None
    except _INPUT_ERRORS:
        raise ArcCoverError("arc_cover_invalid",
                            "The canonical circular-arc controls or rays were not usable inputs.") from None


def _root_order(root, first, second, work):
    """Exact increasing order from the root start, within a single directed turn."""
    a, b = [tuple(work.q(v) for v in ray) for ray in (first, second)]
    direction = work.q(root.direction)

    def half(vector):
        y = work.mul(direction, _cross(root.start_vector, vector, work))
        x = _dot(root.start_vector, vector, work)
        return 0 if y > 0 or (not y and x >= 0) else 1

    ha, hb = half(a), half(b)
    if ha != hb:
        return -1 if ha < hb else 1
    return -work.cmp(work.mul(direction, _cross(a, b, work)), ZERO)


def _chain(span, work):
    """The selected rays plus every cardinal strictly inside, in directed order.

    Strict comparisons drop a cardinal that coincides with either boundary, and
    the fixed four candidates keep this partition at most five leaves wide.
    """
    root, start, end = span.root, span.start_ray, span.end_ray
    interior = []
    for cardinal in CARDINAL_RAYS:
        if (_root_order(root, start, cardinal, work) < 0 and
                _root_order(root, cardinal, end, work) < 0):
            interior.append(cardinal)
    interior.sort(key=cmp_to_key(lambda a, b: _root_order(root, a, b, work)))
    return (start,) + tuple(interior) + (end,)


def _leaf_end(center, radius_squared, ray, target, work):
    """The outward coordinate box of one true ray point, its midpoint and error.

    The true point is center + ray*sqrt(radius_squared/dot(ray,ray)). Each
    coordinate box comes from the outward radical enclosure with the sign of
    that ray coordinate respected, so the box encloses the true point. The box
    midpoint is then within the sum of the two half widths of it, because the
    Euclidean norm never exceeds the L1 norm.
    """
    vector = (work.q(ray[0]), work.q(ray[1]))
    squared = work.div(radius_squared, _dot(vector, vector, work))
    bits = INITIAL_SQRT_BITS + max(abs(v).bit_length() for v in ray)
    while True:
        work.tick()
        low, high = _sqrt_bounds(squared, bits, work)
        box = []
        for axis in (0, 1):
            first, second = work.mul(vector[axis], low), work.mul(vector[axis], high)
            if work.cmp(first, second) > 0:
                first, second = second, first
            box.append((work.add(center[axis], first), work.add(center[axis], second)))
        error = work.add(work.mul(HALF, work.sub(box[0][1], box[0][0])),
                         work.mul(HALF, work.sub(box[1][1], box[1][0])))
        if work.cmp(error, target) <= 0:
            point = tuple(work.mul(HALF, work.add(side[0], side[1])) for side in box)
            return _Endpoint(tuple(box), point, error)
        # An outward enclosure of this radical loses one bit of the result for
        # each bit of the ray magnitude, so double the working precision. The
        # bit guard inside the enclosure ends this loop when it is exhausted.
        bits *= 2


def _leaf_bound(head, tail, work):
    """Bound every true leaf point against the leaf start approximation.

    Inside one closed quadrant both circle coordinates are monotone, so the
    union of the two endpoint boxes encloses the whole leaf. The maximum
    absolute x difference plus the maximum absolute y difference from the leaf
    start approximation to that union bounds the Euclidean distance from it to
    any enclosed point.
    """
    total = ZERO
    for axis in (0, 1):
        low = _minimum(head.box[axis][0], tail.box[axis][0], work)
        high = _maximum(head.box[axis][1], tail.box[axis][1], work)
        total = work.add(total, _maximum(work.sub(high, head.point[axis]),
                                         work.sub(head.point[axis], low), work))
    return total


def _bisect(first, second, work):
    """The L1 normalised midpoint direction, strictly inside one closed quadrant.

    primitive(u*norm1(v) + v*norm1(u)) is the exact midpoint of the two L1
    normalised directions. Within one closed quadrant that parameter is affine
    in the direction, so repeated bisection halves the leaf every time whatever
    the two primitive ray magnitudes are.
    """
    work.tick()
    work.guard(*(abs(v).bit_length() for ray in (first, second) for v in ray))
    work.guard(*(max(abs(v).bit_length() for v in ray) + 1
                 for ray in (first, second)))
    scales = (abs(second[0]) + abs(second[1]), abs(first[0]) + abs(first[1]))
    work.guard(*(max(abs(v).bit_length() for v in ray) + scale.bit_length() + 1
                 for ray, scale in zip((first, second), scales)))
    work.tick()
    combined = (first[0] * scales[0] + second[0] * scales[1],
                first[1] * scales[0] + second[1] * scales[1])
    work.tick()
    divisor = gcd(combined[0], combined[1])
    if not divisor:
        _invalid("A circular-cover subdivision did not select a direction.")
    return (combined[0] // divisor, combined[1] // divisor)


def arc_cover(controls, *, start_ray=None, end_ray=None, spacing,
              max_samples=DEFAULT_MAX_SAMPLES,
              operation_limit=DEFAULT_OPERATION_LIMIT,
              bit_limit=DEFAULT_BIT_LIMIT):
    """Cover one closed circular span with bounded rational approximations.

    All distances are canonical PDF micropoints. On success every point of the
    true span lies within ``cover_radius <= spacing`` of a returned
    approximation, and every approximation lies within
    ``point_error <= min(spacing/16, 2**-80)`` of its own true span point. An
    exhausted sample, bit or arithmetic budget raises
    ``arc_cover_resource_indeterminate`` instead of a coarser result.
    """
    max_samples = _limit(max_samples, "sample", MAX_SAMPLES_RANGE)
    operation_limit = _limit(operation_limit, "operation", OPERATION_LIMIT_RANGE)
    bit_limit = _limit(bit_limit, "bit", BIT_LIMIT_RANGE)
    spacing = _spacing(spacing)
    work = _Work(bit_limit, operation_limit)
    span = _span(controls, start_ray, end_ray)
    center = tuple(work.q(v) for v in span.center)
    radius_squared = work.q(span.radius_squared)
    spacing = work.q(spacing)
    target = _minimum(work.div(spacing, work.q(POINT_ERROR_DIVISOR)),
                      work.q(POINT_ERROR_CEILING), work)
    chain = _chain(span, work)
    if len(chain) > max_samples:
        _indeterminate("The cardinal partition alone exceeds the circular-cover sample budget.")
    ends = [_leaf_end(center, radius_squared, ray, target, work) for ray in chain]
    pending = [(chain[index], ends[index], chain[index + 1], ends[index + 1])
               for index in range(len(chain) - 2, -1, -1)]
    points, radius, error = [], ZERO, ZERO
    while pending:
        work.tick()
        first, head, second, tail = pending.pop()
        bound = _leaf_bound(head, tail, work)
        if work.cmp(bound, spacing) <= 0:
            points.append(head.point)
            radius = _maximum(radius, bound, work)
            error = _maximum(error, head.error, work)
            continue
        # Every pending interval still yields at least one leaf start and the
        # selected end is always returned, so this is the smallest point count
        # that splitting this leaf can commit to.
        if len(points) + len(pending) + 3 > max_samples:
            _indeterminate("Circular-cover sample budget was exhausted before the requested spacing.")
        middle = _bisect(first, second, work)
        node = _leaf_end(center, radius_squared, middle, target, work)
        pending.append((middle, node, second, tail))
        pending.append((first, head, middle, node))
    points.append(ends[-1].point)
    error = _maximum(error, ends[-1].error, work)
    if (work.cmp(radius, spacing) > 0 or work.cmp(error, target) > 0 or
            radius < 0 or error < 0 or len(points) > max_samples):
        _invalid("The circular-cover certificate invariant failed.")
    return {"points": tuple(points), "point_error": error, "cover_radius": radius,
            "method": METHOD, "work_units": work.operations}
