"""Pure source-bound duct quantities, Python 3.9+; no model quantity authority."""
import copy
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from fractions import Fraction
import hashlib
import importlib.util
import json
from math import gcd
from pathlib import Path
import re
from types import SimpleNamespace

_spec = importlib.util.spec_from_file_location(
    "duct_sheet_scale", Path(__file__).with_name("sheet_scale.py"))
sheet_scale = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sheet_scale)
_links_spec = importlib.util.spec_from_file_location('duct_reading_links', Path(__file__).with_name('project_duct_reading_links.py'))
reading_links = importlib.util.module_from_spec(_links_spec)
_links_spec.loader.exec_module(reading_links)
_checks_spec = importlib.util.spec_from_file_location('duct_arc_obligations', Path(__file__).with_name('duct_arc_obligations.py'))
arc_checks = importlib.util.module_from_spec(_checks_spec)
_checks_spec.loader.exec_module(arc_checks)
_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
_SHA = re.compile(r"^[0-9a-f]{64}$")
_METER = Decimal("0.000001")
_FOOT = Decimal("0.3048")
_ROLES = {"duct", "wall", "leader", "dimension", "legend", "grid", "unrelated_service", "unknown"}


class DuctError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _fail(code, message):
    raise DuctError(code, message)


def _plain(value, maximum=500, nullable=False):
    if value is None and nullable:
        return value
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum or
            any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value)):
        _fail("invalid_text", "Use bounded nonempty Unicode plain text.")
    return value


def _hash(value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        _fail("invalid_identity", "A SHA-256 identity is required.")
    return value


def _exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields.split()):
        _fail("invalid_fields", "Record fields do not match the duct contract.")
    return value


def _array(value, maximum=2000, minimum=0):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        _fail("invalid_collection", "Use a bounded array of contract records.")
    return value


def _ids(value, maximum=128, minimum=0):
    _array(value, maximum, minimum)
    for item in value:
        _plain(item, 160)
    if len(set(value)) != len(value):
        _fail("duplicate_identity", "Identifiers must be unique within a collection.")
    return value


def _enum(value, allowed):
    if not isinstance(value, str) or value not in allowed:
        _fail("invalid_choice", "Choose a supported duct contract value.")
    return value


def _number(value, signed=False, positive=False, strings=True):
    if (strings and not isinstance(value, str)) or isinstance(value, bool):
        _fail("invalid_number", "Dimension readings use finite decimal strings.")
    if not isinstance(value, (str, int, float)) or len(str(value)) > 80:
        _fail("invalid_number", "Use a bounded finite decimal number.")
    try:
        number = Decimal(str(value))
        if (not number.is_finite() or number.copy_abs() > Decimal("1e12") or
                abs(number.as_tuple().exponent) > 40 or
                (not signed and number < 0) or (positive and number <= 0)):
            raise InvalidOperation()
        return number
    except (ValueError, InvalidOperation, OverflowError):
        _fail("invalid_number", "Use a bounded finite decimal number.")


def _path(points, empty=False):
    _array(points, 128, 0 if empty else 2)
    if empty and len(points) == 1:
        _fail("invalid_points", "A path requires two or more points.")
    for pair in points:
        _array(pair, 2, 2)
        if any(_number(v, strings=False) > 1 for v in pair):
            _fail("invalid_points", "Coordinates must be inside normalized page bounds.")


def _source(value):
    _exact(value, "revision_id index sheet_id geometry_fingerprint")
    for key in ("revision_id", "sheet_id", "geometry_fingerprint"):
        _hash(value[key])
    if type(value["index"]) is not int or not 0 <= value["index"] <= 4294967295:
        _fail("invalid_source", "A bounded source page index is required.")


def fingerprint(value):
    """Canonical finite JSON identity; bounds also protect direct API callers."""
    def check(item, depth=0):
        if depth > 24:
            _fail("invalid_record", "The record exceeds nesting limits.")
        if isinstance(item, str):
            if len(item) > 32768 or any(0xD800 <= ord(c) <= 0xDFFF for c in item):
                _fail("invalid_record", "Use bounded Unicode scalar text.")
        elif isinstance(item, dict):
            if len(item) > 20000 or any(not isinstance(k, str) for k in item):
                _fail("invalid_record", "JSON object keys must be text.")
            for key, child in item.items():
                check(key, depth + 1)
                check(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 20000:
                _fail("invalid_record", "The record exceeds collection limits.")
            for child in item:
                check(child, depth + 1)
        elif item is not None and type(item) not in (int, float, bool):
            _fail("invalid_record", "Only finite JSON data is accepted.")
    check(value)
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, OverflowError):
        _fail("invalid_record", "Only finite JSON data is accepted.")
    if len(raw) > 32 * 1024 * 1024:
        _fail("invalid_record", "The record exceeds byte limits.")
    return hashlib.sha256(raw).hexdigest()


def _ray(value):
    _array(value, 2, 2)
    if any(not isinstance(v, str) or len(v) > 79 or
           not re.fullmatch(r"0|-[1-9][0-9]*|[1-9][0-9]*", v) for v in value):
        _fail("arc_invalid", "Arc boundary rays use bounded canonical integer strings.")
    ray = tuple(int(v) for v in value)
    if any(abs(v).bit_length() > 256 for v in ray) or gcd(*ray) != 1:
        _fail("arc_invalid", "Arc boundary rays must be nonzero primitive directed pairs.")
    return ray


