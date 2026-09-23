"""Closed-form and exact rational checks only; no product, model or native imports.

Every expectation is written as the algebraic value sqrt(total - 2*p*sqrt(s))
derived from the circle, the point and the selected ray, then compared with the
returned certificate by exact integer arithmetic. Nothing here is computed by
the implementation under test.
"""
from decimal import Decimal
from fractions import Fraction as F
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[2] / "scripts/duct_curve_distance.py"
spec = importlib.util.spec_from_file_location("duct_curve_distance_test", PATH)
curve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(curve)

QUARTER = [(5, 0), (3, 4), (0, 5)]              # centre (0,0), radius 5, ccw 0 to 90 degrees
QUARTER_CW = [(0, 5), (3, 4), (5, 0)]           # the same quadrant traversed clockwise
MAJOR_CCW = [(5, 0), (0, 5), (0, -5)]           # ccw 0 to 270 degrees, gap in the fourth quadrant
MAJOR_CW = [(5, 0), (-5, 0), (0, 5)]            # cw 0 to 90 degrees, gap in the open first quadrant
SEMI_CCW = [(5, 0), (3, 4), (-5, 0)]            # upper half
SEMI_CW = [(5, 0), (3, -4), (-5, 0)]            # lower half
RATIONAL = [(0, 0), (4, 3), (8, 0)]             # centre (4,-7/6), radius 25/6
SCALED = [(115, -200), (109, -188), (100, -185)]  # QUARTER tripled, then translated by (100,-200)
BILLION = 10 ** 9


def signum(value):
    return (value > 0) - (value < 0)


def compare_sqrt(rational, coefficient, radicand):
    """Exact sign of rational - coefficient*sqrt(radicand) for a nonnegative radicand."""
    if not coefficient or not radicand:
        return signum(rational)
    if coefficient > 0:
        return -1 if rational <= 0 else signum(rational * rational - coefficient * coefficient * radicand)
    return 1 if rational >= 0 else signum(coefficient * coefficient * radicand - rational * rational)


