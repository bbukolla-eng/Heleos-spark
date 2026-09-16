"""Independent algebraic checks of the bounded circular-cover primitive.

Every expectation is derived by hand from the circle, the selected rays and an
independently generated set of exact rational circle points, then compared with
the returned bounds by exact integer or Fraction arithmetic. Nothing here
reproduces the subdivision of the module under test: the cover claim is checked
against that separate point set, and each endpoint claim is checked against the
closed form centre + ray*sqrt(radius_squared/dot(ray,ray)).

These fixtures exercise the named geometric cases only. They establish no
general geometric accuracy claim, no quantity, no connectivity and no
acceptance tolerance.
"""
from decimal import Decimal
from fractions import Fraction as F
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[2] / "scripts/duct_arc_cover.py"
spec = importlib.util.spec_from_file_location("duct_arc_cover_test", PATH)
cover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cover)

QUARTER = [(5, 0), (3, 4), (0, 5)]              # centre (0,0), radius 5, ccw 0 to 90 degrees
QUARTER_CW = [(0, 5), (3, 4), (5, 0)]           # the same quadrant traversed clockwise
MAJOR_CCW = [(5, 0), (0, 5), (0, -5)]           # ccw 0 to 270 degrees, gap in the fourth quadrant
SEMI_CCW = [(5, 0), (3, 4), (-5, 0)]            # upper half
SEMI_CW = [(5, 0), (3, -4), (-5, 0)]            # lower half
FIVE_LEAF = [(4, 3), (-4, 3), (3, 4)]           # cw major, both endpoints in the first quadrant
IRRATIONAL = [(0, 0), (1, 1), (2, -1)]          # centre (7/6,-1/6), radius squared 25/18
OFFSET = (123456, -654321)
MOVED = [(x + OFFSET[0], y + OFFSET[1]) for x, y in FIVE_LEAF]
ORIGIN = (F(0), F(0))
MOVED_CENTRE = (F(OFFSET[0]), F(OFFSET[1]))
IRRATIONAL_CENTRE = (F(7, 6), F(-1, 6))
CEILING = F(1, 2 ** 80)


def signum(value):
    return (value > 0) - (value < 0)


def compare_sqrt(rational, coefficient, radicand):
    """Exact sign of rational - coefficient*sqrt(radicand) for a nonnegative radicand."""
    if not coefficient or not radicand:
        return signum(rational)
    if coefficient > 0:
        return -1 if rational <= 0 else signum(rational * rational - coefficient * coefficient * radicand)
    return 1 if rational >= 0 else signum(coefficient * coefficient * radicand - rational * rational)


def rotations(vectors):
    """Quarter turns keep a circle vector on its circle and keep it rational."""
    result = set()
    for x, y in vectors:
        result.update(((x, y), (-y, x), (-x, -y), (y, -x)))
    return tuple(sorted(result))


def circle_vectors(radius, count=16, step=16):
    """Centre relative rational circle vectors from the half-angle form.

    radius*((1-t*t)/(1+t*t), 2*t/(1+t*t)) is exactly on the circle for every
    rational t, with t = tan(theta/2). Sampling t over [-1,1] covers the right
    half plane, and the quarter turns complete the circle.
    """
    base = [(-radius, F(0))]
    for k in range(-count, count + 1):
        t = F(k, step)
        divisor = 1 + t * t
        base.append((radius * (1 - t * t) / divisor, radius * 2 * t / divisor))
    return rotations(base)


def secant_vectors(base, count=16, step=16):
    """Centre relative rational circle vectors from one known vector.

    The second intersection of base + s*d with |v| = |base| satisfies
    s = -2*(base.d)/|d|^2, so |base + s*d| = |base| exactly. This keeps the
    construction rational on a circle whose radius is irrational.
    """
    found = [base]
    for d in [(F(0), F(1))] + [(F(1), F(k, step)) for k in range(-count, count + 1)]:
        scale = -2 * (base[0] * d[0] + base[1] * d[1]) / (d[0] * d[0] + d[1] * d[1])
        found.append((base[0] + scale * d[0], base[1] + scale * d[1]))
    return rotations(found)


