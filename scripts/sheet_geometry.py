"""Revision-bound Foundation coordinates and deterministic paper lengths.

The stored Foundation matrix maps PDF micropoints into displayed, top-left
micropoints. Crop translation and rotation are already in that matrix. This
module applies/inverts it; it never rotates the displayed dimensions again.
"""
import hashlib
import json
import re
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext


VERSION = "sheet-geometry-1"
COORDINATE_SPACE = "display_page_normalized_top_left"
_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)
_MICROPOINTS = Decimal(1000000)
_MAX_INTEGER = 9007199254740991
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MATRICES = {0: (1, 0, 0, -1), 90: (0, 1, 1, 0),
             180: (-1, 0, 0, 1), 270: (0, -1, -1, 0)}
_MATRIX_FIELDS = ("m11", "m12", "m21", "m22")
_TRANSFORM_FIELDS = _MATRIX_FIELDS + ("tx_micropoints", "ty_micropoints")


class GeometryError(ValueError):
    """Source geometry or a coordinate cannot be used for measurement."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _integer(value, name, lower=-_MAX_INTEGER, upper=_MAX_INTEGER):
    if type(value) is not int or not lower <= value <= upper:
        raise GeometryError("geometry_invalid", "%s must be a bounded integer." % name)
    return value


def _digest(value, name):
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise GeometryError("geometry_invalid", "%s must be a SHA-256 identity." % name)
    return value


def sheet_geometry(sheet, revision_id):
    """Build a canonical identity from full Foundation metadata, without guesses.

    Extra sheet metadata (including an existing fingerprint) is ignored. A
    geometry identity may therefore be rebuilt and compared to its stored hash.
    """
    if not isinstance(sheet, dict):
        raise GeometryError("geometry_invalid", "Full Foundation sheet metadata is required.")
    revision_id = _digest(revision_id, "revision_id")
    if sheet.get("revision_id", revision_id) != revision_id:
        raise GeometryError("geometry_invalid", "Sheet revision disagrees with its source.")
    if sheet.get("parent_content_sha256") != revision_id:
        raise GeometryError("geometry_invalid", "Sheet parent must match the PDF revision.")
    if sheet.get("unit") != "pt":
        raise GeometryError("geometry_invalid", "Foundation paper units must be pt.")
    rotation = _integer(sheet.get("rotation_degrees"), "rotation_degrees", 0, 270)
    transform = sheet.get("transform")
    if not isinstance(transform, dict) or set(transform) != set(_TRANSFORM_FIELDS):
        raise GeometryError("geometry_invalid", "The complete Foundation transform is required.")
    transform = {key: _integer(transform[key], key) for key in _TRANSFORM_FIELDS}
    if tuple(transform[key] for key in _MATRIX_FIELDS) != _MATRICES.get(rotation):
        raise GeometryError("geometry_invalid", "Transform does not match the Foundation rotation.")
    identity = {
        "revision_id": revision_id,
        "sheet_id": _digest(sheet.get("sheet_id"), "sheet_id"),
        "index": _integer(sheet.get("index"), "index", 0, 4294967295),
        "width_micropoints": _integer(sheet.get("width_micropoints"), "width_micropoints", 1),
        "height_micropoints": _integer(sheet.get("height_micropoints"), "height_micropoints", 1),
        "unit": "pt", "rotation_degrees": rotation, "transform": transform,
        "parent_content_sha256": revision_id, "coordinate_space": COORDINATE_SPACE,
    }
    packed = json.dumps({"contract": VERSION, "geometry": identity}, sort_keys=True,
                        separators=(",", ":"), allow_nan=False).encode("utf-8")
    identity["fingerprint"] = hashlib.sha256(packed).hexdigest()
    return identity


def _geometry(value):
    if not isinstance(value, dict):
        raise GeometryError("geometry_invalid", "A pinned geometry identity is required.")
    current = sheet_geometry(value, value.get("revision_id"))
    if (value.get("fingerprint") != current["fingerprint"]
            or value.get("coordinate_space") != COORDINATE_SPACE):
        raise GeometryError("geometry_stale", "Sheet coordinate identity has changed.")
    return current


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise GeometryError("point_invalid", "%s must be a finite number." % name)
    if isinstance(value, int) and abs(value) > _MAX_INTEGER:
        raise GeometryError("point_invalid", "%s exceeds coordinate limits." % name)
    text = str(value)
    if len(text) > 128:
        raise GeometryError("point_invalid", "%s exceeds numeric precision limits." % name)
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise GeometryError("point_invalid", "%s must be a finite number." % name)
    if not number.is_finite() or abs(number.as_tuple().exponent) > 1000:
        raise GeometryError("point_invalid", "%s must be a bounded finite number." % name)
    return number


def _display_point(point):
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise GeometryError("point_invalid", "A point must contain exactly x and y.")
    result = [_number(value, "Displayed coordinate") for value in point]
    if not all(0 <= value <= 1 for value in result):
        raise GeometryError("outside_crop", "Displayed point lies outside the effective crop.")
    return result


def _inverse(point, geometry):
    x, y = _display_point(point)
    transform = geometry["transform"]
    with localcontext(_CONTEXT):
        x = x * geometry["width_micropoints"] - transform["tx_micropoints"]
        y = y * geometry["height_micropoints"] - transform["ty_micropoints"]
        a, b, c, d = (transform[key] for key in _MATRIX_FIELDS)
        determinant = a * d - b * c
        # Foundation's four matrices are orthogonal, with determinant -1.
        return [int(((d * x - b * y) / determinant).to_integral_value()),
                int(((a * y - c * x) / determinant).to_integral_value())]


def display_to_pdf(point, geometry):
    """Convert normalized displayed coordinates to nearest HALF_EVEN micropoints."""
    return _inverse(point, _geometry(geometry))


def pdf_to_display(point, geometry):
    """Apply the original matrix and return full float precision normalized points."""
    geometry = _geometry(geometry)
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise GeometryError("point_invalid", "PDF point must contain exactly x and y.")
    if any(type(value) is not int or abs(value) > 2 * _MAX_INTEGER for value in point):
        raise GeometryError("point_invalid", "PDF coordinates must be bounded integer micropoints.")
    transform = geometry["transform"]
    x = transform["m11"] * point[0] + transform["m12"] * point[1] + transform["tx_micropoints"]
    y = transform["m21"] * point[0] + transform["m22"] * point[1] + transform["ty_micropoints"]
    if not 0 <= x <= geometry["width_micropoints"] or not 0 <= y <= geometry["height_micropoints"]:
        raise GeometryError("outside_crop", "PDF point lies outside the effective crop.")
    with localcontext(_CONTEXT):
        return [float(Decimal(x) / geometry["width_micropoints"]),
                float(Decimal(y) / geometry["height_micropoints"])]


def paper_length(path, geometry):
    """Measure a polyline in paper points from its frozen PDF micropoints.

    Quantizing endpoints once aligns this length with the source coordinates
    retained by takeoff measurements. Precision and rounding ignore ambient
    Decimal settings; no binary-float distance calculation is used.
    """
    geometry = _geometry(geometry)
    if not isinstance(path, (list, tuple)) or not 2 <= len(path) <= 128:
        raise GeometryError("path_invalid", "A measurement path needs 2 through 128 points.")
    points = [_inverse(point, geometry) for point in path]
    with localcontext(_CONTEXT):
        length = Decimal(0)
        for start, end in zip(points, points[1:]):
            dx, dy = Decimal(end[0] - start[0]), Decimal(end[1] - start[1])
            length += (dx * dx + dy * dy).sqrt()
        if length == 0:
            raise GeometryError("path_invalid", "Select two different drawing points for a nonzero length.")
        return length / _MICROPOINTS


def check_layout(page, geometry):
    """Require positioned text to use the same effective displayed page extent."""
    geometry = _geometry(geometry)
    if not isinstance(page, dict):
        raise GeometryError("layout_mismatch", "Positioned page dimensions are required.")
    with localcontext(_CONTEXT):
        for axis in ("width", "height"):
            try:
                observed = _number(page.get(axis), "Layout " + axis)
            except GeometryError:
                raise GeometryError("layout_mismatch", "Positioned page dimensions are invalid.")
            expected = Decimal(geometry[axis + "_micropoints"]) / _MICROPOINTS
            if observed <= 0 or abs(observed - expected) > Decimal("0.01"):
                raise GeometryError("layout_mismatch", "Positioned page differs from the Foundation crop.")
