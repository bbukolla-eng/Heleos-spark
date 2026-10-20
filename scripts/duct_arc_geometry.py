"""Exact canonical circular arcs and bounded rational quantity certificates.

Inputs are already-rounded integer PDF micropoints. This module neither fits
normalized coordinates nor establishes drawing semantics/source eligibility.
"""
from collections import namedtuple
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from functools import cmp_to_key, lru_cache
from math import gcd, isqrt

METHOD = "circular_arc_rational_v1"
MAX_COORDINATE = 2 * 9007199254740991
MAX_BITS = 262144
MAX_OPERATIONS = 1000000
MAX_RECTANGLES = 128
PRECISION_LEVELS = (8, 16, 32, 64, 128, 256)
Arc = namedtuple("Arc", "points center radius_squared direction sweep_class start_vector end_vector")


class ArcError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _fail(code, message):
    raise ArcError(code, message)


class _Budget:
    def __init__(self, bit_limit=MAX_BITS, operation_limit=MAX_OPERATIONS):
        self.bit_limit, self.operation_limit, self.operations = bit_limit, operation_limit, 0

    def guard(self, *bits):
        if max(bits, default=0) > self.bit_limit:
            _fail("arc_resource_indeterminate", "Circular-arc rational bit budget was exhausted.")

    def tick(self):
        self.operations += 1
        if self.operations > self.operation_limit:
            _fail("arc_resource_indeterminate", "Circular-arc operation budget was exhausted.")

    def q(self, value, denominator=1):
        self.tick()
        if isinstance(value, Fraction) and denominator == 1:
            self.guard(value.numerator.bit_length(), value.denominator.bit_length())
            return value
        self.guard(value.bit_length(), denominator.bit_length())
        return Fraction(value, denominator)

    def add(self, a, b):
        self.tick()
        self.guard(max(a.numerator.bit_length() + b.denominator.bit_length(), b.numerator.bit_length() + a.denominator.bit_length()) + 1,
                   a.denominator.bit_length() + b.denominator.bit_length())
        return a + b

    def sub(self, a, b):
        return self.add(a, -b)

    def mul(self, a, b):
        self.tick()
        self.guard(a.numerator.bit_length() + b.numerator.bit_length(), a.denominator.bit_length() + b.denominator.bit_length())
        return a * b

    def div(self, a, b):
        self.tick()
        if not b:
            _fail("arc_invalid", "Circular-arc exact division has a zero denominator.")
        self.guard(a.numerator.bit_length() + b.denominator.bit_length(), a.denominator.bit_length() + b.numerator.bit_length())
        return a / b

    def cmp(self, a, b):
        self.tick()
        self.guard(a.numerator.bit_length() + b.denominator.bit_length(), b.numerator.bit_length() + a.denominator.bit_length())
        return (a > b) - (a < b)


def _sub(a, b, work):
    return (work.sub(a[0], b[0]), work.sub(a[1], b[1]))


def _dot(a, b, work):
    return work.add(work.mul(a[0], b[0]), work.mul(a[1], b[1]))


def _cross(a, b, work):
    return work.sub(work.mul(a[0], b[1]), work.mul(a[1], b[0]))


def _norm(a, work):
    return _dot(a, a, work)


def _minimum(a, b, work):
    return a if work.cmp(a, b) <= 0 else b


def _maximum(a, b, work):
    return a if work.cmp(a, b) >= 0 else b


