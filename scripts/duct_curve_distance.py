"""Certified point-to-circular-span distance over exact rational arithmetic.

This primitive encloses one continuous Euclidean distance from an exact
rational point to an already validated closed circular span. It establishes no
quantity, connectivity edge, model admission or acceptance threshold, and it
never modifies the canonical circular-arc source that it loads.
"""
import importlib.util
from fractions import Fraction
from math import isqrt
from pathlib import Path

METHOD = "point_arc_distance_rational_v1"
PRECISION_BITS_RANGE = (32, 256)
BIT_LIMIT_RANGE = (64, 262144)
OPERATION_LIMIT_RANGE = (1, 1000000)
DEFAULT_PRECISION_BITS = 80
DEFAULT_OPERATION_LIMIT = 20000
DEFAULT_BIT_LIMIT = 16384
ZERO = Fraction(0)
_GEOMETRY_SOURCE = Path(__file__).resolve().parent / "duct_arc_geometry.py"
_RESOURCE_CODES = frozenset({"arc_resource_indeterminate", "arc_numeric_indeterminate"})
_INPUT_ERRORS = (TypeError, ZeroDivisionError, OverflowError, AttributeError, IndexError, KeyError)


def _load_geometry():
    """Load the canonical sibling by absolute path, whatever the working directory.

    The loaded module is deliberately not registered in sys.modules: this file
    only calls its public constructors and never rewrites that source.
    """
    spec = importlib.util.spec_from_file_location("duct_arc_geometry_curve_distance", _GEOMETRY_SOURCE)
    if spec is None or spec.loader is None:
        raise ImportError("The canonical circular-arc geometry source could not be loaded.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_arc = _load_geometry()


class CurveDistanceError(ValueError):
    """Stable codes: curve_distance_invalid and curve_distance_resource_indeterminate."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _invalid(message):
    raise CurveDistanceError("curve_distance_invalid", message)


def _indeterminate(message):
    raise CurveDistanceError("curve_distance_resource_indeterminate", message)


def _translated(error):
    code = getattr(error, "code", "")
    if code in _RESOURCE_CODES:
        return CurveDistanceError("curve_distance_resource_indeterminate",
                                  "The canonical circular-arc construction exhausted its own bounded budget.")
    return CurveDistanceError("curve_distance_invalid",
                              "The canonical circular-arc controls or rays were rejected: " + str(code or "arc_invalid"))


class _Work:
    """Bounded rational arithmetic for this module's own derivation and refinement."""

    def __init__(self, bit_limit, operation_limit):
        self.bit_limit, self.operation_limit, self.operations = bit_limit, operation_limit, 0

    def guard(self, *bits):
        if max(bits, default=0) > self.bit_limit:
            _indeterminate("Point-to-arc rational bit budget was exhausted.")

    def tick(self):
        self.operations += 1
        if self.operations > self.operation_limit:
            _indeterminate("Point-to-arc operation budget was exhausted.")

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
            _invalid("Point-to-arc exact division has a zero denominator.")
        self.guard(a.numerator.bit_length() + b.denominator.bit_length(),
                   a.denominator.bit_length() + b.numerator.bit_length())
        return a / b

    def cmp(self, a, b):
        self.tick()
        self.guard(a.numerator.bit_length() + b.denominator.bit_length(),
                   b.numerator.bit_length() + a.denominator.bit_length())
        return (a > b) - (a < b)


def _sub(a, b, work):
    return (work.sub(a[0], b[0]), work.sub(a[1], b[1]))


def _dot(a, b, work):
    return work.add(work.mul(a[0], b[0]), work.mul(a[1], b[1]))


def _cross(a, b, work):
    return work.sub(work.mul(a[0], b[1]), work.mul(a[1], b[0]))


def _minimum(a, b, work):
    return a if work.cmp(a, b) <= 0 else b


def _clamp(value, work):
    return value if work.cmp(value, ZERO) >= 0 else ZERO


def _sqrt_bounds(value, bits, work):
    """Outward rational enclosure of a nonnegative square root, exact when possible."""
    work.tick()
    n, d = value.numerator, value.denominator
    work.guard(n.bit_length(), d.bit_length())
    if n < 0:
        _invalid("A point-to-arc squared value cannot be negative.")
    if not n:
        return ZERO, ZERO
    root_n, root_d = isqrt(n), isqrt(d)
    if root_n * root_n == n and root_d * root_d == d:
        exact = work.q(root_n, root_d)
        return exact, exact
    work.guard(n.bit_length() + 2 * bits, bits + 1)
    k = isqrt((n << (2 * bits)) // d)
    return work.q(k, 1 << bits), work.q(k + 1, 1 << bits)


def _coordinate(value, work):
    if type(value) is not int and type(value) is not Fraction:
        _invalid("Point coordinates must be exact int or Fraction values in the canonical unit.")
    return work.q(value)


def _point(value, work):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        _invalid("A point-to-arc distance needs one point with two exact rational coordinates.")
    return tuple(_coordinate(v, work) for v in value)


def _limit(value, name, bounds):
    if type(value) is not int or not bounds[0] <= value <= bounds[1]:
        _invalid("The " + name + " limit must be an exact integer inside its documented range.")
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
        raise CurveDistanceError("curve_distance_invalid",
                                 "The canonical circular-arc controls or rays were not usable inputs.") from None


def _in_span(span, vector, work):
    """Exact directed membership from cross-product signs; no angle or epsilon."""
    direction = work.q(span.direction)
    start = tuple(work.q(v) for v in span.start_ray)
    end = tuple(work.q(v) for v in span.end_ray)
    after = work.cmp(work.mul(direction, _cross(start, vector, work)), ZERO) >= 0
    before = work.cmp(work.mul(direction, _cross(vector, end, work)), ZERO) >= 0
    return (after or before) if span.sweep_class == "major" else (after and before)


def _radial(squared_norm, radius_squared, bits, work):
    """The nearest circle point lies inside the span, so only the radii differ."""
    order = work.cmp(squared_norm, radius_squared)
    if not order:
        return ZERO, ZERO
    outer = _sqrt_bounds(squared_norm if order > 0 else radius_squared, bits, work)
    inner = _sqrt_bounds(radius_squared if order > 0 else squared_norm, bits, work)
    return _clamp(work.sub(outer[0], inner[1]), work), work.sub(outer[1], inner[0])


def _endpoints(vector, squared_norm, radius_squared, rays, bits, work):
    """Nearest point of a closed interval that excludes the radial direction."""
    total = work.add(squared_norm, radius_squared)
    lower = upper = None
    for ray in rays:
        ray_vector = tuple(work.q(v) for v in ray)
        scaled = work.div(radius_squared, _dot(ray_vector, ray_vector, work))
        projection = work.mul(work.q(2), _dot(vector, ray_vector, work))
        low, high = _sqrt_bounds(scaled, bits, work)
        first, second = work.mul(projection, low), work.mul(projection, high)
        if work.cmp(first, second) > 0:
            # A negative projection reverses which root bounds the product.
            first, second = second, first
        candidate = (work.sub(total, second), work.sub(total, first))
        lower = candidate[0] if lower is None else _minimum(lower, candidate[0], work)
        upper = candidate[1] if upper is None else _minimum(upper, candidate[1], work)
    return (_sqrt_bounds(_clamp(lower, work), bits, work)[0],
            _sqrt_bounds(_clamp(upper, work), bits, work)[1])


def _refine(evaluate, precision_bits, work):
    work.guard(precision_bits + 1)
    target = work.q(1, 1 << precision_bits)
    bits = precision_bits + 16
    while True:
        work.tick()
        lower, upper = evaluate(bits)
        if work.cmp(work.sub(upper, lower), target) <= 0:
            return lower, upper
        # An endpoint certificate loses about half of its working bits to the
        # outer square root, so double the precision instead of stepping. The
        # bit and operation guards end this loop explicitly when exhausted.
        bits *= 2


def point_arc_distance(point, *, controls, start_ray=None, end_ray=None,
                       precision_bits=DEFAULT_PRECISION_BITS,
                       operation_limit=DEFAULT_OPERATION_LIMIT,
                       bit_limit=DEFAULT_BIT_LIMIT):
    """Enclose the Euclidean distance from one point to a closed circular span."""
    precision_bits = _limit(precision_bits, "precision bits", PRECISION_BITS_RANGE)
    operation_limit = _limit(operation_limit, "operation", OPERATION_LIMIT_RANGE)
    bit_limit = _limit(bit_limit, "bit", BIT_LIMIT_RANGE)
    work = _Work(bit_limit, operation_limit)
    selected = _point(point, work)
    span = _span(controls, start_ray, end_ray)
    center = tuple(work.q(v) for v in span.center)
    radius_squared = work.q(span.radius_squared)
    vector = _sub(selected, center, work)
    squared_norm = _dot(vector, vector, work)
    if not squared_norm:
        # Every point of the curve is one radius away from the circle center.
        def evaluate(bits):
            return _sqrt_bounds(radius_squared, bits, work)
    elif _in_span(span, vector, work):
        def evaluate(bits):
            return _radial(squared_norm, radius_squared, bits, work)
    else:
        rays = (span.start_ray, span.end_ray)

        def evaluate(bits):
            return _endpoints(vector, squared_norm, radius_squared, rays, bits, work)
    lower, upper = _refine(evaluate, precision_bits, work)
    if lower < 0 or work.cmp(lower, upper) > 0:
        _invalid("The point-to-arc interval invariant failed.")
    return {"lower": lower, "upper": upper, "method": METHOD}