def validate_observation(value):
    """Validate a proposal without granting it admission or quantity authority."""
    fingerprint(value)
    _exact(value, "schema id source group geometry readings evidence_ids issues")
    _enum(value["schema"], {"duct-observation-1", "duct-observation-2", "duct-observation-3"})
    _plain(value["id"], 160)
    _source(value["source"])
    top_evidence = set(_ids(value["evidence_ids"], minimum=1))
    _ids(value["issues"])
    group = _exact(value["group"], "scope system material work_status size evidence_ids")
    _enum(group["scope"], {"ductwork"})
    for field in ("system", "material"):
        _plain(group[field], 200, nullable=True)
    if group["work_status"] is not None:
        _enum(group["work_status"], {"new_install", "existing_to_remain", "demolition"})
    referenced = set(_ids(group["evidence_ids"]))
    if any(group[k] is not None for k in ("system", "material", "size", "work_status")) and not referenced:
        _fail("evidence_missing", "Known grouping properties require source evidence.")
    size = group["size"]
    if size is not None:
        _exact(size, "shape dimensions unit original_text")
        _enum(size["shape"], {"rectangular", "round", "oval"})
        _enum(size["unit"], set(sheet_scale.UNITS))
        _plain(size["original_text"], 200)
        count = 1 if size["shape"] == "round" else 2
        _array(size["dimensions"], count, count)
        for dimension in size["dimensions"]:
            _number(dimension, positive=True)
    readings = {}
    for reading in _array(value["readings"], 128):
        _exact(reading, "id kind value unit datum_id evidence_ids")
        _plain(reading["id"], 160)
        if reading["id"] in readings:
            _fail("duplicate_identity", "Reading IDs must be unique.")
        readings[reading["id"]] = reading
        _enum(reading["kind"], {"length", "elevation"})
        _enum(reading["unit"], set(sheet_scale.UNITS))
        _number(reading["value"], signed=reading["kind"] == "elevation",
                positive=reading["kind"] == "length")
        references = _ids(reading["evidence_ids"], minimum=1)
        referenced.update(references)
        if reading["kind"] == "length":
            if reading["datum_id"] is not None:
                _fail("invalid_datum", "A length reading does not declare an elevation datum.")
        elif reading["datum_id"] not in references:
            _fail("invalid_datum", "Elevation requires its retained datum evidence identity.")
    geometry = value["geometry"]
    if not isinstance(geometry, dict):
        _fail("invalid_geometry", "Provide a tagged geometry object.")
    kinds = ({"planar", "vertical", "slope", "unsupported"}
             if value["schema"] == "duct-observation-1" else
             {"circular_arc"} if value["schema"] == "duct-observation-2" else {"circular_arc_span"})
    kind = _enum(geometry.get("kind"), kinds)
    reading_ids = []
    if kind in {"planar", "circular_arc", "circular_arc_span"}:
        fields = "kind points scale_fact_id dimension_check_ids"
        if kind == "circular_arc_span":
            fields = "kind base_points start_ray end_ray scale_fact_id dimension_check_ids"
        _exact(geometry, fields + (" radius_check_ids" if kind != "planar" else ""))
        points = geometry["base_points"] if kind == "circular_arc_span" else geometry["points"]
        _path(points)
        if kind == "circular_arc_span":
            for name in ("start_ray", "end_ray"):
                _ray(geometry[name])
        _plain(geometry["scale_fact_id"], 160)
        reading_ids += _ids(geometry["dimension_check_ids"])
        if kind in {"circular_arc", "circular_arc_span"}:
            _array(points, 3, 3)
            radius_ids = _ids(geometry["radius_check_ids"])
            if set(radius_ids) & set(reading_ids):
                _fail("reading_role_conflict", "An arc reading cannot be both centerline radius and whole-arc length.")
            reading_ids += radius_ids
    elif kind == "vertical":
        _exact(geometry, "kind rise_reading_id elevation_ids support_ids")
        if geometry["rise_reading_id"] is not None:
            _plain(geometry["rise_reading_id"], 160)
            reading_ids.append(geometry["rise_reading_id"])
        elevations = _ids(geometry["elevation_ids"], 2)
        if len(elevations) not in (0, 2) or (elevations and geometry["rise_reading_id"] is not None):
            _fail("invalid_geometry", "Use one rise reading or two elevations, never both.")
        reading_ids += elevations
        referenced.update(_ids(geometry["support_ids"], minimum=1))
    elif kind == "slope":
        _exact(geometry, "kind projection elevation_ids support_ids")
        projection = geometry["projection"]
        if not isinstance(projection, dict):
            _fail("invalid_geometry", "A slope requires a tagged projection.")
        if projection.get("kind") == "scaled_path":
            _exact(projection, "kind points scale_fact_id")
            _path(projection["points"])
            if len(projection["points"]) != 2:
                _fail("invalid_geometry", "A straight slope projection requires exactly two endpoints.")
            _plain(projection["scale_fact_id"], 160)
        elif projection.get("kind") == "dimension":
            _exact(projection, "kind reading_id")
            _plain(projection["reading_id"], 160)
            reading_ids.append(projection["reading_id"])
        else:
            _fail("invalid_geometry", "A slope projection is scaled_path or dimension.")
        reading_ids += _ids(geometry["elevation_ids"], 2, 2)
        referenced.update(_ids(geometry["support_ids"], minimum=1))
    else:
        _exact(geometry, "kind reason points support_ids")
        _plain(geometry["reason"], 160)
        _path(geometry["points"], empty=True)
        referenced.update(_ids(geometry["support_ids"]))
    if not referenced <= top_evidence:
        _fail("evidence_missing", "Every used evidence ID must be retained by the observation.")
    if any(identifier not in readings for identifier in reading_ids):
        _fail("reading_missing", "Geometry refers to a missing reading.")
    if kind in {"circular_arc", "circular_arc_span"} and any(readings[identifier]["kind"] != "length" for identifier in reading_ids):
        _fail("reading_role_conflict", "Whole-arc length and centerline radius checks require length readings.")
    return copy.deepcopy(value)


def _current_context(current_sources):
    if not isinstance(current_sources, dict) or len(current_sources) > 2000:
        _fail("invalid_sources", "Use a bounded mapping of current source sheets.")
    geometries, evidence = {}, {}
    for sheet_id, context in current_sources.items():
        _hash(sheet_id)
        _exact(context, "geometry evidence")
        geometry = context["geometry"]
        try:
            actual = sheet_scale.geometry_module.sheet_geometry(geometry, geometry.get("revision_id"))
        except (sheet_scale.geometry_module.GeometryError, AttributeError) as exc:
            _fail("geometry_invalid", str(exc))
        if actual["fingerprint"] != geometry.get("fingerprint") or actual["sheet_id"] != sheet_id:
            _fail("geometry_changed", "Current source geometry does not match its identity.")
        geometries[sheet_id] = actual
        expected_source = {k: actual[k] for k in ("revision_id", "index", "sheet_id")}
        expected_source["geometry_fingerprint"] = actual["fingerprint"]
        for record in _array(context["evidence"], 10000):
            _exact(record, "id source bbox text artifact_sha256")
            _plain(record["id"], 160)
            _source(record["source"])
            if record["source"] != expected_source:
                _fail("evidence_source_changed", "Retained evidence must bind current source coordinates.")
            _array(record["bbox"], 4, 4)
            bounds = [_number(v, strings=False) for v in record["bbox"]]
            if not (0 <= bounds[0] < bounds[2] <= 1 and 0 <= bounds[1] < bounds[3] <= 1):
                _fail("invalid_bounds", "Evidence needs positive normalized bounds.")
            _plain(record["text"], 4000, nullable=True)
            _hash(record["artifact_sha256"])
            if record["id"] in evidence:
                _fail("duplicate_identity", "Evidence identifiers must be globally unique.")
            evidence[record["id"]] = record
    return geometries, evidence


def _identity_map(value):
    if not isinstance(value, dict) or len(value) > 2000:
        _fail("invalid_identities", "Use a bounded identity mapping.")
    for key, val in value.items():
        _plain(key, 160)
        _hash(val)
    return value


def _admissions(records):
    current, ids = {}, set()
    for row in _array(records, 10000):
        _exact(row, "id observation_id observation_fingerprint state role evidence_ids method rule_fingerprint")
        _plain(row["id"], 160)
        _plain(row["observation_id"], 160)
        _hash(row["observation_fingerprint"])
        _hash(row["rule_fingerprint"])
        _enum(row["state"], {"included", "excluded", "unresolved"})
        _enum(row["role"], _ROLES)
        _enum(row["method"], {"source_validator", "user_review"})
        _ids(row["evidence_ids"], minimum=1)
        if row["id"] in ids:
            _fail("duplicate_identity", "Admission event IDs must be unique.")
        ids.add(row["id"])
        current[row["observation_id"]] = row
    return current


