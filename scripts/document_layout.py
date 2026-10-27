"""Positioned PDF text shared by mechanical readers (Python 3.9+, stdlib).

Preserves lines and individual word locations; no drawing semantics are inferred.
"""
import importlib.util
import math
from pathlib import Path
import xml.etree.ElementTree as ET

MAX_BYTES = 16 * 1024 * 1024
MAX_WORDS = 100000

_GEOMETRY_SPEC = importlib.util.spec_from_file_location(
    "heleos_layout_geometry", Path(__file__).with_name("sheet_geometry.py"))
geometry_tools = importlib.util.module_from_spec(_GEOMETRY_SPEC)
_GEOMETRY_SPEC.loader.exec_module(geometry_tools)


def union(boxes):
    return [min(v[0] for v in boxes), min(v[1] for v in boxes),
            max(v[2] for v in boxes), max(v[3] for v in boxes)]


def poppler_dimensions(raw_width, raw_height, geometry):
    """Validate Poppler's unrotated crop header, then use displayed dimensions.

    With -bbox-layout -cropbox, Poppler word coordinates already include page
    rotation, while its page width/height header describes the unrotated crop.
    The complete pinned Foundation geometry disambiguates these two extents.
    """
    try:
        if isinstance(raw_width, bool) or isinstance(raw_height, bool):
            raise ValueError()
        width, height = float(raw_width), float(raw_height)
        if not all(math.isfinite(value) and value > 0 for value in (width, height)):
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        raise geometry_tools.GeometryError("layout_mismatch", "Poppler page dimensions are invalid.") from None
    if not isinstance(geometry, dict):
        raise geometry_tools.GeometryError("geometry_invalid", "Poppler reading requires full Foundation geometry.")
    if geometry.get("rotation_degrees") in (90, 270):
        width, height = height, width
    geometry_tools.check_layout({"width": width, "height": height}, geometry)
    return (geometry["width_micropoints"] / 1000000,
            geometry["height_micropoints"] / 1000000)


def parse_layout(payload, source, geometry=None):
    if len(payload) > MAX_BYTES or b"<!ENTITY" in payload.upper():
        raise ValueError("Unsupported or oversized PDF text output")
    try:
        root = ET.fromstring(payload)
        pages = [v for v in root.iter() if v.tag.rsplit("}", 1)[-1] == "page"]
        if len(pages) != 1:
            raise ValueError("Expected one page")
        page = pages[0]
        width, height = float(page.attrib["width"]), float(page.attrib["height"])
        if not all(math.isfinite(v) and v > 0 for v in (width, height)):
            raise ValueError("Invalid page dimensions")
        extra = {}
        if geometry is not None:
            raw_dimensions = {"width": width, "height": height}
            width, height = poppler_dimensions(width, height, geometry)
            if any(source.get(key) != geometry.get(key) for key in ("revision_id", "sheet_id", "index")):
                raise geometry_tools.GeometryError("geometry_invalid", "Page source differs from its Foundation geometry.")
            extra = {"raw_page_dimensions": raw_dimensions,
                     "geometry": geometry_tools.sheet_geometry(geometry, geometry["revision_id"]),
                     "coordinate_space": geometry_tools.COORDINATE_SPACE}
        lines, count = [], 0
        for line in page.iter():
            if line.tag.rsplit("}", 1)[-1] != "line":
                continue
            words = []
            for word in line:
                if word.tag.rsplit("}", 1)[-1] != "word":
                    continue
                value = " ".join("".join(word.itertext()).split())
                if not value:
                    continue
                if len(value) > 2000 or any(ord(v) < 32 for v in value):
                    raise ValueError("Unsupported word")
                coords = [float(word.attrib[k]) for k in ("xMin", "yMin", "xMax", "yMax")]
                if (not all(math.isfinite(v) for v in coords) or coords[0] < -1 or coords[1] < -1
                        or coords[2] > width + 1 or coords[3] > height + 1):
                    raise ValueError("Word outside page")
                bounds = [max(0, coords[0]) / width, max(0, coords[1]) / height,
                          min(width, coords[2]) / width, min(height, coords[3]) / height]
                if not (0 <= bounds[0] < bounds[2] <= 1 and 0 <= bounds[1] < bounds[3] <= 1):
                    raise ValueError("Invalid word location")
                words.append({"text": value, "bbox": bounds})
                count += 1
                if count > MAX_WORDS:
                    raise ValueError("Page word limit exceeded")
            if words:
                lines.append({"text": " ".join(v["text"] for v in words),
                              "bbox": union([v["bbox"] for v in words]), "words": words})
        result = dict(source, width=width, height=height, lines=lines, word_count=count)
        result.update(extra)
        return result
    except (ET.ParseError, KeyError, TypeError, OverflowError) as error:
        raise ValueError("Invalid positioned PDF text") from error
