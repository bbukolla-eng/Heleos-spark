"""Controller-owned closed-form oracle; no expected values from product code.

Run with --self-check while the assigned implementation is absent, or provide
the candidate's absolute scripts/duct_curve_distance.py path. Decimal only seeds
radical bounds: exact rational squaring verifies both ends before use.
"""
import argparse
import copy
from decimal import Decimal, localcontext
from fractions import Fraction as F
import importlib.util
import json
from pathlib import Path
from unittest import mock


def radical(value):
    value = F(value)
    assert value >= 0
    with localcontext() as ctx:
        ctx.prec = 180
        guess = F((Decimal(value.numerator) / Decimal(value.denominator)).sqrt())
    error = F(1, 10**160)
    lo, hi = max(F(0), guess - error), guess + error
    assert lo * lo <= value <= hi * hi, "Oracle approximation is uncertified"
    return lo, hi


def nested_radical(total, multiplier, radicand):
    lo, hi = radical(radicand)
    a, b = total - multiplier * hi, total - multiplier * lo
    assert 0 <= a <= b
    return radical(a)[0], radical(b)[1]


def cases():
    q = [(5, 0), (3, 4), (0, 5)]
    major = [(5, 0), (-5, 0), (0, 5)]
    half = [(-5, 0), (0, 5), (5, 0)]
    rational = [(0, 0), (4, 3), (8, 0)]
    def exact(value):
        return F(value), F(value)
    def item(name, point, controls, truth, **kwargs):
        return dict(name=name, point=point, controls=controls, truth=truth, kwargs=kwargs)
    sqrt_half = radical(F(25, 2))
    result = [
        item("center", (0, 0), q, exact(5)),
        item("on_curve", (3, 4), q, exact(0)),
        item("outside_radius", (6, 8), q, exact(5)),
        item("inside_radius", (F(3, 2), 2), q, exact(F(5, 2))),
        item("nearest_end", (-3, 4), q, radical(10)),
        item("nearest_start", (4, -3), q, radical(10)),
        item("endpoint_tie", (-5, -5), q, radical(125)),
        item("false_chord_match", (F(5, 2), F(5, 2)), q,
             (5 - sqrt_half[1], 5 - sqrt_half[0])),
        item("irrational_end", (0, 5), q, nested_radical(50, 25, 2),
             start_ray=(1, 0), end_ray=(1, 1)),
        item("irrational_start", (5, 0), q, nested_radical(50, 25, 2),
             start_ray=(1, 1), end_ray=(0, 1)),
        item("negative_endpoint_dot", (-3, -4), q, radical(80),
             start_ray=(1, 0), end_ray=(1, 1)),
        item("major_excluded_wedge", (3, 4), major, radical(10)),
        item("major_interior", (-3, 4), major, exact(0)),
        item("major_outward", (-6, 8), major, exact(5)),
        item("half_excluded", (0, -4), half, radical(41)),
        item("half_interior", (0, 4), half, exact(1)),
        item("rational_center", (4, F(-7, 6)), rational, exact(F(25, 6))),
        item("rational_radius_outward", (4, 4), rational, exact(1)),
        item("rational_circle_endpoint", (4, -8), rational, radical(80)),
    ]
    # Reversal changes direction, never the geometric set or its distance.
    for case in list(result):
        reverse = dict(case, name=case["name"] + "_reversed",
                       controls=list(reversed(case["controls"])), kwargs=dict(case["kwargs"]))
        if "start_ray" in reverse["kwargs"]:
            reverse["kwargs"]["start_ray"], reverse["kwargs"]["end_ray"] = (
                reverse["kwargs"]["end_ray"], reverse["kwargs"]["start_ray"])
        result.append(reverse)
    # Translation and positive integer scaling preserve the nearest-point identity.
    for case in list(result):
        scale, dx, dy = 137, 1234567, -9876543
        result.append(dict(case, name=case["name"] + "_transformed",
                           point=tuple(scale * v + d for v, d in zip(case["point"], (dx, dy))),
                           controls=[(scale*x+dx, scale*y+dy) for x,y in case["controls"]],
                           truth=tuple(scale*v for v in case["truth"])))
    tiny = F(1, 2**120)
    result.append(item("just_outside_endpoint_ray", (5, -tiny), q, exact(tiny), precision_bits=256))
    a, b = radical(25 + tiny*tiny)
    result.append(item("just_inside_endpoint_ray", (5, tiny), q, (a-5, b-5), precision_bits=256))
    return result