def _group(value):
    result = {k: value[k] for k in ("scope", "system", "material", "work_status")}
    size = value["size"]
    if size is None:
        result["size"] = None
    else:
        with localcontext(_CONTEXT):
            dimensions = [format((_number(v, positive=True) * sheet_scale.UNITS[size["unit"]]
                / sheet_scale.UNITS["in"]).normalize(), "f") for v in size["dimensions"]]
        result["size"] = {"shape": size["shape"], "dimensions": dimensions, "unit": "in"}
    return result


def _stored(meters, positive=True):
    with localcontext(_CONTEXT):
        if not meters.is_finite() or abs(meters) > Decimal("1e12") or (positive and meters <= 0):
            _fail("quantity_range", "A supported physical length must be positive and bounded.")
        value = meters.quantize(_METER)
        if positive and value == 0:
            _fail("quantity_resolution", "Positive length is below stored measurement resolution.")
        return format(value, "f")


def _feet(meters):
    with localcontext(_CONTEXT):
        return format((Decimal(meters) / _FOOT).quantize(Decimal("0.01")), "f")


def _read_meters(reading, kind):
    if reading["kind"] != kind:
        _fail("reading_kind_mismatch", "Use the matching dimension or elevation reading.")
    with localcontext(_CONTEXT):
        return _number(reading["value"], signed=kind == "elevation", positive=kind == "length") * sheet_scale.UNITS[reading["unit"]]


def _rise(ids, readings):
    low, high = (readings[v] for v in ids)
    if not low["datum_id"] or low["datum_id"] != high["datum_id"]:
        _fail("datum_mismatch", "Both elevations need the same retained datum evidence.")
    with localcontext(_CONTEXT):
        return abs(_read_meters(high, "elevation") - _read_meters(low, "elevation"))


def validate_planar_path(points, geometry):
    """Validate the complete source path before a split, independently of scale."""
    _path(points)
    try:
        paper = sheet_scale.geometry_module.paper_length(points, geometry)
        pdf_points = [sheet_scale.geometry_module.display_to_pdf(point, geometry) for point in points]
    except sheet_scale.geometry_module.GeometryError as exc:
        _fail(exc.code, exc.message)
    edges = []
    for start, end in zip(pdf_points, pdf_points[1:]):
        if start == end:
            _fail("path_retraced", "A traced physical edge cannot be measured more than once.")
        dx, dy = end[0] - start[0], end[1] - start[1]
        for other_start, other_end in edges:
            if all(dx * (point[1] - start[1]) == dy * (point[0] - start[0])
                   for point in (other_start, other_end)):
                axis = 0 if dx else 1
                lower = max(min(start[axis], end[axis]), min(other_start[axis], other_end[axis]))
                upper = min(max(start[axis], end[axis]), max(other_start[axis], other_end[axis]))
                if lower < upper:
                    _fail("path_retraced", "Positive-length collinear path overlap cannot be counted twice.")
        edges.append((start, end))
    return {"pdf_points": pdf_points, "paper_points": str(paper)}


def partition_planar_path(points, geometry, cuts):
    """Derive contiguous child source paths; never accept caller child geometry."""
    validated = validate_planar_path(points, geometry)
    _array(cuts, 127, 1)
    parent = validated["pdf_points"]
    previous = None
    by_edge = {}
    for cut in cuts:
        _exact(cut, "edge_index fraction evidence_ids")
        edge = cut["edge_index"]
        if type(edge) is not int or not 0 <= edge < len(parent) - 1:
            _fail("split_cut_invalid", "Choose an existing parent edge.")
        fraction = _number(cut["fraction"], positive=True)
        if fraction > 1 or (edge == len(parent) - 2 and fraction == 1):
            _fail("split_cut_invalid", "Cuts must be internal to the retained parent path.")
        _ids(cut["evidence_ids"], minimum=1)
        position = (edge, fraction)
        if previous is not None and position <= previous:
            _fail("split_cut_order", "Cuts must be strictly ordered and distinct.")
        previous = position
        with localcontext(_CONTEXT):
            point = [int((Decimal(a) + Decimal(b - a) * fraction).to_integral_value())
                     for a, b in zip(parent[edge], parent[edge + 1])]
        if point == parent[edge] or (point == parent[edge + 1] and fraction != 1):
            _fail("split_cut_resolution", "The cut collapses onto an existing endpoint.")
        by_edge.setdefault(edge, []).append(point)
    paths, current = [], [parent[0]]
    for index in range(len(parent) - 1):
        for point in by_edge.get(index, []):
            if current[-1] == point:
                _fail("split_cut_resolution", "Two cuts collapse onto the same source coordinate.")
            current.append(point)
            paths.append(current)
            current = [point]
        if current[-1] != parent[index + 1]:
            current.append(parent[index + 1])
    paths.append(current)
    displayed = []
    for path in paths:
        converted = [sheet_scale.geometry_module.pdf_to_display(point, geometry) for point in path]
        if validate_planar_path(converted, geometry)["pdf_points"] != path:
            _fail("split_coordinate_precision", "The generated cut cannot retain exact source coordinates.")
        displayed.append(converted)
    return {"paths": displayed, "pdf_paths": copy.deepcopy(paths),
            "parent_pdf_points": parent, "cuts": copy.deepcopy(cuts)}


def arc_span(shape, geometry):
    """Resolve only exact source geometry; this does not admit derived lineage."""
    if shape["kind"] == "circular_arc":
        return sheet_scale.arc_geometry.root_span(sheet_scale.validate_arc(shape["points"], geometry))
    return sheet_scale.validate_arc_span(shape["base_points"], shape["start_ray"], shape["end_ray"], geometry)


def span_geometry(shape, span):
    return {"kind": "circular_arc_span",
        "base_points": copy.deepcopy(shape.get("base_points", shape.get("points"))),
        "start_ray": [str(v) for v in span.start_ray], "end_ray": [str(v) for v in span.end_ray],
        "scale_fact_id": shape["scale_fact_id"], "dimension_check_ids": [], "radius_check_ids": []}


def partition_arc_span(shape, geometry, cuts):
    """Derive children on one retained circle without rounded endpoint refitting."""
    _array(cuts, 127, 1)
    span = arc_span(shape, geometry)
    rays = []
    try:
        for cut in cuts:
            _exact(cut, "source_point evidence_ids")
            _path([cut["source_point"], cut["source_point"]])
            _ids(cut["evidence_ids"], minimum=1)
            point = sheet_scale.geometry_module.display_to_pdf(cut["source_point"], geometry)
            rays.append(sheet_scale.arc_geometry.ray_from_point(span.root, point))
        children = sheet_scale.arc_geometry.partition_span(span, rays)
    except sheet_scale.arc_geometry.ArcError as exc:
        _fail(exc.code, exc.message)
    return {"geometries": [span_geometry(shape, child) for child in children],
            "cuts": copy.deepcopy(cuts), "rays": rays, "span": span}