ROUND_FIVE = circle_vectors(F(5))
ROUND_IRRATIONAL = secant_vectors((F(-7, 6), F(1, 6)))

# Each case names one hand-derived membership predicate on centre relative
# coordinates. Every predicate is the complement of the open gap arc, read
# directly from the two selected rays and the traversal direction, and the
# leaf count is the number of cardinal rays strictly inside that interval.
CASES = (
    {"name": "ccw minor root", "controls": QUARTER, "rays": {}, "centre": ORIGIN,
     "radius_squared": F(25), "sample": ROUND_FIVE, "leaves": 1, "direction": 1,
     "inside": lambda x, y: x >= 0 and y >= 0},
    {"name": "cw minor root", "controls": QUARTER_CW, "rays": {}, "centre": ORIGIN,
     "radius_squared": F(25), "sample": ROUND_FIVE, "leaves": 1, "direction": -1,
     "inside": lambda x, y: x >= 0 and y >= 0},
    {"name": "ccw selected 45 to 90", "controls": QUARTER,
     "rays": {"start_ray": (1, 1), "end_ray": (0, 1)}, "centre": ORIGIN,
     "radius_squared": F(25), "sample": ROUND_FIVE, "leaves": 1, "direction": 1,
     "inside": lambda x, y: y >= x and x >= 0},
    {"name": "ccw major root", "controls": MAJOR_CCW, "rays": {}, "centre": ORIGIN,
     "radius_squared": F(25), "sample": ROUND_FIVE, "leaves": 3, "direction": 1,
     "inside": lambda x, y: not (x > 0 and y < 0)},
    {"name": "ccw semicircle root", "controls": SEMI_CCW, "rays": {}, "centre": ORIGIN,
     "radius_squared": F(25), "sample": ROUND_FIVE, "leaves": 2, "direction": 1,
     "inside": lambda x, y: y >= 0},
    {"name": "cw semicircle root", "controls": SEMI_CW, "rays": {}, "centre": ORIGIN,
     "radius_squared": F(25), "sample": ROUND_FIVE, "leaves": 2, "direction": -1,
     "inside": lambda x, y: y <= 0},
    {"name": "cw major root with four cardinals", "controls": FIVE_LEAF, "rays": {},
     "centre": ORIGIN, "radius_squared": F(25), "sample": ROUND_FIVE, "leaves": 5,
     "direction": -1, "inside": lambda x, y: not (4 * y > 3 * x and 4 * x > 3 * y)},
    {"name": "cw major selected with four cardinals", "controls": FIVE_LEAF,
     "rays": {"start_ray": (3, 2), "end_ray": (1, 2)}, "centre": ORIGIN,
     "radius_squared": F(25), "sample": ROUND_FIVE, "leaves": 5, "direction": -1,
     "inside": lambda x, y: not (3 * y > 2 * x and 2 * x > y)},
    {"name": "cw major translated", "controls": MOVED, "rays": {}, "centre": MOVED_CENTRE,
     "radius_squared": F(25), "sample": ROUND_FIVE, "leaves": 5, "direction": -1,
     "inside": lambda x, y: not (4 * y > 3 * x and 4 * x > 3 * y)},
    {"name": "cw major irrational radius", "controls": IRRATIONAL, "rays": {},
     "centre": IRRATIONAL_CENTRE, "radius_squared": F(25, 18), "sample": ROUND_IRRATIONAL,
     "leaves": 3, "direction": -1,
     "inside": lambda x, y: not (x + 7 * y < 0 and x + y < 0)},
)


