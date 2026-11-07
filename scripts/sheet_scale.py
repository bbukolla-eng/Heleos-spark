"""Immutable view-scale facts and source-bound Decimal measurements, Python 3.9+."""
import copy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import uuid

_spec = importlib.util.spec_from_file_location(
    "heleos_sheet_geometry", Path(__file__).with_name("sheet_geometry.py"))
geometry_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(geometry_module)
_arc_spec = importlib.util.spec_from_file_location(
    "heleos_duct_arc_geometry", Path(__file__).with_name("duct_arc_geometry.py"))
arc_geometry = importlib.util.module_from_spec(_arc_spec)
_arc_spec.loader.exec_module(arc_geometry)

UNITS = {"m": Decimal("1"), "mm": Decimal("0.001"),
         "ft": Decimal("0.3048"), "in": Decimal("0.0254")}
INACTIVE = {"rejected", "withdrawn", "superseded"}
STATES = INACTIVE | {"verified"}


class ScaleError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _fail(code, message):
    raise ScaleError(code, message)


def _text(value, maximum=500):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum or
            any(ord(c) < 32 for c in value)):
        _fail("invalid_text", "Provide bounded plain text for the record.")
    return value.strip()


def _number(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)) or len(str(value)) > 80:
        _fail("invalid_number", "Use a finite number within the supported range.")
    try:
        result = Decimal(str(value))
        if (not result.is_finite() or result < 0 or result > Decimal("1e12") or
                result.as_tuple().exponent < -40 or (positive and result == 0)):
            raise InvalidOperation()
    except (InvalidOperation, ValueError, OverflowError):
        _fail("invalid_number", "Use a finite number within the supported range.")
    return result


def _hash(value):
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, OverflowError):
        _fail("invalid_record", "The saved record is not valid finite JSON.")
    if len(raw) > 128 * 1024:
        _fail("invalid_record", "The saved scale record exceeds its size limit.")
    return hashlib.sha256(raw).hexdigest()


def _fact_hash(fact):
    return _hash({k: v for k, v in fact.items() if k not in {"fingerprint", "state", "issues"}})


def _geometry(value):
    if not isinstance(value, dict):
        _fail("geometry_missing", "Current source coordinates are required for measurement.")
    try:
        actual = geometry_module.sheet_geometry(value, value.get("revision_id"))
    except geometry_module.GeometryError as exc:
        raise ScaleError(exc.code, exc.message) from None
    if value.get("fingerprint") != actual["fingerprint"]:
        _fail("geometry_changed", "The source coordinate identity has changed.")
    return actual


def _source(value, geometry):
    if not isinstance(value, dict) or any(value.get(k) != geometry[k]
                                          for k in ("revision_id", "index", "sheet_id")):
        _fail("source_mismatch", "The scale must refer to these exact source coordinates.")
    return {k: geometry[k] for k in ("revision_id", "index", "sheet_id")}


def _points(value, maximum=128):
    if not isinstance(value, list) or not 2 <= len(value) <= maximum:
        _fail("invalid_points", "Select between two and 128 drawing points.")
    result = []
    for pair in value:
        if not isinstance(pair, list) or len(pair) != 2:
            _fail("invalid_points", "Drawing points need an x and y coordinate.")
        parsed = [_number(v) for v in pair]
        if any(v > 1 for v in parsed):
            _fail("invalid_points", "Drawing points must be inside the page.")
        result.append([float(v) for v in parsed])
    return result


def _view(value):
    if not isinstance(value, list) or len(value) != 4:
        _fail("invalid_view", "Select the bounds of the drawing view.")
    points = _points([value[:2], value[2:]], 2)
    result = points[0] + points[1]
    if result[0] >= result[2] or result[1] >= result[3]:
        _fail("invalid_view", "The drawing view needs a positive width and height.")
    return result


def _contained(path, view):
    if not all(view[0] <= p[0] <= view[2] and view[1] <= p[1] <= view[3] for p in path):
        _fail("outside_view", "Keep the complete path inside the selected scale view.")


def _paper(path, geometry):
    try:
        return geometry_module.paper_length(path, geometry)
    except geometry_module.GeometryError as exc:
        raise ScaleError(exc.code, exc.message) from None