def _pdf_box(bbox, geometry):
    x0, y0, x1, y1 = bbox
    points = [sheet_scale.geometry_module.display_to_pdf(point, geometry)
              for point in ([x0, y0], [x1, y0], [x1, y1], [x0, y1])]
    return [min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points)]


def split_boundary_inside(partition, index, bbox, geometry):
    if "span" in partition:
        try:
            return sheet_scale.arc_geometry.endpoint_in_rect(partition["span"].root,
                partition["rays"][index], _pdf_box(bbox, geometry))
        except sheet_scale.arc_geometry.ArcError as exc:
            _fail(exc.code, exc.message)
    point = partition["paths"][index][-1]
    return (Decimal(str(bbox[0])) <= Decimal(str(point[0])) <= Decimal(str(bbox[2])) and
            Decimal(str(bbox[1])) <= Decimal(str(point[1])) <= Decimal(str(bbox[3])))


def prove_split_partition(parent, children, geometry, cuts, *, arc=False):
    """Shared exact structural proof for calculation, rebind and family retirement."""
    if arc:
        partition = partition_arc_span(parent["geometry"], geometry, cuts)
        if len(children) != len(partition["geometries"]):
            _fail("split_partition_changed", "The source interval needs every ordered child.")
        for child, expected in zip(children, partition["geometries"]):
            if child is None:
                continue
            if (child["source"] != parent["source"] or child["schema"] != "duct-observation-3" or
                    child["geometry"]["kind"] != "circular_arc_span" or
                    any(child["geometry"][key] != expected[key] for key in ("base_points", "start_ray", "end_ray"))):
                _fail("split_partition_changed", "Derived children must exactly partition the retained source circle and interval.")
        return partition
    partition = partition_planar_path(parent["geometry"]["points"], geometry, cuts)
    if len(children) != len(partition["pdf_paths"]):
        _fail("split_partition_changed", "The retained planar path needs every ordered child, without extras.")
    for child, path in zip(children, partition["pdf_paths"]):
        if child is not None and (child["source"] != parent["source"] or child["geometry"]["kind"] != "planar" or
                validate_planar_path(child["geometry"]["points"], geometry)["pdf_points"] != path):
            _fail("split_partition_changed", "Children must exactly partition the retained planar path.")
    return partition


def _segment_covered(start, end, boxes):
    """Cover a canonical PDF segment with an exact union of closed intervals."""
    intervals = []
    for box in boxes:
        lower, upper = Fraction(0), Fraction(1)
        for axis in (0, 1):
            delta = end[axis] - start[axis]
            if delta == 0:
                if not box[axis] <= start[axis] <= box[axis + 2]:
                    break
            else:
                bounds = sorted((Fraction(box[axis] - start[axis], delta),
                                 Fraction(box[axis + 2] - start[axis], delta)))
                lower, upper = max(lower, bounds[0]), min(upper, bounds[1])
                if lower > upper:
                    break
        else:
            intervals.append((lower, upper))
    covered = Fraction(0)
    for lower, upper in sorted(intervals):
        if lower > covered:
            return False
        covered = max(covered, upper)
        if covered == 1:
            return True
    return False


def _require_graphic_support(observation, pdf_points, geometry, evidence, arc=None, span=None):
    """Only observation-cited, same-source null-text graphics support a path."""
    graphics = [evidence[key] for key in observation["evidence_ids"] if key in evidence
                and evidence[key]["text"] is None
                and evidence[key]["source"] == observation["source"]]
    if not graphics:
        _fail("graphic_support_missing", "Cite a graphic region on this source that supports the drawn path.")
    boxes = []
    for graphic in graphics:
        x0, y0, x1, y1 = graphic["bbox"]
        corners = [sheet_scale.geometry_module.display_to_pdf(point, geometry)
                   for point in ([x0, y0], [x1, y0], [x1, y1], [x0, y1])]
        boxes.append([min(point[0] for point in corners), min(point[1] for point in corners),
                      max(point[0] for point in corners), max(point[1] for point in corners)])
    if arc is not None or span is not None:
        try:
            covered = (sheet_scale.arc_geometry.span_covered_by_rects(span, boxes) if span is not None else
                       sheet_scale.arc_geometry.arc_covered_by_rects(arc, boxes))
        except sheet_scale.arc_geometry.ArcError as exc:
            _fail(exc.code, exc.message)
    else:
        covered = all(_segment_covered(start, end, boxes) for start, end in zip(pdf_points, pdf_points[1:]))
    if not covered:
        _fail("graphic_support_incomplete", "Cited graphic regions must cover every segment of the drawn path.")


def _arc_measurement(observation, geometry, facts, decisions, evidence, fact_id=None):
    shape = observation["geometry"]
    fact_id = shape["scale_fact_id"] if fact_id is None else fact_id
    if shape["kind"] == "circular_arc_span":
        span = arc_span(shape, geometry)
        _require_graphic_support(observation, None, geometry, evidence, span=span)
        resolution = sheet_scale.resolve_arc_span(facts, decisions, fact_id, geometry,
            shape["base_points"], shape["start_ray"], shape["end_ray"])
        return sheet_scale.measure_arc_span(shape["base_points"], shape["start_ray"], shape["end_ray"],
            geometry, resolution, radius_required=bool(shape["radius_check_ids"]))
    arc = sheet_scale.validate_arc(shape["points"], geometry)
    _require_graphic_support(observation, None, geometry, evidence, arc=arc)
    resolution = sheet_scale.resolve_arc(facts, decisions, fact_id, geometry, shape["points"])
    return sheet_scale.measure_arc(shape["points"], geometry, resolution,
        radius_required=bool(shape["radius_check_ids"]))


def _measure(observation, geometry, facts, decisions, evidence):
    shape = observation["geometry"]
    readings = {r["id"]: r for r in observation["readings"]}
    def path_measure(path):
        validated = validate_planar_path(path["points"], geometry)
        _require_graphic_support(observation, validated["pdf_points"], geometry, evidence)
        resolution = sheet_scale.resolve(facts, decisions, path["scale_fact_id"], geometry, path["points"])
        return sheet_scale.measure_path(path["points"], geometry, resolution)
    kind = shape["kind"]
    if kind == "unsupported":
        _fail(shape["reason"], "This physical portion needs supported geometry before measurement.")
    if kind == "planar":
        measurement = path_measure(shape)
        for identifier in shape["dimension_check_ids"]:
            stated = _stored(_read_meters(readings[identifier], "length"))
            if stated != measurement["meters"]:
                _fail("dimension_conflict", "Stated dimensions and scaled geometry disagree.")
        return measurement["meters"], "measured_planar", {"measurement": measurement}
    if kind in ("circular_arc", "circular_arc_span"):
        measurement = _arc_measurement(observation, geometry, facts, decisions, evidence)
        for identifier in shape["dimension_check_ids"]:
            stated = _stored(_read_meters(readings[identifier], "length"))
            if stated != measurement["meters"]:
                _fail("dimension_conflict", "Stated whole-arc length and scaled circular geometry disagree.")
        for identifier in shape["radius_check_ids"]:
            stated = _stored(_read_meters(readings[identifier], "length"))
            if stated != measurement["radius_measurement"]["meters"]:
                _fail("radius_conflict", "Stated centerline radius and scaled circular geometry disagree.")
        return measurement["meters"], "measured_planar", {"measurement": measurement}
    if kind == "vertical":
        if shape["rise_reading_id"] is not None:
            rise = _read_meters(readings[shape["rise_reading_id"]], "length")
        elif shape["elevation_ids"]:
            rise = _rise(shape["elevation_ids"], readings)
        else:
            _fail("vertical_length_unknown", "The indicated rise has no supported height.")
        meters = _stored(rise)
        return meters, "dimension_derived", {"rise_meters": meters, "readings": copy.deepcopy(observation["readings"])}
    projection = shape["projection"]
    components = {"readings": copy.deepcopy(observation["readings"])}
    if projection["kind"] == "scaled_path":
        components["measurement"] = path_measure(projection)
        projected = components["measurement"]["meters"]
    else:
        projected = _stored(_read_meters(readings[projection["reading_id"]], "length"))
    rise = _stored(_rise(shape["elevation_ids"], readings), positive=False)
    with localcontext(_CONTEXT):
        meters = _stored((Decimal(projected) ** 2 + Decimal(rise) ** 2).sqrt())
    components.update(projection_meters=projected, rise_meters=rise)
    return meters, "dimension_derived", components