def make_arc(points):
    if (not isinstance(points, (list, tuple)) or len(points) != 3 or
            any(not isinstance(p, (list, tuple)) or len(p) != 2 or
                any(type(v) is not int or abs(v) > MAX_COORDINATE for v in p) for p in points)):
        _fail("arc_invalid", "A circular arc needs three bounded canonical integer PDF micropoints.")
    points = tuple(tuple(p) for p in points)
    if len(set(points)) != 3:
        _fail("arc_invalid", "Circular-arc controls must remain pairwise distinct after canonicalization.")
    w = _Budget()
    a, t, e = [tuple(w.q(v) for v in p) for p in points]
    d, f = _sub(t, a, w), _sub(e, a, w)
    cross = _cross(d, f, w)
    if not cross:
        _fail("arc_invalid", "Collinear circular-arc controls do not define a circle.")
    denominator = w.mul(w.q(2), cross)
    dd, ff = _norm(d, w), _norm(f, w)
    h = (w.div(w.sub(w.mul(dd, f[1]), w.mul(ff, d[1])), denominator),
         w.div(w.sub(w.mul(d[0], ff), w.mul(f[0], dd)), denominator))
    center = (w.add(a[0], h[0]), w.add(a[1], h[1]))
    av, tv, ev = [_sub(p, center, w) for p in (a, t, e)]
    radius = _norm(h, w)
    if not radius or any(w.cmp(_norm(v, w), radius) for v in (av, tv, ev)):
        _fail("arc_invalid", "Circular-arc circle invariants failed.")
    direction = 1 if cross > 0 else -1
    sine = w.mul(w.q(direction), _cross(av, ev, w))
    cosine = _dot(av, ev, w)
    if w.cmp(w.add(w.mul(sine, sine), w.mul(cosine, cosine)), w.mul(radius, radius)):
        _fail("arc_invalid", "Circular-arc angular invariant failed.")
    kind = "minor" if sine > 0 else "major" if sine < 0 else "semicircle"
    if not sine and cosine != -radius:
        _fail("arc_invalid", "Circular-arc endpoints cannot encode a complete circle.")
    arc = Arc(points, center, radius, direction, kind, av, ev)
    normals = _normals(arc)
    inside = [_dot(normal, tv, w) > 0 for normal in normals]
    if not (any(inside) if kind == "major" else all(inside)):
        _fail("arc_invalid", "Through point does not lie in the selected directed arc interior.")
    return arc


def _rational(value):
    # Circle values from bounded canonical inputs are small; never serialize
    # the much larger atan-series intermediates.
    if max(value.numerator.bit_length(), value.denominator.bit_length()) > 2048:
        _fail("arc_resource_indeterminate", "Circular-arc descriptor exceeds its serialization budget.")
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def describe_arc(arc):
    return {"method": METHOD, "pdf_points": [list(p) for p in arc.points],
            "center_micropoints": [_rational(v) for v in arc.center],
            "radius_squared_micropoints": _rational(arc.radius_squared),
            "direction": "counterclockwise" if arc.direction > 0 else "clockwise", "sweep_class": arc.sweep_class}


def _normals(arc):
    a, c, s = arc.start_vector, arc.end_vector, arc.direction
    return ((-s * a[1], s * a[0]), (s * c[1], -s * c[0]))


def _rect(value, work):
    if (not isinstance(value, (list, tuple)) or len(value) != 4 or
            any(type(v) is not int and not isinstance(v, Fraction) for v in value)):
        _fail("arc_invalid", "Arc rectangles require four exact rational canonical coordinates.")
    result = tuple(work.q(v) for v in value)
    if work.cmp(result[0], result[2]) > 0 or work.cmp(result[1], result[3]) > 0:
        _fail("arc_invalid", "Arc rectangle bounds must be ordered.")
    return result


def _deduplicate(poly):
    result = []
    for p in poly:
        if not result or result[-1] != p:
            result.append(p)
    if len(result) > 1 and result[-1] == result[0]:
        result.pop()
    return result


def _clip(poly, normal, work):
    if not poly:
        return []
    result = []
    previous = poly[-1]
    fp = _dot(normal, previous, work)
    for current in poly:
        fc = _dot(normal, current, work)
        if (fp >= 0) != (fc >= 0):
            ratio = work.div(fp, work.sub(fp, fc))
            delta = _sub(current, previous, work)
            result.append((work.add(previous[0], work.mul(ratio, delta[0])),
                           work.add(previous[1], work.mul(ratio, delta[1]))))
        if fc >= 0:
            result.append(current)
        previous, fp = current, fc
    return _deduplicate(result)


def _area(poly, work):
    total = work.q(0)
    for p, q in zip(poly, poly[1:] + poly[:1]):
        total = work.add(total, _cross(p, q, work))
    return total