class ArcCoverTests(unittest.TestCase):
    def certificate(self, result, spacing):
        """Every documented key, type and required bound of the contract."""
        self.assertEqual(sorted(result),
                         ["cover_radius", "method", "point_error", "points", "work_units"])
        self.assertEqual(result["method"], "circular_arc_cover_rational_v1")
        points, radius, error = result["points"], result["cover_radius"], result["point_error"]
        self.assertIs(type(points), tuple)
        self.assertGreaterEqual(len(points), 2)
        for point in points:
            self.assertIs(type(point), tuple)
            self.assertEqual(len(point), 2)
            self.assertIs(type(point[0]), F)
            self.assertIs(type(point[1]), F)
        self.assertIs(type(radius), F)
        self.assertIs(type(error), F)
        self.assertGreaterEqual(radius, 0)
        self.assertGreaterEqual(error, 0)
        self.assertLessEqual(radius, spacing)
        self.assertLessEqual(error, min(F(spacing, 16), CEILING))
        self.assertIs(type(result["work_units"]), int)
        self.assertGreater(result["work_units"], 0)
        return points, radius, error

    def on_circle(self, points, centre, radius_squared, error):
        """Exact (r-e)^2 <= |p-c|^2 <= (r+e)^2 with r = sqrt(radius_squared)."""
        for point in points:
            v = (point[0] - centre[0], point[1] - centre[1])
            offset = v[0] * v[0] + v[1] * v[1] - radius_squared - error * error
            self.assertGreaterEqual(compare_sqrt(offset, -2 * error, radius_squared), 0)
            self.assertLessEqual(compare_sqrt(offset, 2 * error, radius_squared), 0)

    def at_ray(self, point, centre, radius_squared, ray, error):
        """The true ray point is centre + ray*sqrt(radius_squared/dot(ray,ray))."""
        v = (point[0] - centre[0], point[1] - centre[1])
        squared = F(radius_squared, ray[0] * ray[0] + ray[1] * ray[1])
        total = v[0] * v[0] + v[1] * v[1] + radius_squared - error * error
        projection = v[0] * ray[0] + v[1] * ray[1]
        self.assertLessEqual(compare_sqrt(total, 2 * projection, squared), 0)

    def covered(self, points, radius, centre, sample, inside):
        """Every independently generated curve point is within cover_radius."""
        limit, checked = radius * radius, 0
        for vx, vy in sample:
            if not inside(vx, vy):
                continue
            checked += 1
            x, y = centre[0] + vx, centre[1] + vy
            for qx, qy in points:
                if (x - qx) * (x - qx) + (y - qy) * (y - qy) <= limit:
                    break
            else:
                self.fail("No returned sample covers the curve point " + str((x, y)))
        self.assertGreater(checked, 5)
        return checked

    def advances(self, points, centre, direction):
        """Consecutive samples turn strictly the selected way, never backwards."""
        for first, second in zip(points, points[1:]):
            a = (first[0] - centre[0], first[1] - centre[1])
            b = (second[0] - centre[0], second[1] - centre[1])
            self.assertGreater(direction * (a[0] * b[1] - a[1] * b[0]), 0)

    def code(self, expected, **kwargs):
        with self.assertRaises(cover.ArcCoverError) as caught:
            cover.arc_cover(**kwargs)
        self.assertIs(type(caught.exception), cover.ArcCoverError)
        self.assertEqual(caught.exception.code, expected)
        return caught.exception

    def test_five_leaf_major_span_partition_is_exact(self):
        # (4,3),(-4,3),(3,4) is the clockwise major arc of radius 5 about the
        # origin from (4,3) to (3,4). All four cardinals are strictly inside,
        # so the partition has five leaves and six exactly rational endpoints.
        expected = ((F(4), F(3)), (F(5), F(0)), (F(0), F(-5)),
                    (F(-5), F(0)), (F(0), F(5)), (F(3), F(4)))
        for spacing in (20, 10, F(21, 2)):
            with self.subTest(spacing=spacing):
                result = cover.arc_cover(FIVE_LEAF, spacing=spacing)
                points, radius, error = self.certificate(result, spacing)
                self.assertEqual(points, expected)
                # The widest leaf runs from (5,0) to (0,-5) through the fourth
                # quadrant, so its enclosing box is [0,5]x[-5,0] and the L1
                # bound from (5,0) is 5 + 5 exactly.
                self.assertEqual(radius, F(10))
                self.assertEqual(error, F(0))
                self.advances(points, ORIGIN, -1)
        finer = cover.arc_cover(FIVE_LEAF, spacing=9)
        points, radius, _ = self.certificate(finer, 9)
        self.assertGreater(len(points), 6)
        self.assertEqual(points[0], expected[0])
        self.assertEqual(points[-1], expected[-1])
        for exact in expected:
            self.assertIn(exact, points)

    def test_selected_rays_between_irrational_endpoints(self):
        # Rays (3,2) and (1,2) also enclose all four cardinals on the same
        # clockwise major root, and both selected endpoints are irrational.
        result = cover.arc_cover(FIVE_LEAF, start_ray=(3, 2), end_ray=(1, 2), spacing=10)
        points, radius, error = self.certificate(result, 10)
        self.assertEqual(len(points), 6)
        self.assertEqual(points[1:5], ((F(5), F(0)), (F(0), F(-5)), (F(-5), F(0)), (F(0), F(5))))
        self.assertEqual(radius, F(10))
        self.assertGreater(error, 0)
        self.at_ray(points[0], ORIGIN, F(25), (3, 2), error)
        self.at_ray(points[-1], ORIGIN, F(25), (1, 2), error)
        # 5*(3,2)/sqrt(13) = (4.160251,2.773500) and 5*(1,2)/sqrt(5) = (2.236067,4.472135).
        self.assertGreater(points[0][0], F(4160251, 10 ** 6))
        self.assertLess(points[0][0], F(4160252, 10 ** 6))
        self.assertGreater(points[0][1], F(2773500, 10 ** 6))
        self.assertLess(points[0][1], F(2773501, 10 ** 6))
        self.assertGreater(points[-1][0], F(2236067, 10 ** 6))
        self.assertLess(points[-1][0], F(2236068, 10 ** 6))
        self.assertGreater(points[-1][1], F(4472135, 10 ** 6))
        self.assertLess(points[-1][1], F(4472136, 10 ** 6))
        self.advances(points, ORIGIN, -1)
        self.on_circle(points, ORIGIN, F(25), error)

    def test_quarter_root_span_has_one_exact_leaf(self):
        result = cover.arc_cover(QUARTER, spacing=20)
        points, radius, error = self.certificate(result, 20)
        self.assertEqual(points, ((F(5), F(0)), (F(0), F(5))))
        self.assertEqual(radius, F(10))
        self.assertEqual(error, F(0))
        chord = (F(5, 2), F(5, 2))
        self.assertNotIn(chord, points)
        self.assertLess(chord[0] * chord[0] + chord[1] * chord[1], 25)

    def test_every_case_certifies_points_order_and_cover(self):
        for case in CASES:
            with self.subTest(case=case["name"]):
                result = cover.arc_cover(case["controls"], spacing=1, **case["rays"])
                points, radius, error = self.certificate(result, 1)
                self.assertGreaterEqual(len(points), case["leaves"] + 1)
                self.on_circle(points, case["centre"], case["radius_squared"], error)
                self.covered(points, radius, case["centre"], case["sample"], case["inside"])
                self.advances(points, case["centre"], case["direction"])
                # Chord midpoints of the returned polyline stay strictly inside
                # the circle, so no returned sample was taken from a chord.
                for first, second in zip(points, points[1:]):
                    middle = ((first[0] + second[0]) / 2, (first[1] + second[1]) / 2)
                    v = (middle[0] - case["centre"][0], middle[1] - case["centre"][1])
                    self.assertLess(v[0] * v[0] + v[1] * v[1], case["radius_squared"])

    def test_finer_spacing_refines_the_cover(self):
        previous = None
        for spacing in (F(4), F(1), F(1, 4), F(1, 16)):
            with self.subTest(spacing=spacing):
                result = cover.arc_cover(FIVE_LEAF, spacing=spacing)
                points, radius, error = self.certificate(result, spacing)
                if spacing >= F(1, 4):
                    self.covered(points, radius, ORIGIN, ROUND_FIVE,
                                 lambda x, y: not (4 * y > 3 * x and 4 * x > 3 * y))
                if previous is not None:
                    self.assertGreater(len(points), previous[0])
                    self.assertLessEqual(radius, previous[1])
                previous = (len(points), radius)
        # A cover radius of 1/16 on a radius 5 arc of about six radians needs
        # several hundred samples; the default budget must still certify it.
        self.assertGreater(previous[0], 200)

    def test_translated_circle_reproduces_the_same_relative_cover(self):
        for spacing in (F(1), F(1, 4)):
            with self.subTest(spacing=spacing):
                base = cover.arc_cover(FIVE_LEAF, spacing=spacing)
                moved = cover.arc_cover(MOVED, spacing=spacing)
                self.certificate(base, spacing)
                self.certificate(moved, spacing)
                self.assertEqual(moved["points"],
                                 tuple((x + OFFSET[0], y + OFFSET[1]) for x, y in base["points"]))
                self.assertEqual(moved["cover_radius"], base["cover_radius"])
                self.assertEqual(moved["point_error"], base["point_error"])
                self.assertEqual(moved["work_units"], base["work_units"])

    def test_unequal_primitive_ray_magnitudes_are_bisected(self):
        # (1,1000) has L1 norm 1001 against 1 for (0,1). An unnormalised sum of
        # the two rays would step almost nowhere and never reach the spacing.
        narrow = cover.arc_cover(QUARTER, start_ray=(1, 1000), end_ray=(0, 1), spacing=F(1, 1000))
        points, radius, error = self.certificate(narrow, F(1, 1000))
        self.assertGreater(len(points), 2)
        self.assertEqual(points[-1], (F(0), F(5)))
        self.at_ray(points[0], ORIGIN, F(25), (1, 1000), error)
        self.advances(points, ORIGIN, 1)
        self.on_circle(points, ORIGIN, F(25), error)
        wide = cover.arc_cover(FIVE_LEAF, start_ray=(3, 2), end_ray=(1, 2), spacing=F(1, 2))
        points, radius, error = self.certificate(wide, F(1, 2))
        self.covered(points, radius, ORIGIN, ROUND_FIVE,
                     lambda x, y: not (3 * y > 2 * x and 2 * x > y))
        self.on_circle(points, ORIGIN, F(25), error)

    def test_irrational_radius_keeps_exact_bounds(self):
        result = cover.arc_cover(IRRATIONAL, spacing=F(1, 8))
        points, radius, error = self.certificate(result, F(1, 8))
        self.assertGreater(error, 0)
        self.on_circle(points, IRRATIONAL_CENTRE, F(25, 18), error)
        self.covered(points, radius, IRRATIONAL_CENTRE, ROUND_IRRATIONAL,
                     lambda x, y: not (x + 7 * y < 0 and x + y < 0))
        # The controls (0,0) and (2,-1) are the selected endpoints of the root
        # span, at the primitive centre relative rays (-7,1) and (1,-1).
        self.assertEqual(points[0], (F(0), F(0)))
        self.at_ray(points[0], IRRATIONAL_CENTRE, F(25, 18), (-7, 1), error)
        self.at_ray(points[-1], IRRATIONAL_CENTRE, F(25, 18), (1, -1), error)

    def test_invalid_controls_rays_spacing_and_limits(self):
        class Forged:
            centre = center = (0, 0)
            radius_squared, direction = 25, 1
            start_ray, end_ray, sweep_class = (1, 0), (0, 1), "minor"

        class Wider(int):
            pass

        class Rate(F):
            pass

        self.assertTrue(issubclass(cover.ArcCoverError, ValueError))
        with self.assertRaises(TypeError):
            cover.arc_cover(QUARTER)
        for controls in (None, 5, "abc", QUARTER[:2], [(0, 0), (1, 1), (2, 2)],
                         [(0, 0), (1, 1), (0, 0)], [(0, 0), (1.0, 1), (2, 0)],
                         [(0, 0), (True, 1), (2, 0)], [(0, 0), (1, 1), (2, 0), (3, 3)],
                         [(0, 0, 0), (1, 1, 1), (2, 0, 2)], [(0, 0), (1, 1), (2, F(1, 2))],
                         Forged(), (Forged(), Forged(), Forged())):
            with self.subTest(controls=controls):
                self.code("arc_cover_invalid", controls=controls, spacing=1)
        for start, end in ((None, (0, 1)), ((1, 0), None), ((0, 0), (0, 1)), ((2, 2), (0, 1)),
                           ((0, 1), (1, 0)), ((1, 0), (-1, 0)), ((1, -1), (0, 1)),
                           ((1.0, 1), (0, 1)), ((True, 1), (0, 1)), (5, (0, 1)),
                           ((1, 0), (0, 1, 2)), ((1, 0), (1, 0))):
            with self.subTest(start=start, end=end):
                self.code("arc_cover_invalid", controls=QUARTER, spacing=1,
                          start_ray=start, end_ray=end)
        for spacing in (0, -1, F(0), F(-1, 2), 1.0, 0.5, True, False, None, "1",
                        Decimal("1"), Decimal("0.5"), Wider(1), Rate(1, 2), 1 + 0j, [1]):
            with self.subTest(spacing=spacing):
                self.code("arc_cover_invalid", controls=QUARTER, spacing=spacing)
        for name, value in [("max_samples", 1), ("max_samples", 0), ("max_samples", -1),
                            ("max_samples", 20001), ("max_samples", True), ("max_samples", 10.0),
                            ("max_samples", F(10)), ("max_samples", None),
                            ("operation_limit", 0), ("operation_limit", -1),
                            ("operation_limit", 10 ** 6 + 1), ("operation_limit", True),
                            ("operation_limit", F(100)), ("bit_limit", 63), ("bit_limit", 0),
                            ("bit_limit", 262145), ("bit_limit", True), ("bit_limit", 128.0)]:
            with self.subTest(limit=name, value=value):
                self.code("arc_cover_invalid", controls=QUARTER, spacing=1, **{name: value})
        leaked = self.code("arc_cover_invalid", controls=[(0, 0), (1, 1), (2, 2)], spacing=1)
        self.assertNotIsInstance(leaked, cover._arc.ArcError)
        self.assertNotIsInstance(leaked, (TypeError, ZeroDivisionError))

    def test_exhausted_sample_operation_and_bit_budgets(self):
        # Five leaves need six samples, so a smaller budget can never certify
        # that partition and must not return a coarser one instead.
        for samples in (2, 3, 5):
            with self.subTest(max_samples=samples):
                self.code("arc_cover_resource_indeterminate", controls=FIVE_LEAF,
                          spacing=20, max_samples=samples)
        accepted = cover.arc_cover(FIVE_LEAF, spacing=20, max_samples=6)
        self.assertEqual(len(accepted["points"]), 6)
        self.code("arc_cover_resource_indeterminate", controls=QUARTER,
                  spacing=F(1, 64), max_samples=10)
        for limit in (1, 2, 40):
            with self.subTest(operation_limit=limit):
                self.code("arc_cover_resource_indeterminate", controls=FIVE_LEAF,
                          spacing=20, operation_limit=limit)
        self.code("arc_cover_resource_indeterminate", controls=QUARTER,
                  spacing=F(1, 16), operation_limit=200)
        for limit in (64, 80):
            with self.subTest(bit_limit=limit):
                self.code("arc_cover_resource_indeterminate", controls=FIVE_LEAF,
                          spacing=20, bit_limit=limit)
        self.code("arc_cover_resource_indeterminate", controls=QUARTER,
                  spacing=F(1, 4), bit_limit=100)
        # A spacing far below the working precision exhausts the bit budget
        # before any coarse cover may be returned.
        self.code("arc_cover_resource_indeterminate", controls=QUARTER, spacing=F(1, 2 ** 20000))
        generous = cover.arc_cover(FIVE_LEAF, spacing=F(1, 2), max_samples=20000,
                                   operation_limit=1000000, bit_limit=262144)
        self.certificate(generous, F(1, 2))

    def test_work_units_are_counted_and_deterministic(self):
        coarse = cover.arc_cover(FIVE_LEAF, spacing=20)
        fine = cover.arc_cover(FIVE_LEAF, spacing=F(1, 2))
        self.assertGreater(fine["work_units"], coarse["work_units"])
        self.assertLessEqual(fine["work_units"], 1000000)
        repeated = cover.arc_cover(FIVE_LEAF, spacing=20)
        self.assertEqual(repeated["work_units"], coarse["work_units"])
        self.assertEqual(repeated["points"], coarse["points"])
        self.code("arc_cover_resource_indeterminate", controls=FIVE_LEAF, spacing=20,
                  operation_limit=coarse["work_units"] - 1)


if __name__ == "__main__":
    unittest.main()