class CurveDistanceTests(unittest.TestCase):
    def certificate(self, result, bits=80):
        lower, upper = result["lower"], result["upper"]
        self.assertEqual(sorted(result), ["lower", "method", "upper"])
        self.assertEqual(result["method"], "point_arc_distance_rational_v1")
        self.assertIs(type(lower), F)
        self.assertIs(type(upper), F)
        self.assertGreaterEqual(lower, 0)
        self.assertLessEqual(lower, upper)
        self.assertLessEqual(upper - lower, F(1, 2 ** bits))
        return lower, upper

    def encloses(self, result, total, projection, radicand, bits=80):
        """The true distance is sqrt(total - 2*projection*sqrt(radicand))."""
        lower, upper = self.certificate(result, bits)
        self.assertGreaterEqual(compare_sqrt(total - lower * lower, 2 * projection, radicand), 0)
        self.assertLessEqual(compare_sqrt(total - upper * upper, 2 * projection, radicand), 0)
        return lower, upper

    def radial(self, result, squared_norm, radius_squared, bits=80):
        """A nearest point inside the span leaves abs(sqrt(V) - sqrt(R2))."""
        return self.encloses(result, squared_norm + radius_squared, 1,
                             squared_norm * radius_squared, bits)

    def code(self, expected, **kwargs):
        with self.assertRaises(curve.CurveDistanceError) as caught:
            curve.point_arc_distance(**kwargs)
        self.assertIs(type(caught.exception), curve.CurveDistanceError)
        self.assertEqual(caught.exception.code, expected)
        return caught.exception

    def test_centre_on_curve_and_exactly_representable_certificates(self):
        centre = curve.point_arc_distance((0, 0), controls=QUARTER)
        self.assertEqual((centre["lower"], centre["upper"]), (F(5), F(5)))
        on_curve = curve.point_arc_distance((3, 4), controls=QUARTER)
        self.assertEqual((on_curve["lower"], on_curve["upper"]), (F(0), F(0)))
        # A rational centre and radius: every arc point is 25/6 from (4,-7/6).
        rational_centre = curve.point_arc_distance((4, F(-7, 6)), controls=RATIONAL)
        self.assertEqual((rational_centre["lower"], rational_centre["upper"]), (F(25, 6), F(25, 6)))
        inside = curve.point_arc_distance((4, 0), controls=RATIONAL)
        self.assertEqual((inside["lower"], inside["upper"]), (F(3), F(3)))
        for result in (centre, on_curve, rational_centre, inside):
            self.certificate(result)

    def test_radial_distances_inside_and_outside_the_radius(self):
        near = curve.point_arc_distance((1, 1), controls=QUARTER)
        self.radial(near, 2, 25)
        self.assertGreater(near["lower"], F(3585, 1000))      # 5 - sqrt(2) = 3.58578...
        self.assertLess(near["upper"], F(3586, 1000))
        far = curve.point_arc_distance((10, 10), controls=QUARTER)
        self.radial(far, 200, 25)
        self.assertGreater(far["lower"], F(9142, 1000))       # sqrt(200) - 5 = 9.14213...
        self.assertLess(far["upper"], F(9143, 1000))

    def test_each_endpoint_is_selected_outside_the_span(self):
        start = curve.point_arc_distance((20, -1), controls=QUARTER)
        self.encloses(start, 426, 20, 25)                     # nearest is (5,0): sqrt(226)
        end = curve.point_arc_distance((-1, 20), controls=QUARTER)
        self.encloses(end, 426, 20, 25)                       # nearest is (0,5): sqrt(226)
        for result in (start, end):
            lower, upper = result["lower"], result["upper"]
            self.assertLessEqual(lower * lower, 226)
            self.assertGreaterEqual(upper * upper, 226)
            self.assertLess(upper * upper, 436)               # the far endpoint was not taken

    def test_minor_major_and_semicircle_spans_in_both_directions(self):
        for controls, point, total, projection, radicand in [
                (QUARTER, (1, 1), 27, 1, 50),                 # ccw minor, radial
                (QUARTER_CW, (1, 1), 27, 1, 50),              # cw minor, same point set
                (QUARTER_CW, (20, -1), 426, 20, 25),          # cw minor, endpoint (5,0)
                (MAJOR_CCW, (-1, -1), 27, 1, 50),             # ccw major, radial in the third quadrant
                (MAJOR_CCW, (3, -1), 35, 3, 25),              # ccw major gap, endpoint (5,0)
                (MAJOR_CW, (-1, -1), 27, 1, 50),              # cw major, radial
                (MAJOR_CW, (2, 1), 30, 2, 25),                # cw major gap, endpoint (5,0)
                (SEMI_CCW, (0, 3), 34, 1, 225),               # ccw semicircle, radial
                (SEMI_CCW, (1, -2), 30, 1, 25),               # below the diameter, endpoint (5,0)
                (SEMI_CW, (0, -3), 34, 1, 225),               # cw semicircle, radial
                (SEMI_CW, (2, 3), 38, 2, 25)]:                # above the diameter, endpoint (5,0)
            with self.subTest(controls=controls, point=point):
                result = curve.point_arc_distance(point, controls=controls)
                self.encloses(result, total, projection, radicand)
        for controls, point in ((SEMI_CCW, (0, 3)), (SEMI_CW, (0, -3))):
            exact = curve.point_arc_distance(point, controls=controls)
            self.assertEqual((exact["lower"], exact["upper"]), (F(2), F(2)))

    def test_irrational_ray_endpoints_of_a_partial_span(self):
        upper_half = {"controls": QUARTER, "start_ray": (1, 1), "end_ray": (0, 1)}
        outside = curve.point_arc_distance((5, 0), **upper_half)
        self.encloses(outside, 50, 5, F(25, 2))               # the 45 degree endpoint, not (0,5)
        self.assertGreater(outside["lower"], F(3826, 1000))   # 10*sin(22.5 degrees) = 3.82683...
        self.assertLess(outside["upper"], F(3827, 1000))
        boundary = curve.point_arc_distance((7, 7), **upper_half)
        self.radial(boundary, 98, 25)                         # the closed start ray belongs to the span
        lower_half = curve.point_arc_distance((0, 10), controls=QUARTER,
                                              start_ray=(1, 0), end_ray=(1, 1))
        self.encloses(lower_half, 125, 10, F(25, 2))          # the irrational end ray endpoint

    def test_nearly_boundary_points_use_exact_membership_signs(self):
        inside = curve.point_arc_distance((BILLION, 1), controls=QUARTER)
        self.radial(inside, BILLION ** 2 + 1, 25)             # sqrt(1e18+1) - 5
        outside = curve.point_arc_distance((BILLION, -1), controls=QUARTER)
        self.encloses(outside, BILLION ** 2 + 26, BILLION, 25)  # sqrt((1e9-5)^2+1)
        # The two branches differ by about 2.5e-18, far above the 2**-80 width,
        # so an epsilon or a rounded membership test cannot reproduce both.
        self.assertLess(inside["upper"], outside["lower"])
        touching = curve.point_arc_distance((5, F(-1, BILLION)), controls=QUARTER)
        self.assertEqual((touching["lower"], touching["upper"]), (F(1, BILLION), F(1, BILLION)))
        skew = curve.point_arc_distance((BILLION + 1, BILLION), controls=QUARTER,
                                        start_ray=(1, 1), end_ray=(0, 1))
        self.encloses(skew, (BILLION + 1) ** 2 + BILLION ** 2 + 25, 2 * BILLION + 1, F(25, 2))

    def test_translated_and_scaled_curves_keep_the_certificate(self):
        base = curve.point_arc_distance((1, 1), controls=QUARTER)
        moved = curve.point_arc_distance((124, -455), controls=[(x + 123, y - 456) for x, y in QUARTER])
        self.radial(moved, 2, 25)
        self.assertEqual((base["lower"], base["upper"]), (moved["lower"], moved["upper"]))
        scaled = curve.point_arc_distance((103, -197), controls=SCALED)
        self.radial(scaled, 18, 225)                          # 15 - sqrt(18) = 3*(5 - sqrt(2))
        self.assertLessEqual(3 * base["lower"], scaled["upper"])
        self.assertLessEqual(scaled["lower"], 3 * base["upper"])

    def test_chord_points_are_not_treated_as_curve_points(self):
        midpoint = (F(5, 2), F(5, 2))
        minor = curve.point_arc_distance(midpoint, controls=QUARTER)
        self.radial(minor, F(25, 2), 25)                      # 5 - 5/sqrt(2), never zero
        self.assertGreater(minor["lower"], F(146, 100))
        self.assertLess(minor["upper"], F(147, 100))
        # The same chord midpoint against the complementary major arc lies in
        # the gap, so its nearest curve point is the endpoint (5,0).
        gap = curve.point_arc_distance(midpoint, controls=MAJOR_CW)
        self.encloses(gap, F(75, 2), F(5, 2), 25)
        self.assertGreater(gap["lower"], F(3535, 1000))       # sqrt(25/2) = 3.53553...
        self.assertLess(gap["upper"], F(3536, 1000))
        for controls in (QUARTER, MAJOR_CW):
            for point in ((5, 0), (0, 5)):
                shared = curve.point_arc_distance(point, controls=controls)
                self.assertEqual((shared["lower"], shared["upper"]), (F(0), F(0)))

    def test_requested_precision_is_certified_or_refused(self):
        midpoint = (F(5, 2), F(5, 2))
        coarse = curve.point_arc_distance(midpoint, controls=QUARTER, precision_bits=32)
        self.radial(coarse, F(25, 2), 25, bits=32)
        fine = curve.point_arc_distance(midpoint, controls=QUARTER, precision_bits=256)
        self.radial(fine, F(25, 2), 25, bits=256)
        self.assertGreaterEqual(coarse["upper"], fine["lower"])   # nested enclosures of one value
        self.assertLessEqual(coarse["lower"], fine["upper"])
        # A budget that cannot certify the request must fail instead of widening.
        self.code("curve_distance_resource_indeterminate", point=midpoint, controls=QUARTER, bit_limit=100)
        self.code("curve_distance_resource_indeterminate", point=midpoint, controls=QUARTER,
                  precision_bits=256, bit_limit=64)

    def test_invalid_controls_rays_points_and_limits(self):
        class Forged:
            centre = center = (0, 0)
            radius_squared, direction = 25, 1
            start_ray, end_ray, sweep_class = (1, 0), (0, 1), "minor"

        self.assertTrue(issubclass(curve.CurveDistanceError, ValueError))
        for controls in (None, 5, "abc", QUARTER[:2], [(0, 0), (1, 1), (2, 2)],
                         [(0, 0), (1, 1), (0, 0)], [(0, 0), (1.0, 1), (2, 0)],
                         [(0, 0), (True, 1), (2, 0)], [(0, 0), (1, 1), (2, 0), (3, 3)],
                         [(0, 0, 0), (1, 1, 1), (2, 0, 2)], [(0, 0), (1, 1), (2, F(1, 2))],
                         Forged(), (Forged(), Forged(), Forged())):
            with self.subTest(controls=controls):
                self.code("curve_distance_invalid", point=(0, 0), controls=controls)
        for start, end in ((None, (0, 1)), ((1, 0), None), ((0, 0), (0, 1)), ((2, 2), (0, 1)),
                           ((0, 1), (1, 0)), ((1, 0), (-1, 0)), ((1.0, 1), (0, 1)),
                           ((True, 1), (0, 1)), (5, (0, 1)), ((1, 0), (0, 1, 2)), ((1, 0), (1, 0))):
            with self.subTest(start=start, end=end):
                self.code("curve_distance_invalid", point=(1, 1), controls=QUARTER,
                          start_ray=start, end_ray=end)
        for point in (None, 5, "ab", (1,), (1, 2, 3), (1, 1.0), (1, True), ("1", "2"),
                      (1, Decimal("1")), [F(1, 2), 0.5], {"x": 1, "y": 2}, (1 + 0j, 1)):
            with self.subTest(point=point):
                self.code("curve_distance_invalid", point=point, controls=QUARTER)
        for name, value in [("precision_bits", 31), ("precision_bits", 257), ("precision_bits", 80.0),
                            ("precision_bits", True), ("precision_bits", None), ("precision_bits", F(80)),
                            ("operation_limit", 0), ("operation_limit", -1), ("operation_limit", True),
                            ("operation_limit", 10 ** 6 + 1), ("bit_limit", 63), ("bit_limit", 262145),
                            ("bit_limit", True), ("bit_limit", F(128))]:
            with self.subTest(limit=name, value=value):
                self.code("curve_distance_invalid", point=(1, 1), controls=QUARTER, **{name: value})
        leaked = self.code("curve_distance_invalid", point=(0, 0), controls=[(0, 0), (1, 1), (2, 2)])
        self.assertNotIsInstance(leaked, curve._arc.ArcError)
        self.assertNotIsInstance(leaked, (TypeError, ZeroDivisionError))

    def test_exhausted_operation_and_bit_budgets_are_explicit(self):
        for limits in ({"operation_limit": 1}, {"operation_limit": 12}, {"bit_limit": 64},
                       {"bit_limit": 128, "precision_bits": 200}):
            with self.subTest(limits=limits):
                self.code("curve_distance_resource_indeterminate", point=(1, 1),
                          controls=QUARTER, **limits)
        self.code("curve_distance_resource_indeterminate", point=(1 << 20000, 0), controls=QUARTER)
        self.code("curve_distance_resource_indeterminate", point=(F(1, 3 ** 12000), 0), controls=QUARTER)
        generous = curve.point_arc_distance((1, 1), controls=QUARTER, precision_bits=256,
                                            operation_limit=1000000, bit_limit=262144)
        self.radial(generous, 2, 25, bits=256)


if __name__ == "__main__":
    unittest.main()