def _prepare(observation, admissions, geometries, evidence, facts, decisions, rule, relationships, link_core, lineage_valid=True):
    identifier = observation["id"]
    geometry = geometries.get(observation["source"]["sheet_id"])
    admission = admissions.get(identifier)
    issues = list(observation["issues"])
    if not lineage_valid:
        issues.append("arc_span_lineage_missing")
    if geometry is None or any(observation["source"][k] != geometry[k] for k in ("revision_id", "index", "sheet_id")):
        issues.append("source_changed")
    elif observation["source"]["geometry_fingerprint"] != geometry["fingerprint"]:
        issues.append("geometry_changed")
    if admission is None:
        issues.append("admission_missing")
    elif admission["observation_fingerprint"] != fingerprint(observation) or admission["rule_fingerprint"] != rule["fingerprint"]:
        issues.append("admission_changed")
    required = set(observation["evidence_ids"])
    if admission:
        required.update(admission["evidence_ids"])
    if any(key not in evidence for key in required):
        issues.append("evidence_missing")
    link_dependency = None
    if admission and admission['state'] == 'included' and admission['role'] == 'duct' and observation['geometry']['kind'] != 'unsupported':
        link_issues, link_dependency, unused = reading_links.applicability(link_core, observation,
            relationships, geometries, evidence, admission['evidence_ids'])
        issues.extend(link_issues)
        if link_issues and geometry is not None:
            path_geometry = observation['geometry']
            if path_geometry['kind'] == 'slope':
                path_geometry = path_geometry['projection']
            if path_geometry['kind'] in ('planar', 'scaled_path'):
                try:
                    path = validate_planar_path(path_geometry['points'], geometry)
                    _require_graphic_support(observation, path['pdf_points'], geometry, evidence)
                except (DuctError, sheet_scale.geometry_module.GeometryError) as exc:
                    issues.append(exc.code)
            elif path_geometry['kind'] in ('circular_arc', 'circular_arc_span'):
                try:
                    if path_geometry['kind'] == 'circular_arc_span':
                        span = arc_span(path_geometry, geometry)
                        _require_graphic_support(observation, None, geometry, evidence, span=span)
                    else:
                        arc = sheet_scale.validate_arc(path_geometry['points'], geometry)
                        _require_graphic_support(observation, None, geometry, evidence, arc=arc)
                except (DuctError, sheet_scale.ScaleError) as exc:
                    issues.append(exc.code)
    row = {"id": identifier, "observation_ids": [identifier], "group": _group(observation["group"]),
        "status": "unresolved", "method": None, "meters": None, "feet": None,
        "issues": issues, "components": {}, "dependency_fingerprint": None}
    prerequisites = list(issues)
    if not issues and admission["state"] == "excluded" and admission["role"] not in {"duct", "unknown"}:
        row["status"] = "excluded"
    elif not issues and admission["state"] == "included" and admission["role"] == "duct":
        try:
            meters, method, components = _measure(observation, geometry, facts, decisions, evidence)
            row.update(status="current", meters=meters, feet=_feet(meters), method=method, components=components)
        except (DuctError, sheet_scale.ScaleError, sheet_scale.geometry_module.GeometryError) as exc:
            row["issues"].append(exc.code)
    elif not issues:
        row["issues"].append("role_unresolved")
        prerequisites.append("role_unresolved")
    dependency = {"observation": observation, "admission": admission,
        "geometry": geometry["fingerprint"] if geometry else None,
        "evidence": {key: evidence.get(key) for key in sorted(required)}, "rule": rule}
    if link_dependency is not None:
        dependency['reading_relationships'] = link_dependency
    return row, dependency, prerequisites


def _correspond(records, observations, rows, dependencies, prerequisites, evidence):
    active = {}
    for record in _array(records, 2000):
        _exact(record, "id members primary_observation_id state evidence_ids")
        _plain(record["id"], 160)
        _identity_map(record["members"])
        if len(record["members"]) < 2:
            _fail("invalid_correspondence", "A correspondence requires two or more depictions.")
        _plain(record["primary_observation_id"], 160)
        if record["primary_observation_id"] not in record["members"]:
            _fail("invalid_correspondence", "The primary must be a listed depiction.")
        _enum(record["state"], {"verified", "uncertain", "rejected"})
        _ids(record["evidence_ids"], minimum=1)
        active[record["id"]] = record
    memberships = {}
    for record in active.values():
        if record["state"] != "rejected":
            for key in record["members"]:
                memberships.setdefault(key, []).append(record["id"])
    for record in active.values():
        if record["state"] == "rejected":
            continue
        members = record["members"]
        bad = (record["state"] != "verified" or any(key not in observations or
            fingerprint(observations[key]) != value for key, value in members.items()) or
            any(key not in evidence for key in record["evidence_ids"]) or
            any(len(memberships[key]) != 1 for key in members))
        present = [key for key in members if key in rows]
        if len({fingerprint(rows[key]["group"]) for key in present}) != 1:
            bad = True
        if any(prerequisites[key] or rows[key]["status"] != "current" or rows[key]["issues"]
               for key in present):
            bad = True
        length_conflict = len({rows[key]["meters"] for key in present if rows[key]["meters"] is not None}) > 1
        if length_conflict:
            bad = True
        shared = {"decision": record, "members": {key: {
            "inputs": dependencies[key], "calculation": copy.deepcopy(rows[key])} for key in present},
            "evidence": {key: evidence.get(key) for key in record["evidence_ids"]}}
        shared_hash = fingerprint(shared)
        for key in present:
            dependencies[key]["correspondence"] = shared_hash
            if bad:
                rows[key].update(status="unresolved", meters=None, feet=None)
                rows[key]["issues"].append("correspondence_unresolved")
                if length_conflict:
                    rows[key]["issues"].append("correspondence_length_conflict")
        if not bad:
            primary = record["primary_observation_id"]
            rows[primary]["observation_ids"] = [primary] + sorted(key for key in members if key != primary)
            for key in members:
                if key != primary:
                    rows[key].update(status="duplicate", method=None, meters=None, feet=None, issues=[], components={})