def _record(source, geometry, view, label, actor, reason):
    return {"id": uuid.uuid4().hex, "source": _source(source, geometry), "view": _view(view),
            "label": _text(label, 120), "actor": _text(actor, 120), "reason": _text(reason, 1000),
            "at": datetime.now(timezone.utc).isoformat(), "geometry": copy.deepcopy(geometry),
            "geometry_fingerprint": geometry["fingerprint"], "state": "unverified"}


def make_calibration(source, geometry, values, actor, reason):
    geometry = _geometry(geometry)
    if not isinstance(values, dict):
        _fail("invalid_calibration", "Provide a reference length and drawing view.")
    if values.get("uniform_scale_confirmed") is not True:
        _fail("scale_confirmation", "Confirm uniform scale within this drawing view.")
    if not isinstance(values.get("unit"), str) or values["unit"] not in UNITS:
        _fail("invalid_unit", "Use m, mm, ft or in for the known length.")
    fact = _record(source, geometry, values.get("view"), values.get("label"), actor, reason)
    path = _points(values.get("points"), 2)
    _contained(path, fact["view"])
    known = _number(values.get("known_length"), positive=True)
    paper = _paper(path, geometry)
    with localcontext() as ctx:
        ctx.prec, ctx.rounding = 34, ROUND_HALF_EVEN
        ratio = known * UNITS[values["unit"]] / paper
        _number(ratio, positive=True)
    fact.update(origin="calibrated", points=path, known_length=str(known), unit=values["unit"],
                meters_per_point=str(ratio), paper_points=str(paper), uniform_scale_confirmed=True)
    fact["fingerprint"] = _fact_hash(fact)
    return fact


def make_declared(source, geometry, candidate, view, label, uniform_scale_confirmed, actor, reason):
    geometry = _geometry(geometry)
    if not isinstance(candidate, dict):
        _fail("invalid_candidate", "Select a retained scale-label reading.")
    _source(candidate.get("source"), geometry)
    if (candidate.get("origin") != "declared" or candidate.get("notation") not in
            {"ratio", "imperial", "nts", "unknown"} or not isinstance(candidate.get("evidence"), dict)):
        _fail("invalid_candidate", "The scale-label reading is incomplete.")
    _text(candidate.get("id"), 128)
    _text(candidate["evidence"].get("text"), 4000)
    _view(candidate["evidence"].get("bbox"))
    ratio = candidate.get("meters_per_point")
    if candidate["notation"] in {"nts", "unknown"}:
        if ratio is not None:
            _fail("invalid_candidate", "An unknown or NTS label cannot supply a numeric scale.")
    elif ratio is None:
        _fail("invalid_candidate", "A numeric scale label must supply its parsed scale.")
    if ratio is not None:
        ratio = _number(ratio, positive=True)
        if uniform_scale_confirmed is not True:
            _fail("scale_confirmation", "Confirm uniform scale within this drawing view.")
    fact = _record(source, geometry, view, label, actor, reason)
    fact.update(origin="declared", points=[], known_length=None, unit="m", paper_points=None,
                meters_per_point=str(ratio) if ratio is not None else None,
                uniform_scale_confirmed=uniform_scale_confirmed is True, candidate=copy.deepcopy(candidate))
    fact["fingerprint"] = _fact_hash(fact)
    return fact


def _integrity(fact):
    if not isinstance(fact, dict) or not isinstance(fact.get("fingerprint"), str):
        _fail("scale_unbound", "This legacy scale needs new source-bound evidence.")
    if fact["fingerprint"] != _fact_hash(fact):
        _fail("scale_changed", "The saved scale fact no longer matches its fingerprint.")
    geometry = _geometry(fact.get("geometry"))
    if fact.get("geometry_fingerprint") != geometry["fingerprint"]:
        _fail("geometry_changed", "The saved scale coordinate identity has changed.")
    _source(fact.get("source"), geometry)
    _view(fact.get("view"))
    if fact.get("origin") not in {"calibrated", "declared"}:
        _fail("invalid_record", "The scale fact origin is unsupported.")
    return geometry


def _decision(fact, decisions):
    if not isinstance(decisions, list):
        _fail("invalid_decision", "Scale decisions must remain an ordered event history.")
    events = [v for v in decisions if isinstance(v, dict) and v.get("fact_id") == fact.get("id")]
    if not events:
        return None
    latest = events[-1]
    if (not isinstance(latest.get("state"), str) or latest["state"] not in STATES or
            not isinstance(latest.get("id"), str) or not latest["id"] or
            latest.get("fact_fingerprint") != _fact_hash(fact) or
            (latest["state"] == "verified" and
             latest.get("geometry_fingerprint") != fact.get("geometry_fingerprint"))):
        _fail("decision_changed", "The latest scale decision does not match the saved fact.")
    return latest