def verify(module_path=None):
    reference = cases()
    for case in reference:
        lo, hi = case["truth"]
        assert 0 <= lo <= hi
        assert hi-lo < F(1, 10**140)
    if module_path is None:
        return {"reference_cases": len(reference), "reference_intervals_verified": True,
                "candidate_tested": False}
    spec = importlib.util.spec_from_file_location("controller_curve_distance_candidate", module_path)
    candidate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(candidate)
    for case in reference:
        kwargs = case["kwargs"]
        before = copy.deepcopy(case)
        with mock.patch("builtins.open", side_effect=AssertionError("Calculation attempted file I/O")):
            value = candidate.point_arc_distance(case["point"], controls=case["controls"], **kwargs)
        assert case == before, "Caller input was mutated"
        assert candidate.point_arc_distance(case["point"], controls=case["controls"], **kwargs) == value
        assert set(value) == {"lower", "upper", "method"}, case["name"]
        assert value["method"] == "point_arc_distance_rational_v1", case["name"]
        lo, hi = value["lower"], value["upper"]
        assert type(lo) is F and type(hi) is F, case["name"]
        assert 0 <= lo <= hi and hi-lo <= F(1, 2**kwargs.get("precision_bits", 80)), case["name"]
        a, b = case["truth"]
        # Oracle width is <1e-140; disjoint intervals decisively disprove a bound.
        assert lo <= b and a <= hi, (case["name"], "disjoint closed-form intervals")
        if case["name"] == "just_inside_endpoint_ray":
            assert lo > 0, "Tiny positive radial distance collapsed to zero"
    rejected = 0
    quarter = [(5, 0), (3, 4), (0, 5)]
    def reject(point=(2, 3), controls=quarter, code="curve_distance_invalid", **kwargs):
        nonlocal rejected
        try:
            candidate.point_arc_distance(point, controls=controls, **kwargs)
        except candidate.CurveDistanceError as error:
            assert error.code == code, (error.code, code)
            rejected += 1
        else:
            raise AssertionError("Malformed or exhausted request produced success")
    for point in [None, [], [1], [1, 2, 3], [True, 0], [1.0, 0], [Decimal(1), 0], ["1", 0]]:
        reject(point=point)
    for controls in [None, quarter[:2], [(0, 0)]*3, [(0, 0), (1, 1), (2, 2)],
                     [(5.0, 0), (3, 4), (0, 5)]]:
        reject(controls=controls)
    for kwargs in [dict(start_ray=(1, 0)), dict(end_ray=(0, 1)),
                   dict(start_ray=(2, 0), end_ray=(0, 1)),
                   dict(start_ray=(0, 0), end_ray=(0, 1)),
                   dict(start_ray=(0, 1), end_ray=(1, 0)),
                   dict(start_ray=(-1, 0), end_ray=(0, 1)),
                   dict(start_ray=(1, 0), end_ray=(1, 0))]:
        reject(**kwargs)
    for field, values in {"precision_bits": [True, 31, 257, 80.0, "80"],
                          "operation_limit": [True, 0, 1000001, 1.0, "1"],
                          "bit_limit": [True, 63, 262145, 64.0, "64"]}.items():
        for value in values:
            reject(**{field: value})
    reject(operation_limit=1, code="curve_distance_resource_indeterminate")
    reject(point=(1 << 20000, 1), code="curve_distance_resource_indeterminate")
    reject(bit_limit=64, code="curve_distance_resource_indeterminate")
    return {"reference_cases": len(reference), "reference_intervals_verified": True,
            "rejected_invalid_or_exhausted_requests": rejected,
            "candidate_tested": True, "candidate_path": str(Path(module_path).resolve()),
            "limit": "Closed-form interval agreement; not whole-path or topology acceptance"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("module", nargs="?")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if not args.self_check and not args.module:
        parser.error("Provide a candidate module or --self-check")
    print(json.dumps(verify(None if args.self_check else args.module), indent=2))