def _extrema(poly, work):
    values = [_norm(v, work) for v in poly]
    minimum = maximum = values[0]
    for value in values[1:]:
        if work.cmp(value, minimum) < 0: minimum = value
        if work.cmp(value, maximum) > 0: maximum = value
    area = _area(poly, work)
    if area and all(_cross(_sub(q, p, work), (-p[0], -p[1]), work) >= 0 for p, q in zip(poly, poly[1:] + poly[:1])):
        return work.q(0), maximum
    for p, q in zip(poly, poly[1:] + poly[:1]):
        delta = _sub(q, p, work)
        denominator = _norm(delta, work)
        if denominator:
            ratio = work.div(-_dot(p, delta, work), denominator)
            ratio = _maximum(Fraction(0), _minimum(Fraction(1), ratio, work), work)
            nearest = (work.add(p[0], work.mul(ratio, delta[0])), work.add(p[1], work.mul(ratio, delta[1])))
            value = _norm(nearest, work)
            if work.cmp(value, minimum) < 0: minimum = value
    return minimum, maximum


def _intersects(arc, rect, closed, work):
    x0, y0, x1, y1 = rect
    if not closed:
        if x0 == x1 or y0 == y1:
            return False
        for x, y in (arc.points[0], arc.points[2]):
            if work.cmp(x0, Fraction(x)) < 0 and work.cmp(Fraction(x), x1) < 0 and work.cmp(y0, Fraction(y)) < 0 and work.cmp(Fraction(y), y1) < 0:
                return True
    ox, oy = arc.center
    x0, y0, x1, y1 = work.sub(x0, ox), work.sub(y0, oy), work.sub(x1, ox), work.sub(y1, oy)
    poly = _deduplicate([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    normals = _normals(arc)
    pieces = [_clip(poly, normal, work) for normal in normals] if arc.sweep_class == "major" else [_clip(_clip(poly, normals[0], work), normals[1], work)]
    for piece in pieces:
        if not piece or (not closed and not _area(piece, work)):
            continue
        minimum, maximum = _extrema(piece, work)
        lower, upper = work.cmp(minimum, arc.radius_squared), work.cmp(arc.radius_squared, maximum)
        if (lower <= 0 and upper <= 0) if closed else (lower < 0 and upper < 0):
            return True
    return False


def arc_intersects_rect(arc, rect, *, closed=True):
    if type(closed) is not bool:
        _fail("arc_invalid", "Arc intersection boundary policy must be explicit.")
    work = _Budget()
    return _intersects(arc, _rect(rect, work), closed, work)


def _envelope(arc, work):
    r = work.q(arc.radius_squared)
    work.tick()
    k = isqrt(r.numerator // r.denominator)
    b = work.q(k + 1 if work.cmp(work.q(k * k), r) == 0 else k + 2)
    x, y = arc.center
    return (work.sub(x, b), work.sub(y, b), work.add(x, b), work.add(y, b))


def arc_contained_in_rect(arc, rect):
    work = _Budget()
    x0, y0, x1, y1 = _rect(rect, work)
    bx0, by0, bx1, by1 = _envelope(arc, work)
    candidates = [(bx0, by0, _minimum(x0, bx1, work), by1), (_maximum(x1, bx0, work), by0, bx1, by1),
                  (bx0, by0, bx1, _minimum(y0, by1, work)), (bx0, _maximum(y1, by0, work), bx1, by1)]
    return not any(work.cmp(a, c) < 0 and work.cmp(b, d) < 0 and _intersects(arc, (a, b, c, d), False, work) for a, b, c, d in candidates)


def arc_covered_by_rects(arc, rects):
    if not isinstance(rects, (list, tuple)) or len(rects) > MAX_RECTANGLES:
        _fail("arc_resource_indeterminate", "Circular-arc graphics are limited to 128 rectangles.")
    work = _Budget()
    bx0, by0, bx1, by1 = _envelope(arc, work)
    boxes = []
    for value in rects:
        x0, y0, x1, y1 = _rect(value, work)
        clipped = (_maximum(x0, bx0, work), _maximum(y0, by0, work), _minimum(x1, bx1, work), _minimum(y1, by1, work))
        if work.cmp(clipped[0], clipped[2]) <= 0 and work.cmp(clipped[1], clipped[3]) <= 0: boxes.append(clipped)
    sides = sorted({bx0, bx1, *(v for box in boxes for v in (box[0], box[2]))}, key=cmp_to_key(work.cmp))
    for left, right in zip(sides, sides[1:]):
        middle = work.div(work.add(left, right), work.q(2))
        intervals = [(bottom, top) for x0, bottom, x1, top in boxes if work.cmp(x0, middle) < 0 and work.cmp(middle, x1) < 0]
        intervals.sort(key=cmp_to_key(lambda a, b: work.cmp(a[0], b[0]) or work.cmp(a[1], b[1])))
        cursor = by0
        for bottom, top in intervals:
            work.tick()
            if work.cmp(bottom, cursor) > 0 and _intersects(arc, (left, cursor, right, bottom), False, work): return False
            cursor = _maximum(cursor, top, work)
        if work.cmp(cursor, by1) < 0 and _intersects(arc, (left, cursor, right, by1), False, work): return False
    return True


def _series(x, terms, work):
    if x < 0:
        lo, hi = _series(-x, terms, work)
        return -hi, -lo
    if work.cmp(x, Fraction(1, 2)) > 0:
        _fail("arc_invalid", "Atan series argument was not range reduced.")
    total, power = work.q(0), x
    square = work.mul(x, x)
    for k in range(terms):
        term = work.div(power, work.q(2 * k + 1))
        total = work.add(total, term if k % 2 == 0 else -term)
        power = work.mul(power, square)
    remainder = work.div(power, work.q(2 * terms + 1))
    return (total, work.add(total, remainder)) if terms % 2 == 0 else (work.sub(total, remainder), total)


@lru_cache(maxsize=6)
def _pi_bounds(terms):
    if terms not in PRECISION_LEVELS:
        _fail("arc_invalid", "Unsupported circular-arc precision level.")
    work = _Budget()
    a, b = _series(work.q(1, 5), terms, work), _series(work.q(1, 239), terms, work)
    return (work.sub(work.mul(work.q(16), a[0]), work.mul(work.q(4), b[1])),
            work.sub(work.mul(work.q(16), a[1]), work.mul(work.q(4), b[0])))


def _atan_bounds(x, terms, pi, work):
    if x < 0:
        lo, hi = _atan_bounds(-x, terms, pi, work)
        return -hi, -lo
    if x > 1:
        lo, hi = _atan_bounds(work.div(work.q(1), x), terms, pi, work)
        return work.sub(work.div(pi[0], work.q(2)), hi), work.sub(work.div(pi[1], work.q(2)), lo)
    if work.cmp(x, Fraction(1, 2)) > 0:
        reduced = work.div(work.sub(x, work.q(1)), work.add(x, work.q(1)))
        lo, hi = _series(reduced, terms, work)
        return work.add(work.div(pi[0], work.q(4)), lo), work.add(work.div(pi[1], work.q(4)), hi)
    return _series(x, terms, work)


def _sqrt_bounds(value, bits, work):
    work.tick()
    n, d = value.numerator, value.denominator
    work.guard(n.bit_length(), d.bit_length())
    if n < 0:
        _fail("arc_invalid", "A radius cannot have a negative square.")
    sn, sd = isqrt(n), isqrt(d)
    if sn * sn == n and sd * sd == d:
        exact = work.q(sn, sd)
        return exact, exact
    work.guard(n.bit_length() + 2 * bits, bits + 1)
    k = isqrt((n << (2 * bits)) // d)
    denominator = 1 << bits
    return work.q(k, denominator), work.q(k + 1, denominator)


def _sweep_bounds(arc, terms, work):
    pi = tuple(work.q(v) for v in _pi_bounds(terms))
    if arc.sweep_class == "semicircle": return pi
    sine = work.mul(work.q(arc.direction), _cross(arc.start_vector, arc.end_vector, work))
    cosine = _dot(arc.start_vector, arc.end_vector, work)
    q = work.div(sine, work.add(arc.radius_squared, cosine))
    lo, hi = _atan_bounds(q, terms, pi, work)
    lo, hi = work.mul(work.q(2), lo), work.mul(work.q(2), hi)
    if q < 0:
        lo, hi = work.add(work.mul(work.q(2), pi[0]), lo), work.add(work.mul(work.q(2), pi[1]), hi)
    return _maximum(work.q(0), lo, work), _minimum(work.mul(work.q(2), pi[1]), hi, work)


def _scale(value, work):
    if not isinstance(value, str) or not 0 < len(value) <= 80:
        _fail("arc_invalid", "Arc scale must be the bounded saved decimal string.")
    try:
        parsed = Decimal(value)
        if not parsed.is_finite() or parsed <= 0 or parsed > Decimal("1e12") or parsed.as_tuple().exponent < -40:
            raise InvalidOperation()
    except (InvalidOperation, ValueError):
        _fail("arc_invalid", "Arc scale must be positive and within the existing scale bounds.")
    return work.q(Fraction(parsed))


def _half_even(value, work):
    work.tick()
    work.guard(value.numerator.bit_length() + 20, value.denominator.bit_length() + 1)
    quotient, remainder = divmod(value.numerator * 1000000, value.denominator)
    twice = remainder * 2
    return quotient + (twice > value.denominator or (twice == value.denominator and quotient % 2 == 1))


def _decimal_integer(value, places):
    digits = str(value).rjust(places + 1, "0")
    return digits[:-places] + "." + digits[-places:]


def _compact_bounds(bounds, places, work):
    work.guard(4 * places + max(v.numerator.bit_length() for v in bounds))
    denominator = 10 ** places
    lower = bounds[0].numerator * denominator // bounds[0].denominator
    upper = -((-bounds[1].numerator * denominator) // bounds[1].denominator)
    return (work.q(lower, denominator), work.q(upper, denominator)), (_decimal_integer(lower, places), _decimal_integer(upper, places))


def _admit(bounds, work):
    lo, hi = bounds
    if lo < 0 or work.cmp(lo, hi) > 0:
        _fail("arc_invalid", "Circular-arc interval invariant failed.")
    if work.cmp(lo, work.q(10 ** 12)) > 0:
        _fail("arc_out_of_range", "Circular-arc quantity exceeds the supported raw meter range.")
    if work.cmp(hi, work.q(10 ** 12)) > 0: return None
    a, b = _half_even(lo, work), _half_even(hi, work)
    if a != b: return None
    if a == 0:
        _fail("arc_below_resolution", "Circular-arc quantity rounds below the positive meter resolution.")
    return _decimal_integer(a, 6)


def _measure(arc, meters_per_point, radius_only):
    work = _Budget()
    scale = work.div(_scale(meters_per_point, work), work.q(1000000))
    for terms in PRECISION_LEVELS:
        if radius_only:
            squared = work.mul(arc.radius_squared, work.mul(scale, scale))
            bounds = _sqrt_bounds(squared, 2 * terms, work)
        else:
            radius = _sqrt_bounds(arc.radius_squared, 2 * terms, work)
            angle = _sweep_bounds(arc, terms, work)
            bounds = (work.mul(work.mul(radius[0], angle[0]), scale), work.mul(work.mul(radius[1], angle[1]), scale))
        if _admit(bounds, work) is None: continue
        compact, text = _compact_bounds(bounds, 6 + 2 * terms, work)
        meters = _admit(compact, work)
        if meters is not None:
            return {"meters": meters, "certificate": {"method": METHOD,
                    "quantity": "centerline_radius" if radius_only else "arc_length", "terms": terms,
                    "radius_binary_bits": 2 * terms, "meters_per_point": meters_per_point,
                    "lower_meters": text[0], "upper_meters": text[1], "rounding": "ROUND_HALF_EVEN",
                    "resolution_meters": "0.000001", "raw_max_meters": "1000000000000"}}
    _fail("arc_numeric_indeterminate", "Circular-arc precision budget could not certify one stored meter value.")


def measure_arc(arc, meters_per_point):
    return _measure(arc, meters_per_point, False)


def measure_radius(arc, meters_per_point):
    """Call only for a consumed centerline-radius check, not an unused display."""
    return _measure(arc, meters_per_point, True)


SPAN_METHOD = "circular_arc_span_rational_v1"
MAX_RAY_BITS = 256


class ArcSpan(namedtuple("ArcSpanFields", "root start_ray end_ray sweep_class")):
    """A directed subset of one immutable three-control source arc."""
    __slots__ = ()

    @property
    def center(self):
        return self.root.center

    @property
    def radius_squared(self):
        return self.root.radius_squared

    @property
    def direction(self):
        return self.root.direction

    @property
    def start_vector(self):
        return tuple(Fraction(v) for v in self.start_ray)

    @property
    def end_vector(self):
        return tuple(Fraction(v) for v in self.end_ray)


def _root_type(root):
    if not isinstance(root, Arc):
        _fail("arc_invalid", "A span requires its validated original circular arc.")


def _span_type(span):
    if not isinstance(span, ArcSpan):
        _fail("arc_invalid", "A circular-arc span is required.")


def _ray(value, work):
    if (not isinstance(value, (list, tuple)) or len(value) != 2 or
            any(type(v) is not int or abs(v).bit_length() > MAX_RAY_BITS for v in value) or
            not any(value)):
        _fail("arc_invalid", "Arc rays require two bounded nonzero primitive integer components.")
    work.tick()
    work.guard(*(abs(v).bit_length() for v in value))
    if gcd(*value) != 1:
        _fail("arc_invalid", "Arc rays must be primitive without changing their direction.")
    return tuple(value)


def _primitive(vector, work):
    x, y = vector
    work.tick()
    work.guard(x.numerator.bit_length() + y.denominator.bit_length(),
               y.numerator.bit_length() + x.denominator.bit_length())
    a, b = x.numerator * y.denominator, y.numerator * x.denominator
    work.tick()
    divisor = gcd(a, b)
    if not divisor:
        _fail("arc_invalid", "A cut at the circle center does not select a direction.")
    return _ray((a // divisor, b // divisor), work)


def ray_from_point(root_arc, pdf_point):
    _root_type(root_arc)
    if (not isinstance(pdf_point, (list, tuple)) or len(pdf_point) != 2 or
            any(type(v) is not int or abs(v) > MAX_COORDINATE for v in pdf_point)):
        _fail("arc_invalid", "A cut needs one bounded canonical integer PDF point.")
    work = _Budget()
    point = tuple(work.q(v) for v in pdf_point)
    return _primitive(_sub(point, root_arc.center, work), work)


def _ray_order(root, first, second, work):
    """Exact increasing order from root start, within a single directed turn."""
    a, b = [tuple(work.q(v) for v in ray) for ray in (first, second)]
    direction = work.q(root.direction)

    def half(vector):
        y = work.mul(direction, _cross(root.start_vector, vector, work))
        x = _dot(root.start_vector, vector, work)
        return 0 if y > 0 or (not y and x >= 0) else 1

    ha, hb = half(a), half(b)
    if ha != hb:
        return -1 if ha < hb else 1
    return -work.cmp(work.mul(direction, _cross(a, b, work)), work.q(0))


def _make_span(root, start, end, work):
    start, end = _ray(start, work), _ray(end, work)
    root_end = _primitive(root.end_vector, work)
    if (_ray_order(root, start, end, work) >= 0 or
            _ray_order(root, end, root_end, work) > 0):
        _fail("arc_invalid", "A span must be a positive ordered interval within its original arc.")
    a, b = [tuple(work.q(v) for v in ray) for ray in (start, end)]
    sine = work.mul(work.q(root.direction), _cross(a, b, work))
    kind = "minor" if sine > 0 else "major" if sine < 0 else "semicircle"
    return ArcSpan(root, start, end, kind)


def root_span(root_arc):
    _root_type(root_arc)
    work = _Budget()
    return _make_span(root_arc, _primitive(root_arc.start_vector, work),
                      _primitive(root_arc.end_vector, work), work)


def make_span(root_arc, start_ray, end_ray):
    _root_type(root_arc)
    return _make_span(root_arc, start_ray, end_ray, _Budget())


def partition_span(parent_span, ordered_rays):
    _span_type(parent_span)
    if not isinstance(ordered_rays, (list, tuple)) or not ordered_rays:
        _fail("arc_invalid", "An arc partition requires ordered interior cuts.")
    if len(ordered_rays) > 127:
        _fail("arc_resource_indeterminate", "Arc partitions are limited to 127 cuts.")
    work = _Budget()
    rays = [_ray(value, work) for value in ordered_rays]
    chain = [parent_span.start_ray] + rays + [parent_span.end_ray]
    if any(_ray_order(parent_span.root, a, b, work) >= 0 for a, b in zip(chain, chain[1:])):
        _fail("arc_invalid", "Arc cuts must be distinct and strictly ordered inside the parent span.")
    return tuple(_make_span(parent_span.root, a, b, work) for a, b in zip(chain, chain[1:]))


def _endpoint_in_rect(root, ray, rect, closed, work):
    vector = tuple(work.q(v) for v in ray)
    norm = _norm(vector, work)

    def compare(index, bound):
        coefficient = vector[index]
        offset = work.sub(bound, root.center[index])
        if not coefficient:
            return -work.cmp(offset, work.q(0))
        sign = 1 if coefficient > 0 else -1
        if not offset or (coefficient > 0) != (offset > 0):
            return sign
        # Compare signed square roots only after separating their signs.
        lhs = work.mul(root.radius_squared, work.mul(coefficient, coefficient))
        rhs = work.mul(norm, work.mul(offset, offset))
        return sign * work.cmp(lhs, rhs)

    comparisons = [compare(0, rect[0]), compare(1, rect[1]),
                   -compare(0, rect[2]), -compare(1, rect[3])]
    return all(v >= 0 if closed else v > 0 for v in comparisons)


def endpoint_in_rect(root_arc, ray, rect, *, closed=True):
    _root_type(root_arc)
    if type(closed) is not bool:
        _fail("arc_invalid", "Arc endpoint boundary policy must be explicit.")
    work = _Budget()
    return _endpoint_in_rect(root_arc, _ray(ray, work), _rect(rect, work), closed, work)


def _span_intersects(span, rect, closed, work):
    x0, y0, x1, y1 = rect
    if not closed:
        if x0 == x1 or y0 == y1:
            return False
        if any(_endpoint_in_rect(span.root, ray, rect, False, work)
               for ray in (span.start_ray, span.end_ray)):
            return True
    ox, oy = span.center
    x0, y0, x1, y1 = work.sub(x0, ox), work.sub(y0, oy), work.sub(x1, ox), work.sub(y1, oy)
    poly = _deduplicate([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    normals = _normals(span)
    pieces = ([_clip(poly, normal, work) for normal in normals] if span.sweep_class == "major"
              else [_clip(_clip(poly, normals[0], work), normals[1], work)])
    for piece in pieces:
        if not piece or (not closed and not _area(piece, work)):
            continue
        minimum, maximum = _extrema(piece, work)
        lower, upper = work.cmp(minimum, span.radius_squared), work.cmp(span.radius_squared, maximum)
        if (lower <= 0 and upper <= 0) if closed else (lower < 0 and upper < 0):
            return True
    return False


def span_intersects_rect(span, rect, *, closed=True):
    _span_type(span)
    if type(closed) is not bool:
        _fail("arc_invalid", "Arc intersection boundary policy must be explicit.")
    work = _Budget()
    return _span_intersects(span, _rect(rect, work), closed, work)


def span_contained_in_rect(span, rect):
    _span_type(span)
    work = _Budget()
    x0, y0, x1, y1 = _rect(rect, work)
    bx0, by0, bx1, by1 = _envelope(span, work)
    candidates = [(bx0, by0, _minimum(x0, bx1, work), by1), (_maximum(x1, bx0, work), by0, bx1, by1),
                  (bx0, by0, bx1, _minimum(y0, by1, work)), (bx0, _maximum(y1, by0, work), bx1, by1)]
    return not any(work.cmp(a, c) < 0 and work.cmp(b, d) < 0 and
                   _span_intersects(span, (a, b, c, d), False, work) for a, b, c, d in candidates)


def span_covered_by_rects(span, rects):
    _span_type(span)
    if not isinstance(rects, (list, tuple)) or len(rects) > MAX_RECTANGLES:
        _fail("arc_resource_indeterminate", "Circular-arc graphics are limited to 128 rectangles.")
    work = _Budget()
    bx0, by0, bx1, by1 = _envelope(span, work)
    boxes = []
    for value in rects:
        x0, y0, x1, y1 = _rect(value, work)
        clipped = (_maximum(x0, bx0, work), _maximum(y0, by0, work),
                   _minimum(x1, bx1, work), _minimum(y1, by1, work))
        if work.cmp(clipped[0], clipped[2]) <= 0 and work.cmp(clipped[1], clipped[3]) <= 0:
            boxes.append(clipped)
    sides = sorted({bx0, bx1, *(v for box in boxes for v in (box[0], box[2]))}, key=cmp_to_key(work.cmp))
    for left, right in zip(sides, sides[1:]):
        middle = work.div(work.add(left, right), work.q(2))
        intervals = [(bottom, top) for x0, bottom, x1, top in boxes
                     if work.cmp(x0, middle) < 0 and work.cmp(middle, x1) < 0]
        intervals.sort(key=cmp_to_key(lambda a, b: work.cmp(a[0], b[0]) or work.cmp(a[1], b[1])))
        cursor = by0
        for bottom, top in intervals:
            work.tick()
            if work.cmp(bottom, cursor) > 0 and _span_intersects(span, (left, cursor, right, bottom), False, work):
                return False
            cursor = _maximum(cursor, top, work)
        if work.cmp(cursor, by1) < 0 and _span_intersects(span, (left, cursor, right, by1), False, work):
            return False
    return True


def describe_span(span):
    _span_type(span)
    return {"method": SPAN_METHOD, "base_pdf_points": [list(p) for p in span.root.points],
            "center_micropoints": [_rational(v) for v in span.center],
            "radius_squared_micropoints": _rational(span.radius_squared),
            "direction": "counterclockwise" if span.direction > 0 else "clockwise",
            "sweep_class": span.sweep_class, "start_ray": [str(v) for v in span.start_ray],
            "end_ray": [str(v) for v in span.end_ray]}


def _span_sweep_bounds(span, terms, work):
    pi = tuple(work.q(v) for v in _pi_bounds(terms))
    sine = work.mul(work.q(span.direction), _cross(span.start_vector, span.end_vector, work))
    cosine = _dot(span.start_vector, span.end_vector, work)
    if not cosine:
        factor = work.q(1 if sine > 0 else 3, 2)
        return work.mul(factor, pi[0]), work.mul(factor, pi[1])
    lo, hi = _atan_bounds(work.div(sine, cosine), terms, pi, work)
    multiple = 1 if cosine < 0 else 2 if sine < 0 else 0
    if multiple:
        lo = work.add(lo, work.mul(work.q(multiple), pi[0]))
        hi = work.add(hi, work.mul(work.q(multiple), pi[1]))
    return _maximum(work.q(0), lo, work), _minimum(work.mul(work.q(2), pi[1]), hi, work)


def measure_span(span, meters_per_point):
    _span_type(span)
    work = _Budget()
    scale = work.div(_scale(meters_per_point, work), work.q(1000000))
    for terms in PRECISION_LEVELS:
        radius = _sqrt_bounds(span.radius_squared, 2 * terms, work)
        angle = _span_sweep_bounds(span, terms, work)
        bounds = (work.mul(work.mul(radius[0], angle[0]), scale),
                  work.mul(work.mul(radius[1], angle[1]), scale))
        if _admit(bounds, work) is None:
            continue
        compact, text = _compact_bounds(bounds, 6 + 2 * terms, work)
        meters = _admit(compact, work)
        if meters is not None:
            return {"meters": meters, "certificate": {"method": SPAN_METHOD,
                    "quantity": "arc_length", "terms": terms, "radius_binary_bits": 2 * terms,
                    "meters_per_point": meters_per_point, "lower_meters": text[0], "upper_meters": text[1],
                    "rounding": "ROUND_HALF_EVEN", "resolution_meters": "0.000001",
                    "raw_max_meters": "1000000000000"}}
    _fail("arc_numeric_indeterminate", "Circular-arc precision budget could not certify one stored meter value.")