def _numeric(fact):
    if fact.get("uniform_scale_confirmed") is not True or fact.get("meters_per_point") is None:
        _fail("scale_unusable", "An unknown, NTS or unconfirmed scale cannot support measurement.")
    return _number(fact["meters_per_point"], positive=True)


def _current(fact, geometry):
    previous = _integrity(fact)
    _source(fact["source"], geometry)
    if previous["fingerprint"] != geometry["fingerprint"]:
        _fail("geometry_changed", "The scale was created against different source coordinates.")


def _select(facts, fact_id):
    if not isinstance(facts, list):
        _fail("invalid_record", "Scale facts must be an ordered record collection.")
    matches = [v for v in facts if isinstance(v, dict) and v.get("id") == fact_id]
    if len(matches) != 1:
        _fail("scale_missing", "Select one existing scale fact.")
    return matches[0]


def decide(facts, decisions, fact_id, state, geometry, actor, reason):
    if not isinstance(state, str) or state not in STATES:
        _fail("invalid_decision", "Choose verified, rejected, withdrawn or superseded.")
    fact = _select(facts, fact_id)
    if not isinstance(decisions, list):
        _fail("invalid_decision", "Scale decisions must remain an ordered event history.")
    # Explicit retirement must also work for stale or legacy evidence. Otherwise
    # an old overlapping view could permanently block a replacement scale.
    geometry = _geometry(geometry) if geometry is not None or state == "verified" else None
    if state == "verified":
        _current(fact, geometry)
        _decision(fact, decisions)
        _numeric(fact)
    return {"id": uuid.uuid4().hex, "fact_id": fact_id, "state": state,
            "actor": _text(actor, 120), "reason": _text(reason, 1000),
            "at": datetime.now(timezone.utc).isoformat(),
            "fact_fingerprint": _fact_hash(fact),
            "geometry_fingerprint": geometry["fingerprint"] if geometry else None}


def fact_view(fact, decisions, geometry_or_none):
    result = copy.deepcopy(fact)
    result.update(state="unverified", issues=[])
    try:
        decision = _decision(fact, decisions)
        if decision and decision["state"] in INACTIVE:
            result["state"] = decision["state"]
            return result
    except ScaleError as exc:
        issue = ("scale_changed" if fact.get("fingerprint") and
                 fact["fingerprint"] != _fact_hash(fact) else exc.code)
        result.update(state="stale", issues=[issue])
        return result
    if not fact.get("fingerprint") or not fact.get("geometry_fingerprint"):
        result["issues"] = ["scale_unbound"]
        return result
    try:
        _current(fact, _geometry(geometry_or_none))
        decision = _decision(fact, decisions)
        result["state"] = decision["state"] if decision else "unverified"
        if result["state"] == "verified":
            _numeric(fact)
    except ScaleError as exc:
        result.update(state="stale", issues=[exc.code])
    return result


def _intersects(path, view):
    # Inclusive Liang-Barsky clipping catches paths crossing a detail even when
    # neither endpoint lies inside it, including a path touching its boundary.
    box = [Decimal(str(v)) for v in view]
    for first, last in zip(path, path[1:]):
        with localcontext() as ctx:
            ctx.prec, ctx.rounding = 34, ROUND_HALF_EVEN
            x, y = (Decimal(str(v)) for v in first)
            dx, dy = Decimal(str(last[0])) - x, Decimal(str(last[1])) - y
            low, high = Decimal(0), Decimal(1)
            for p, q in ((-dx, x-box[0]), (dx, box[2]-x), (-dy, y-box[1]), (dy, box[3]-y)):
                if p == 0:
                    if q < 0:
                        break
                elif p < 0:
                    low = max(low, q/p)
                else:
                    high = min(high, q/p)
                if low > high:
                    break
            else:
                return True
    return False