def _summarize(rows, coverage_issues):
    groups = {}
    for row in rows:
        if row["status"] in {"excluded", "duplicate"}:
            continue
        key = fingerprint(row["group"])
        groups.setdefault(key, {"id": key, "group": row["group"], "row_ids": [], "rows": []})
        groups[key]["row_ids"].append(row["id"])
        groups[key]["rows"].append(row)
    result = []
    with localcontext(_CONTEXT):
        for key in sorted(groups):
            group = groups[key]
            members = group.pop("rows")
            contributions = {}
            for method in ("measured_planar", "dimension_derived"):
                meters = sum((Decimal(r["meters"]) for r in members if r["status"] == "current"
                    and r["method"] == method and r["meters"] is not None), Decimal(0))
                contributions[method] = {"meters": _stored(meters, positive=False), "feet": _feet(meters)}
            total = sum((Decimal(v["meters"]) for v in contributions.values()), Decimal(0))
            complete = (not coverage_issues and all(r["status"] == "current" for r in members) and
                all(group["group"][k] is not None for k in ("system", "material", "size", "work_status")))
            group.update(known_subtotal_meters=_stored(total, positive=False), known_subtotal_ft=_feet(total),
                total_ft=_feet(total) if complete else None, complete=complete, contributions=contributions)
            result.append(group)
        complete = bool(result) and not coverage_issues and all(g["complete"] for g in result)
        statuses = {g["group"]["work_status"] for g in result}
        total = sum((Decimal(g["known_subtotal_meters"]) for g in result), Decimal(0))
        subtotal = _feet(total) if len(statuses) == 1 and None not in statuses else None
    return result, subtotal, subtotal if complete else None, complete


def _split_constraints(records, observations, rows, dependencies, geometries, evidence, facts, decisions, relationships, link_core):
    """Prove retained partitions before reconciling whole-run dimensions once."""
    if not records:
        return [], []
    _array(records, 1000)
    by_parent, by_id, owners = {}, {}, {}
    for record in records:
        arc_record = record.get("schema") == "duct-arc-split-1"
        _exact(record, ("schema " if arc_record else "") + "id parent parent_fingerprint cuts members evidence_ids")
        _plain(record["id"], 160)
        _hash(record["parent_fingerprint"])
        parent = validate_observation(record["parent"])
        allowed = {"circular_arc", "circular_arc_span"} if arc_record else {"planar"}
        if parent["geometry"]["kind"] not in allowed:
            _fail("split_parent_invalid", "A retained partition must use its explicit geometry contract.")
        if record["id"] in by_id or parent["id"] in by_parent:
            _fail("split_identity_conflict", "Split and retained-parent identities must be unique.")
        by_id[record["id"]] = record
        by_parent[parent["id"]] = record
        _array(record["cuts"], 127, 1)
        _array(record["members"], 128, 2)
        if len(record["members"]) != len(record["cuts"]) + 1:
            _fail("split_member_count", "One child is required for each contiguous partition.")
        _ids(record["evidence_ids"], minimum=1)
        for member in record["members"]:
            _exact(member, "observation_id observation_fingerprint")
            _plain(member["observation_id"], 160)
            _hash(member["observation_fingerprint"])
            if member["observation_id"] in owners:
                _fail("split_member_reused", "A child cannot belong to two retained parent partitions.")
            owners[member["observation_id"]] = record["id"]
    snapshots = copy.deepcopy(rows)
    input_snapshots = copy.deepcopy(dependencies)
    computed = {}

    def visit(record, ancestry):
        identifier = record["id"]
        if identifier in ancestry or len(ancestry) >= 16:
            _fail("split_cycle", "Split lineage must be acyclic and within the supported depth.")
        if identifier in computed:
            return computed[identifier]
        parent = record["parent"]
        arc_record = record.get("schema") == "duct-arc-split-1"
        leaf_ids, issues, child_records, descendants = [], list(parent["issues"]), [], []
        if arc_record and parent["geometry"]["kind"] == "circular_arc_span" and parent["id"] not in owners:
            issues.append("arc_span_lineage_missing")
        if record["parent_fingerprint"] != fingerprint(parent):
            issues.append("split_parent_changed")
        if parent["id"] in observations:
            leaf_ids.append(parent["id"])
            issues.append("split_parent_still_active")
        for member in record["members"]:
            child_id = member["observation_id"]
            active = observations.get(child_id)
            nested = by_parent.get(child_id)
            if active is not None and nested is not None:
                issues.append("split_parent_still_active")
            if nested is not None:
                descendant = visit(nested, ancestry + [identifier])
                descendants.append(descendant)
                leaf_ids.extend(descendant["diagnostic"]["leaf_ids"])
                issues.extend(descendant["diagnostic"]["issues"])
                child = nested["parent"]
            elif active is not None:
                leaf_ids.append(child_id)
                child = active
            else:
                issues.append("split_member_missing")
                child = None
            child_records.append(child)
            if child is not None and member["observation_fingerprint"] != fingerprint(child):
                issues.append("split_member_changed")
        leaf_ids = sorted(set(leaf_ids))
        geometry = geometries.get(parent["source"]["sheet_id"])
        required = set(parent["evidence_ids"]) | set(record["evidence_ids"])
        for cut in record["cuts"]:
            _exact(cut, "source_point evidence_ids" if arc_record else "edge_index fraction evidence_ids")
            required.update(_ids(cut["evidence_ids"], minimum=1))
        if any(key not in evidence for key in required):
            issues.append("split_evidence_missing")
        link_issues, link_dependency, allowed_foreign = reading_links.applicability(link_core, parent,
            relationships, geometries, evidence)
        issues.extend(link_issues)
        boundary_refs = set(record['evidence_ids']) | {eid for cut in record['cuts'] for eid in cut['evidence_ids']}
        if any(key in evidence and evidence[key]["source"] != parent["source"] and
               (key not in allowed_foreign or key in boundary_refs) for key in required):
            issues.append("split_evidence_source_mismatch")
        if (geometry is None or parent["source"]["geometry_fingerprint"] != geometry["fingerprint"] or
                any(parent["source"][key] != geometry[key] for key in ("revision_id", "index", "sheet_id"))):
            issues.append("split_source_changed")
        else:
            try:
                partition = prove_split_partition(parent, child_records, geometry, record["cuts"], arc=arc_record)
                for index, cut in enumerate(record["cuts"]):
                    for key in cut["evidence_ids"]:
                        retained = evidence.get(key)
                        if retained is None:
                            continue
                        if (retained["source"] != parent["source"] or
                                not split_boundary_inside(partition, index, retained["bbox"], geometry)):
                            issues.append("split_boundary_evidence_mismatch")
            except (DuctError, sheet_scale.ScaleError, sheet_scale.geometry_module.GeometryError) as exc:
                issues.append(exc.code)
        for key in leaf_ids:
            if key not in snapshots or snapshots[key]["status"] != "current":
                issues.append("split_member_unresolved")
            elif snapshots[key]["method"] != "measured_planar":
                issues.append("split_partition_changed")
        measurement, checks = None, []
        if not issues and leaf_ids:
            bindings = [snapshots[key]["components"]["measurement"]["scale_binding"] for key in leaf_ids]
            if any(binding != bindings[0] for binding in bindings[1:]):
                issues.append("split_scale_conflict")
            else:
                try:
                    # Exact partition identity permits eliminating only artificial
                    # cut vertices. Original bends and the full parent remain.
                    if arc_record:
                        measurement = _arc_measurement(parent, geometry, facts, decisions, evidence, bindings[0]["fact_id"])
                    else:
                        _require_graphic_support(parent, partition["parent_pdf_points"], geometry, evidence)
                        resolution = sheet_scale.resolve(facts, decisions, bindings[0]["fact_id"],
                            geometry, parent["geometry"]["points"])
                        measurement = sheet_scale.measure_path(parent["geometry"]["points"], geometry, resolution)
                    if measurement["scale_binding"] != bindings[0]:
                        issues.append("split_scale_conflict")
                    readings = {reading["id"]: reading for reading in parent["readings"]}
                    roles = ["dimension_check_ids"] + (["radius_check_ids"] if arc_record else [])
                    for role in roles:
                        for key in parent["geometry"][role]:
                            stated = _stored(_read_meters(readings[key], "length"))
                            measured = (measurement["radius_measurement"]["meters"] if role == "radius_check_ids"
                                        else measurement["meters"])
                            check = {"reading_id": key, "stated_meters": stated, "measured_meters": measured}
                            if arc_record:
                                check["role"] = "centerline_radius" if role == "radius_check_ids" else "whole_length"
                            checks.append(check)
                            if stated != measured:
                                issues.append("radius_conflict" if role == "radius_check_ids" else "dimension_conflict")
                except (DuctError, sheet_scale.ScaleError, sheet_scale.geometry_module.GeometryError) as exc:
                    issues.append(exc.code)
        diagnostic = {"constraint_id": identifier, "leaf_ids": leaf_ids, "measurement": measurement,
            "checks": checks, "issues": sorted(set(issues))}
        binding = {"record": record, "evidence": {key: evidence.get(key) for key in sorted(required)},
            "geometry": geometry["fingerprint"] if geometry else None,
            "members": {key: {"inputs": input_snapshots.get(key), "calculation": snapshots.get(key)} for key in leaf_ids},
            "descendants": [item["diagnostic"]["fingerprint"] for item in descendants], "diagnostic": diagnostic}
        if link_dependency is not None:
            binding['reading_relationships'] = link_dependency
        diagnostic["fingerprint"] = fingerprint(binding)
        computed[identifier] = {"diagnostic": diagnostic}
        return computed[identifier]

    for record in records:
        visit(record, [])
    coverage_issues = []
    for identifier in sorted(computed):
        diagnostic = computed[identifier]["diagnostic"]
        affected = set(diagnostic["leaf_ids"])
        # A failed split also affects verified duplicate links touching its leaves.
        # Preserve the same correspondence dependency closure used by replacements.
        for row in snapshots.values():
            if affected.intersection(row["observation_ids"]):
                affected.update(row["observation_ids"])
        if not affected and diagnostic["issues"]:
            coverage_issues.extend(diagnostic["issues"])
        for key in sorted(affected):
            if key not in rows:
                continue
            row = rows[key]
            row["components"].setdefault("planar_splits", []).append(copy.deepcopy(diagnostic))
            dependencies[key].setdefault("planar_splits", []).append(diagnostic["fingerprint"])
            if diagnostic["issues"]:
                row.update(status="unresolved", meters=None, feet=None)
                row["issues"].extend(diagnostic["issues"])
    return coverage_issues, [computed[key]["diagnostic"]["fingerprint"] for key in sorted(computed)]


