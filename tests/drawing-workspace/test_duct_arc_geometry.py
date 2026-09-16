"""Literal and exact symbolic checks; no model, calculator, or native imports."""
from fractions import Fraction as F
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

PATH = Path(__file__).resolve().parents[2] / "scripts/duct_arc_geometry.py"
spec = importlib.util.spec_from_file_location("duct_arc_geometry_test", PATH)
arc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(arc)
QUARTER = [(5, 0), (3, 4), (0, 5)]
UPPER = [(-5, 0), (3, 4), (5, 0)]
PI_LO = F("3.14159265358979323846264338327950288419716939937510")
PI_HI = F("3.14159265358979323846264338327950288419716939937511")


class ArcGeometryTests(unittest.TestCase):
    def setUp(self):
        self.quarter = arc.make_arc(QUARTER)
        self.upper = arc.make_arc(UPPER)

    def code(self, expected, function, *args):
        with self.assertRaises(arc.ArcError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, expected)

    def test_literal_circle_direction_minor_major_semicircle_and_reversal(self):
        for points, kind, direction, meters in [
                (QUARTER, "minor", 1, "7.853982"),
                ([(5, 0), (0, 5), (0, -5)], "major", 1, "23.561945"),
                ([(5, 0), (3, 4), (-5, 0)], "semicircle", 1, "15.707963"),
                (list(reversed(QUARTER)), "minor", -1, "7.853982")]:
            with self.subTest(points=points):
                value = arc.make_arc(points)
                self.assertEqual(value.center, (0, 0)); self.assertEqual(value.radius_squared, 25)
                self.assertEqual((value.sweep_class, value.direction), (kind, direction))
                self.assertEqual(arc.measure_arc(value, "1000000")["meters"], meters)
                self.assertEqual(arc.measure_radius(value, "1000000")["meters"], "5.000000")

    def test_through_selects_complement_and_full_continuous_support(self):
        major = arc.make_arc([(5, 0), (-5, 0), (0, 5)])
        self.assertEqual(major.sweep_class, "major"); self.assertEqual(major.direction, -1)
        self.assertEqual(arc.measure_arc(major, "1000000")["meters"], "23.561945")
        self.assertTrue(arc.arc_contained_in_rect(self.quarter, [0, 0, 5, 5]))
        self.assertFalse(arc.arc_contained_in_rect(major, [0, 0, 5, 5]))
        self.assertTrue(arc.arc_intersects_rect(major, [-5, -1, -4, 1]))
        self.assertFalse(arc.arc_intersects_rect(self.quarter, [-5, -1, -4, 1]))

    def test_exact_degeneracy_and_input_bound_are_refused_without_chord(self):
        for points in ([(0, 0), (0, 0), (1, 1)], [(0, 0), (1, 1), (2, 2)],
                       [(0, 0), (1, 1), (0, 0)], [(0, 0), (True, 1), (2, 0)],
                       [(0, 0), (1.0, 1), (2, 0)], [(0, 0), (arc.MAX_COORDINATE + 1, 1), (2, 0)]):
            self.code("arc_invalid", arc.make_arc, points)
        self.code("arc_invalid", arc.make_arc, QUARTER[:2])

    def test_translation_rotation_and_reflection_in_canonical_units(self):
        for transform in (lambda x, y: (x, y), lambda x, y: (-y, x), lambda x, y: (-x, -y),
                          lambda x, y: (y, -x), lambda x, y: (-x, y)):
            points = [(transform(x, y)[0] + 123, transform(x, y)[1] - 456) for x, y in QUARTER]
            value = arc.make_arc(points)
            self.assertEqual(value.center, (123, -456)); self.assertEqual(value.radius_squared, 25)
            self.assertEqual(arc.measure_arc(value, "1000000")["meters"], "7.853982")

    def test_near_collinear_and_near_full_symbolic_constructions(self):
        k = 1000000
        small = arc.make_arc([(0, 0), (k, 1), (2 * k, 0)])
        self.assertEqual(small.center, (k, F(1 - k * k, 2)))
        self.assertEqual(small.radius_squared, F((k * k + 1) ** 2, 4))
        self.assertEqual(arc.measure_arc(small, "1000000")["meters"], "2000000.000001")
        r = k * k + 1
        large = arc.make_arc([(k * k - 1, 2 * k), (-r, 0), (k * k - 1, -2 * k)])
        self.assertEqual(large.center, (0, 0)); self.assertEqual(large.radius_squared, r * r)
        self.assertEqual(large.sweep_class, "major")
        measured = arc.measure_arc(large, "0.000001")
        # 2*pi*R - 4*R*atan(1/k), scaled by 1e-12, is between
        # 6.283181307185... and 6.283181307186... from alternating atan.
        self.assertEqual(measured["meters"], "6.283181")

    def test_circle_radius_rational_denominator_and_exact_half_even_ties(self):
        value = arc.make_arc([(0, 0), (4, 3), (8, 0)])
        self.assertEqual(value.center, (4, F(-7, 6)))
        self.assertEqual(value.radius_squared, F(625, 36))
        self.assertEqual(arc.measure_radius(value, "0.36")["meters"], "0.000002")
        self.assertEqual(arc.measure_radius(self.quarter, "0.3")["meters"], "0.000002")
        self.assertEqual(arc.measure_radius(self.quarter, "0.5")["meters"], "0.000002")
        self.code("arc_below_resolution", arc.measure_radius, self.quarter, "0.1")
        # An unused radius that rounds to zero must not reject this arc length.
        self.assertEqual(arc.measure_arc(self.quarter, "0.1")["meters"], "0.000001")

    def test_raw_range_resolution_and_scale_bounds(self):
        value = arc.make_arc([(1000000, 0), (0, 1000000), (-1000000, 0)])
        self.assertEqual(arc.measure_radius(value, "1e12")["meters"], "1000000000000.000000")
        self.code("arc_out_of_range", arc.measure_arc, value, "1e12")
        self.code("arc_below_resolution", arc.measure_arc, self.quarter, "1e-40")
        self.assertEqual(arc.measure_arc(self.quarter, "0" * 79 + "1")["meters"], "0.000008")
        for scale in ("NaN", "Infinity", "0", "-1", "1e13", "1e-41", "1e999999999", "1" * 81):
            self.code("arc_invalid", arc.measure_arc, self.quarter, scale)

    def test_certificate_outward_bounds_independently_enclose_quarter(self):
        result = arc.measure_arc(self.quarter, "1000000")
        certificate = result["certificate"]
        lo, hi = F(certificate["lower_meters"]), F(certificate["upper_meters"])
        self.assertLessEqual(lo, 5 * PI_LO / 2); self.assertGreaterEqual(hi, 5 * PI_HI / 2)
        self.assertEqual(certificate["quantity"], "arc_length")
        self.assertLess(len(json.dumps(result)), 3000)
        descriptor = arc.describe_arc(self.quarter)
        self.assertEqual(descriptor["center_micropoints"][0], {"numerator": "0", "denominator": "1"})
        self.assertEqual(descriptor["radius_squared_micropoints"], {"numerator": "25", "denominator": "1"})
        self.assertLess(len(json.dumps(descriptor)), 2500)

    def test_machin_identity_and_signed_pi_enclosures(self):
        tan2 = 2 * F(1, 5) / (1 - F(1, 5) ** 2)
        tan4 = 2 * tan2 / (1 - tan2 ** 2)
        self.assertEqual((tan4 - F(1, 239)) / (1 + tan4 * F(1, 239)), 1)
        # 0 < 4*atan(1/5)-atan(1/239) < .8 < pi/2 fixes the branch.
        self.assertGreater(4 * (F(1, 5) - F(1, 5) ** 3 / 3) - F(1, 239), 0)
        self.assertLess(F(4, 5), F(3, 2))
        previous = None
        for terms in arc.PRECISION_LEVELS:
            lo, hi = arc._pi_bounds(terms)
            self.assertLess(lo, PI_HI); self.assertGreater(hi, PI_LO)
            if previous: self.assertGreaterEqual(lo, previous[0]); self.assertLessEqual(hi, previous[1])
            previous = (lo, hi)
        self.assertGreater(previous[0], PI_LO); self.assertLess(previous[1], PI_HI)

    def test_atan_reciprocal_odd_symmetry_range_reduction_and_sqrt_proof(self):
        work = arc._Budget(); pi = arc._pi_bounds(32)
        for value in (F(0), F(1, 2), F(3, 4), F(1), F(2), F(239)):
            lo, hi = arc._atan_bounds(value, 32, pi, work)
            neglo, neghi = arc._atan_bounds(-value, 32, pi, work)
            self.assertEqual((neglo, neghi), (-hi, -lo))
            self.assertLessEqual(lo, hi)
            if value:
                other = arc._atan_bounds(1 / value, 32, pi, work)
                self.assertLessEqual(lo + other[0], pi[1] / 2)
                self.assertGreaterEqual(hi + other[1], pi[0] / 2)
        for value in (F(2), F(7, 13), F(625, 36), F(0)):
            lo, hi = arc._sqrt_bounds(value, 32, work)
            self.assertLessEqual(lo * lo, value); self.assertGreaterEqual(hi * hi, value)
            if value == F(625, 36): self.assertEqual((lo, hi), (F(25, 6), F(25, 6)))

    def test_half_even_boundary_straddles_and_bounded_precision_exhaustion(self):
        work = arc._Budget()
        for value, expected in [(F(1, 2000000), 0), (F(3, 2000000), 2), (F(5, 2000000), 2), (F(7, 2000000), 4)]:
            self.assertEqual(arc._half_even(value, work), expected)
        epsilon, tie = F(1, 10 ** 30), F(3, 2000000)
        self.assertIsNone(arc._admit((tie - epsilon, tie + epsilon), work))
        self.assertIsNone(arc._admit((F(10 ** 12) - epsilon, F(10 ** 12) + epsilon), work))
        # This irrational radius needs more than the first actual precision
        # level. Ending that ladder early must not select its midpoint.
        irrational = arc.make_arc([(0, 0), (1, 0), (0, 1)])
        measured = arc.measure_arc(irrational, "1000000")
        self.assertEqual(measured["meters"], "3.332162")
        self.assertGreater(measured["certificate"]["terms"], 8)
        with mock.patch.object(arc, "PRECISION_LEVELS", (8,)):
            self.code("arc_numeric_indeterminate", arc.measure_arc, irrational, "1000000")

    def test_closed_tangency_open_exclusion_and_degenerate_polygons(self):
        for rectangle in ([3, 4, 4, 5], [3, 4, 3, 4], [0, 5, 0, 6], [-1, 5, 1, 5]):
            self.assertTrue(arc.arc_intersects_rect(self.quarter, rectangle))
            self.assertFalse(arc.arc_intersects_rect(self.quarter, rectangle, closed=False))
        self.assertTrue(arc.arc_intersects_rect(self.upper, [-1, 5, 1, 6]))
        self.assertFalse(arc.arc_intersects_rect(self.upper, [-1, 5, 1, 6], closed=False))
        self.assertFalse(arc.arc_intersects_rect(self.upper, [-1, -1, 1, 1]))
        self.assertTrue(arc.arc_intersects_rect(self.quarter, [4, -1, 6, 1], closed=False))

    def test_anchor_and_chord_trap_is_rejected_for_view_and_graphics(self):
        self.assertTrue(all(-5 <= x <= 5 and 0 <= y <= 4 for x, y in UPPER))
        self.assertFalse(arc.arc_contained_in_rect(self.upper, [-5, 0, 5, 4]))
        self.assertFalse(arc.arc_covered_by_rects(self.upper, [[-5, 0, 5, 4]]))
        self.assertTrue(arc.arc_intersects_rect(self.upper, [-1, 4, 1, 6]))
        self.assertTrue(arc.arc_contained_in_rect(self.upper, [-5, 0, 5, 5]))
        self.assertFalse(arc.arc_contained_in_rect(self.upper, [10, 10, 20, 20]))

    def test_closed_graphic_seams_and_arbitrarily_small_positive_gaps(self):
        self.assertTrue(arc.arc_covered_by_rects(self.upper, [[-5, 0, 0, 5], [0, 0, 5, 5]]))
        for gap in (F(1), F(1, 1000000), F(1, 10 ** 40)):
            self.assertFalse(arc.arc_covered_by_rects(self.upper, [[-5, 0, -gap, 5], [gap, 0, 5, 5]]))
        self.assertFalse(arc.arc_covered_by_rects(self.upper, []))
        self.assertFalse(arc.arc_covered_by_rects(self.upper, [[-5, 0, 5, 0]]))

    def test_union_limit_maximal_canonical_inputs_and_preallocation_bit_guards(self):
        strips = [[F(-5) + F(10 * i, 128), 0, F(-5) + F(10 * (i + 1), 128), 5] for i in range(128)]
        self.assertTrue(arc.arc_covered_by_rects(self.upper, strips))
        self.code("arc_resource_indeterminate", arc.arc_covered_by_rects, self.upper, strips + [strips[0]])
        near_max = arc.make_arc([(arc.MAX_COORDINATE, 0), (0, arc.MAX_COORDINATE), (-arc.MAX_COORDINATE, 0)])
        self.assertEqual(near_max.radius_squared, arc.MAX_COORDINATE ** 2)
        self.assertTrue(arc.arc_contained_in_rect(near_max, [-arc.MAX_COORDINATE, 0, arc.MAX_COORDINATE, arc.MAX_COORDINATE]))
        budget = arc._Budget(bit_limit=32)
        self.code("arc_resource_indeterminate", budget.mul, F((1 << 20) - 1), F((1 << 20) - 1))
        self.code("arc_resource_indeterminate", budget.add, F(1, (1 << 20) - 1), F(1, (1 << 20) - 3))
        self.code("arc_resource_indeterminate", budget.cmp, F((1 << 20) - 1, (1 << 19) - 1), F((1 << 20) - 3, (1 << 19) - 3))
        self.code("arc_resource_indeterminate", arc._sqrt_bounds, F(2), 32, budget)
        huge = 1 << arc.MAX_BITS
        self.code("arc_resource_indeterminate", arc.arc_intersects_rect, self.upper, [0, 0, huge, huge])
        original = arc._Budget
        with mock.patch.object(arc, "_Budget", lambda: original(operation_limit=30)):
            self.code("arc_resource_indeterminate", arc.arc_covered_by_rects, self.upper, strips)

    def test_default_million_operation_limit_refuses_dense_128_box_decomposition(self):
        major = arc.make_arc([(5, 0), (0, 5), (0, -5)])
        # The full first box covers the arc. All 127 additional closed boxes
        # create real complement slabs/gaps above the circle; none may be
        # silently discarded to claim support after the fixed work cap.
        boxes = [[-5, -5, 5, 5]] + [[-6 + F(i, 128), 5 + F(i + 1, 128),
                  6 - F(i, 128), 5 + F(i + 1, 128) + F(1, 1000)] for i in range(127)]
        self.code("arc_resource_indeterminate", arc.arc_covered_by_rects, major, boxes)


if __name__ == "__main__":
    unittest.main()