def resolve(facts, decisions, fact_id, geometry, path):
    geometry = _geometry(geometry)
    fact = _select(facts, fact_id)
    _current(fact, geometry)
    path = _points(path)
    _contained(path, _view(fact["view"]))
    decision = _decision(fact, decisions)
    if not decision or decision["state"] != "verified":
        _fail("scale_unverified", "Explicitly verify the selected scale before measuring.")
    ratio = _numeric(fact)
    for other in facts:
        if other is fact or not isinstance(other, dict) or other.get("source") != fact["source"]:
            continue
        try:
            other_decision = _decision(other, decisions)
            if other_decision and other_decision["state"] in INACTIVE:
                continue
        except ScaleError:
            pass  # Damaged or legacy evidence is not silently treated as inactive.
        if _intersects(path, _view(other.get("view"))):
            _fail("scale_conflict", "The path intersects another active scale view; resolve that scale first.")
    return {"meters_per_point": str(ratio), "binding": {
        "fact_id": fact["id"], "decision_id": decision["id"],
        "geometry_fingerprint": geometry["fingerprint"], "fact_fingerprint": fact["fingerprint"]}}


def measure_path(path, geometry, resolution):
    geometry = _geometry(geometry)
    path = _points(path)
    if not isinstance(resolution, dict) or not isinstance(resolution.get("binding"), dict):
        _fail("scale_unbound", "Resolve a verified scale before measuring.")
    binding = resolution["binding"]
    if (set(binding) != {"fact_id", "decision_id", "geometry_fingerprint", "fact_fingerprint"} or
            any(not isinstance(v, str) or not v for v in binding.values()) or
            binding["geometry_fingerprint"] != geometry["fingerprint"]):
        _fail("scale_unbound", "The resolved scale must bind these exact coordinates.")
    paper = _paper(path, geometry)
    with localcontext() as ctx:
        ctx.prec, ctx.rounding = 34, ROUND_HALF_EVEN
        meters = paper * _number(resolution.get("meters_per_point"), positive=True)
        if meters > Decimal("1e12"):
            _fail("quantity_range", "The calculated length exceeds the supported range.")
        rounded = meters.quantize(Decimal("0.000001"))
        if meters > 0 and rounded == 0:
            _fail("quantity_resolution", "This positive length is below the supported measurement resolution.")
    return {"paper_points": str(paper), "meters": format(rounded, "f"),
            "pdf_points": [geometry_module.display_to_pdf(p, geometry) for p in path],
            "scale_binding": copy.deepcopy(binding)}


def _pdf_rect(view, geometry):
    """Map the displayed rectangle through the retained orthogonal transform."""
    x0, y0, x1, y1 = _view(view)
    corners = [geometry_module.display_to_pdf(point, geometry)
               for point in ([x0, y0], [x1, y0], [x1, y1], [x0, y1])]
    return [min(point[0] for point in corners), min(point[1] for point in corners),
            max(point[0] for point in corners), max(point[1] for point in corners)]


def validate_arc(points, geometry):
    """Validate the three canonical controls and the entire effective-crop arc."""
    geometry = _geometry(geometry)
    if not isinstance(points, list) or len(points) != 3:
        _fail("arc_invalid", "A circular arc needs exactly start, through and end.")
    try:
        # Keep original decimal controls until the single micropoint transform;
        # _points' legacy float projection is not part of this new geometry.
        arc = arc_geometry.make_arc([geometry_module.display_to_pdf(point, geometry)
                                     for point in points])
        if not arc_geometry.arc_contained_in_rect(arc, _pdf_rect([0, 0, 1, 1], geometry)):
            _fail("outside_crop", "Keep the entire circular arc inside the effective source crop.")
        return arc
    except (arc_geometry.ArcError, geometry_module.GeometryError) as exc:
        raise ScaleError(exc.code, exc.message) from None