def _arc_lineage_members(records, observations, geometries):
    """Preflight exact v3 ancestry before a correspondence can consume a row."""
    by_parent, owners, duplicates, cache = {}, {}, set(), {}
    for record in records:
        if not isinstance(record, dict) or record.get("schema") != "duct-arc-split-1":
            continue
        try:
            parent_id = record["parent"]["id"]
            if parent_id in by_parent:
                duplicates.add(parent_id)
            by_parent[parent_id] = record
            for member in _array(record["members"], 128, 2):
                child_id = member["observation_id"]
                if child_id in owners:
                    duplicates.add(child_id)
                owners[child_id] = record
        except (ValueError, KeyError, TypeError):
            continue
    def valid(record, ancestry):
        key = record["parent"]["id"]
        if key in ancestry or len(ancestry) >= 16 or key in duplicates:
            return False
        if key in cache:
            return cache[key]
        try:
            parent = validate_observation(record["parent"])
            if record["parent_fingerprint"] != fingerprint(parent) or key in observations:
                return False
            if parent["schema"] == "duct-observation-3":
                owner = owners.get(key)
                if owner is None or not valid(owner, ancestry + [key]):
                    return False
            elif parent["schema"] != "duct-observation-2":
                return False
            children = []
            for member in record["members"]:
                child_id = member["observation_id"]
                if child_id in duplicates:
                    return False
                child = observations.get(child_id, by_parent.get(child_id, {}).get("parent"))
                if child is None or member["observation_fingerprint"] != fingerprint(child):
                    return False
                children.append(child)
            prove_split_partition(parent, children, geometries[parent["source"]["sheet_id"]], record["cuts"], arc=True)
        except (ValueError, KeyError, TypeError, AttributeError):
            cache[key] = False
            return False
        cache[key] = True
        return True
    return {key for key, owner in owners.items() if key not in duplicates and valid(owner, [])}