def resolve_arc(facts, decisions, fact_id, geometry, points):
    """Resolve existing scale authority against the continuous circular arc."""
    geometry = _geometry(geometry)
    arc = validate_arc(points, geometry)
    fact = _select(facts, fact_id)
    _current(fact, geometry)
    try:
        if not arc_geometry.arc_contained_in_rect(arc, _pdf_rect(fact["view"], geometry)):
            _fail("outside_view", "Keep the complete circular arc inside the selected scale view.")
        decision = _decision(fact, decisions)
        if not decision or decision["state"] != "verified":
            _fail("scale_unverified", "Explicitly verify the selected scale before measuring.")
        ratio = _numeric(fact)
        for other in facts:
            if other is fact or not isinstance(other, dict) or other.get("source") != fact["source"]:
                continue
            try:
                other_decision = _decision(other, decisions)
                if other_decision and other_decision["state"] in INACTIVE:
                    continue
            except ScaleError:
                pass  # Match legacy resolution: damaged evidence is not inactive.
            if arc_geometry.arc_intersects_rect(arc, _pdf_rect(other.get("view"), geometry), closed=True):
                _fail("scale_conflict", "The circular arc intersects another active scale view; resolve that scale first.")
        return {"meters_per_point": str(ratio), "binding": {
            "fact_id": fact["id"], "decision_id": decision["id"],
            "geometry_fingerprint": geometry["fingerprint"], "fact_fingerprint": fact["fingerprint"]}}
    except arc_geometry.ArcError as exc:
        raise ScaleError(exc.code, exc.message) from None


def measure_arc(points, geometry, resolution, radius_required=False):
    """Keep certified arc quantities separate from any display approximation."""
    geometry = _geometry(geometry)
    if not isinstance(resolution, dict) or not isinstance(resolution.get("binding"), dict):
        _fail("scale_unbound", "Resolve a verified scale before measuring.")
    binding = resolution["binding"]
    if (set(binding) != {"fact_id", "decision_id", "geometry_fingerprint", "fact_fingerprint"} or
            any(not isinstance(value, str) or not value for value in binding.values()) or
            binding["geometry_fingerprint"] != geometry["fingerprint"]):
        _fail("scale_unbound", "The resolved scale must bind these exact coordinates.")
    if type(radius_required) is not bool:
        _fail("arc_invalid", "Radius measurement is required only by an explicit radius check.")
    arc = validate_arc(points, geometry)
    ratio = str(_number(resolution.get("meters_per_point"), positive=True))
    try:
        measured = arc_geometry.measure_arc(arc, ratio)
        result = {"kind": "circular_arc", "pdf_points": [
            geometry_module.display_to_pdf(point, geometry) for point in points],
            "arc": arc_geometry.describe_arc(arc), "meters": measured["meters"],
            "certificate": measured["certificate"], "scale_binding": copy.deepcopy(binding)}
        if radius_required:
            result["radius_measurement"] = arc_geometry.measure_radius(arc, ratio)
        return result
    except arc_geometry.ArcError as exc:
        raise ScaleError(exc.code, exc.message) from None


def _span_ray(value):
    """Bound and reject aliases before parsing untrusted saved integer strings."""
    if not isinstance(value, list) or len(value) != 2:
        _fail("arc_invalid", "A saved arc ray needs two canonical integer strings.")
    result = []
    for item in value:
        if not isinstance(item, str) or not 1 <= len(item) <= 79:
            _fail("arc_invalid", "A saved arc ray exceeds its bounded integer format.")
        digits = item[1:] if item.startswith("-") else item
        if (not digits or len(digits) > 78 or any(c not in "0123456789" for c in digits) or
                (digits.startswith("0") and item != "0")):
            _fail("arc_invalid", "A saved arc ray must use canonical signed decimal integers.")
        number = int(item)
        if abs(number).bit_length() > arc_geometry.MAX_RAY_BITS:
            _fail("arc_invalid", "A saved arc ray exceeds its integer bit bound.")
        result.append(number)
    return tuple(result)


def validate_arc_span(base_points, start_ray, end_ray, geometry):
    """Retain the complete original circle and derive its exact directed subset."""
    geometry = _geometry(geometry)
    start, end = _span_ray(start_ray), _span_ray(end_ray)
    root = validate_arc(base_points, geometry)
    try:
        span = arc_geometry.make_span(root, start, end)
        if not arc_geometry.span_contained_in_rect(span, _pdf_rect([0, 0, 1, 1], geometry)):
            _fail("outside_crop", "Keep the entire circular arc span inside the effective source crop.")
        return span
    except arc_geometry.ArcError as exc:
        raise ScaleError(exc.code, exc.message) from None