def calculate(observations, admissions, correspondences, coverage, current_sources,
              scale_facts, scale_decisions, rule_binding, planar_splits=None, reading_relationships=None, arc_obligations=None):
    """Calculate provisional quantities from trusted workflow context, purely."""
    fingerprint([observations, admissions, correspondences, coverage, current_sources,
        scale_facts, scale_decisions, rule_binding, planar_splits, reading_relationships, arc_obligations])
    link_core = SimpleNamespace(**{name: globals()[name] for name in
        ('_array', '_exact', '_enum', '_plain', '_hash', '_source', '_ids', '_fail', 'fingerprint')})
    reading_relationships = reading_links.validate_records(link_core, reading_relationships)
    planar_splits = [] if planar_splits is None else _array(planar_splits, 1000)
    _array(observations)
    _array(scale_facts, 10000)
    _array(scale_decisions, 20000)
    _exact(rule_binding, "id fingerprint state")
    _plain(rule_binding["id"], 160)
    _hash(rule_binding["fingerprint"])
    _enum(rule_binding["state"], {"approved"})
    _exact(coverage, "state observation_fingerprints source_fingerprints evidence_ids issues")
    _enum(coverage["state"], {"complete", "partial", "unknown"})
    _identity_map(coverage["observation_fingerprints"])
    _identity_map(coverage["source_fingerprints"])
    _ids(coverage["evidence_ids"])
    _ids(coverage["issues"])
    observations = [validate_observation(row) for row in observations]
    by_id = {row["id"]: row for row in observations}
    if len(by_id) != len(observations):
        _fail("duplicate_identity", "Observation IDs must be unique.")
    geometries, evidence = _current_context(current_sources)
    accepted = _admissions(admissions)
    arc_members = _arc_lineage_members(planar_splits, by_id, geometries)
    rows, dependencies, prerequisites = {}, {}, {}
    for observation in observations:
        key = observation["id"]
        rows[key], dependencies[key], prerequisites[key] = _prepare(observation, accepted,
            geometries, evidence, scale_facts, scale_decisions, rule_binding, reading_relationships, link_core,
            lineage_valid=observation["schema"] != "duct-observation-3" or key in arc_members)
    _correspond(correspondences, by_id, rows, dependencies, prerequisites, evidence)
    split_issues, split_bindings = _split_constraints(planar_splits, by_id, rows, dependencies,
        geometries, evidence, scale_facts, scale_decisions, reading_relationships, link_core)
    check_issues, check_bindings = arc_checks.apply(SimpleNamespace(**globals()), arc_obligations, by_id,
        planar_splits, rows, dependencies, geometries, evidence, scale_facts, scale_decisions,
        rule_binding, reading_relationships, link_core)
    for key, row in rows.items():
        row["issues"] = sorted(set(row["issues"]))
        row["dependency_fingerprint"] = fingerprint({"inputs": dependencies[key],
            "result": {k: v for k, v in row.items() if k != "dependency_fingerprint"}})
    coverage_issues = list(coverage["issues"]) + split_issues + check_issues
    if coverage["state"] != "complete":
        coverage_issues.append("coverage_incomplete")
    if not observations:
        coverage_issues.append("coverage_empty")
    if coverage["observation_fingerprints"] != {k: fingerprint(v) for k, v in by_id.items()}:
        coverage_issues.append("coverage_observations_changed")
    if coverage["source_fingerprints"] != {k: v["fingerprint"] for k, v in geometries.items()}:
        coverage_issues.append("coverage_sources_changed")
    if not coverage["evidence_ids"] or any(k not in evidence for k in coverage["evidence_ids"]):
        coverage_issues.append("coverage_evidence_missing")
    coverage_issues = sorted(set(coverage_issues))
    row_list = [rows[v["id"]] for v in observations]
    groups, subtotal, total, complete = _summarize(row_list, coverage_issues)
    coverage_binding = {"record": coverage, "issues": coverage_issues,
        "evidence": {k: evidence.get(k) for k in coverage["evidence_ids"]}}
    if split_bindings:
        coverage_binding["planar_splits"] = split_bindings
    if check_bindings:
        coverage_binding["arc_obligations"] = check_bindings
    result = {"schema": "duct-calculation-1", "rows": row_list, "groups": groups,
        "known_subtotal_ft": subtotal, "total_ft": total, "complete": complete,
        "coverage_issues": coverage_issues,
        "dependencies": {"rows": {k: v["dependency_fingerprint"] for k, v in rows.items()},
            "coverage": fingerprint(coverage_binding)}}
    result["fingerprint"] = fingerprint(result)
    return copy.deepcopy(result)


def result_view(result, current_dependencies):
    """Expose historical quantities only while their precise dependencies match."""
    _exact(current_dependencies, "rows coverage")
    _identity_map(current_dependencies["rows"])
    _hash(current_dependencies["coverage"])
    _exact(result, "schema rows groups known_subtotal_ft total_ft complete coverage_issues dependencies fingerprint")
    if result["fingerprint"] != fingerprint({k: v for k, v in result.items() if k != "fingerprint"}):
        _fail("result_changed", "Saved calculation differs from its retained fingerprint.")
    viewed = copy.deepcopy(result)
    for row in viewed["rows"]:
        row["stored_meters"] = row["meters"]
        if current_dependencies["rows"].get(row["id"]) != row["dependency_fingerprint"]:
            row.update(status="stale", meters=None, feet=None)
            row["issues"] = sorted(set(row["issues"] + ["calculation_dependencies_changed"]))
    if current_dependencies["coverage"] != result["dependencies"]["coverage"]:
        viewed["coverage_issues"] = sorted(set(viewed["coverage_issues"] + ["coverage_changed"]))
    groups, subtotal, total, complete = _summarize(viewed["rows"], viewed["coverage_issues"])
    viewed.update(groups=groups, known_subtotal_ft=subtotal, total_ft=total, complete=complete,
                  stored_fingerprint=result["fingerprint"])
    viewed["fingerprint"] = fingerprint({k: v for k, v in viewed.items() if k != "fingerprint"})
    return viewed


def replace_rows(saved_result, newly_calculated, observation_ids):
    """Append-ready mixed snapshot; recalculate only the explicit affected closure.

    Unselected row bytes and dependencies remain historical. Group summaries use
    current dependencies, so this operation cannot make an unrelated stale row
    usable just because a different row was corrected.
    """
    _ids(observation_ids, 2000)
    selected = set(observation_ids)
    # These calls verify retained result fingerprints before any composition.
    result_view(saved_result, saved_result.get("dependencies"))
    result_view(newly_calculated, newly_calculated.get("dependencies"))
    previous = {row["id"]: row for row in saved_result["rows"]}
    current = {row["id"]: row for row in newly_calculated["rows"]}
    if not selected <= set(previous) | set(current):
        _fail("replacement_missing", "Choose existing or newly calculated observation IDs.")
    if not (set(previous) ^ set(current)) <= selected:
        _fail("replacement_scope_incomplete", "Added and removed observations belong in the replacement scope.")
    for row in list(previous.values()) + list(current.values()):
        members = set(row["observation_ids"])
        for constraint in row["components"].get("planar_splits", []):
            members.update(constraint["leaf_ids"])
        if selected & members and not members <= selected:
            _fail("replacement_scope_incomplete", "Replace the complete physical correspondence group together.")
    rows = []
    for row in saved_result["rows"]:
        key = row["id"]
        if key not in selected:
            rows.append(copy.deepcopy(row))
        elif key in current:
            rows.append(copy.deepcopy(current[key]))
    rows.extend(copy.deepcopy(row) for row in newly_calculated["rows"] if row["id"] not in previous)
    result = copy.deepcopy(newly_calculated)
    result["rows"] = rows
    result["dependencies"]["rows"] = {row["id"]: row["dependency_fingerprint"] for row in rows}
    result["fingerprint"] = fingerprint({k: v for k, v in result.items() if k != "fingerprint"})
    effective = result_view(result, newly_calculated["dependencies"])
    for key in ("groups", "known_subtotal_ft", "total_ft", "complete", "coverage_issues"):
        result[key] = effective[key]
    result["fingerprint"] = fingerprint({k: v for k, v in result.items() if k != "fingerprint"})
    return result