def resolve_arc_span(facts, decisions, fact_id, geometry, base_points, start_ray, end_ray):
    """Apply unchanged scale authority to the complete derived curve interval."""
    geometry = _geometry(geometry)
    span = validate_arc_span(base_points, start_ray, end_ray, geometry)
    fact = _select(facts, fact_id)
    _current(fact, geometry)
    try:
        if not arc_geometry.span_contained_in_rect(span, _pdf_rect(fact["view"], geometry)):
            _fail("outside_view", "Keep the complete circular arc span inside the selected scale view.")
        decision = _decision(fact, decisions)
        if not decision or decision["state"] != "verified":
            _fail("scale_unverified", "Explicitly verify the selected scale before measuring.")
        ratio = _numeric(fact)
        for other in facts:
            if other is fact or not isinstance(other, dict) or other.get("source") != fact["source"]:
                continue
            try:
                other_decision = _decision(other, decisions)
                if other_decision and other_decision["state"] in INACTIVE:
                    continue
            except ScaleError:
                pass  # Damaged evidence is not an inactive competing view.
            if arc_geometry.span_intersects_rect(span, _pdf_rect(other.get("view"), geometry), closed=True):
                _fail("scale_conflict", "The circular arc span intersects another active scale view; resolve that scale first.")
        return {"meters_per_point": str(ratio), "binding": {
            "fact_id": fact["id"], "decision_id": decision["id"],
            "geometry_fingerprint": geometry["fingerprint"], "fact_fingerprint": fact["fingerprint"]}}
    except arc_geometry.ArcError as exc:
        raise ScaleError(exc.code, exc.message) from None


def measure_arc_span(base_points, start_ray, end_ray, geometry, resolution, radius_required=False):
    """Certify length without inventing rounded child controls or a radius check."""
    geometry = _geometry(geometry)
    if not isinstance(resolution, dict) or not isinstance(resolution.get("binding"), dict):
        _fail("scale_unbound", "Resolve a verified scale before measuring.")
    binding = resolution["binding"]
    if (set(binding) != {"fact_id", "decision_id", "geometry_fingerprint", "fact_fingerprint"} or
            any(not isinstance(value, str) or not value for value in binding.values()) or
            binding["geometry_fingerprint"] != geometry["fingerprint"]):
        _fail("scale_unbound", "The resolved scale must bind these exact coordinates.")
    if type(radius_required) is not bool:
        _fail("arc_invalid", "Radius measurement is required only by an explicit radius check.")
    span = validate_arc_span(base_points, start_ray, end_ray, geometry)
    ratio = str(_number(resolution.get("meters_per_point"), positive=True))
    try:
        measured = arc_geometry.measure_span(span, ratio)
        result = {"kind": "circular_arc_span", "pdf_points": [],
                  "arc": arc_geometry.describe_span(span), "meters": measured["meters"],
                  "certificate": measured["certificate"], "scale_binding": copy.deepcopy(binding)}
        if radius_required:
            result["radius_measurement"] = arc_geometry.measure_radius(span.root, ratio)
        return result
    except arc_geometry.ArcError as exc:
        raise ScaleError(exc.code, exc.message) from None


def measurement_view(measurement, facts, decisions, geometry_or_none):
    result = copy.deepcopy(measurement)
    result.update(validity="blocked", block_reason=None, block_message=None,
                  stored_meters=measurement.get("meters"), meters=None)
    try:
        geometry = _geometry(geometry_or_none)
        if not isinstance(measurement.get("scale_binding"), dict):
            _fail("scale_unbound", "This historical measurement needs explicit recalculation.")
        _source(measurement.get("source"), geometry)
        binding = measurement["scale_binding"]
        if measurement.get("calibration_id") != binding.get("fact_id"):
            _fail("scale_unbound", "The measurement scale link differs from its calculation binding.")
        resolution = resolve(facts, decisions, binding.get("fact_id"), geometry, measurement.get("points"))
        if resolution["binding"] != binding:
            _fail("scale_binding_changed", "A scale decision changed; explicitly recalculate this measurement.")
        actual = measure_path(measurement["points"], geometry, resolution)
        if (_number(measurement.get("meters"), positive=True) != Decimal(actual["meters"]) or
                _number(measurement.get("paper_points"), positive=True) != Decimal(actual["paper_points"]) or
                measurement.get("pdf_points") != actual["pdf_points"]):
            _fail("measurement_changed", "The stored result differs from its source-bound calculation.")
        result.update(validity="current", meters=actual["meters"])
    except ScaleError as exc:
        result["block_reason"] = exc.code
        result["block_message"] = exc.message
    return result
