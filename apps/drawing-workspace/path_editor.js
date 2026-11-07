"use strict";

// Drawing edits are proposals only. This module never measures physical lengths.
// Interaction example: DUCT-CORR-RESEARCH-01 (explicit vertices and completion).
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.HeleosPathEditor = api;
})(typeof globalThis === "object" ? globalThis : this, function () {
  const MAX_POINTS = 128;
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const validPoint = (point) => Array.isArray(point) && point.length === 2 &&
    point.every((value) => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1);
  const equalPoint = (a, b) => a[0] === b[0] && a[1] === b[1];
  const validSource = (source) => source && typeof source.revision_id === "string" && source.revision_id.length > 0 &&
    Number.isSafeInteger(source.index) && source.index >= 0 && typeof source.sheet_id === "string" &&
    source.sheet_id.length > 0 && typeof source.geometry_fingerprint === "string" && source.geometry_fingerprint.length > 0;
  const sameSource = (a, b) => validSource(a) && validSource(b) &&
    ["revision_id", "index", "sheet_id", "geometry_fingerprint"].every((key) => a[key] === b[key]);

  function createState(request) {
    if (!request || typeof request.request_id !== "string" || !request.request_id || !validSource(request.source) ||
        !["polyline", "straight", "split", "circular_arc", "arc_split"].includes(request.mode) || !Array.isArray(request.points) ||
        request.points.length > MAX_POINTS || !request.points.every(validPoint) ||
        (request.mode === "straight" && request.points.length > 2) ||
        (request.mode === "circular_arc" && request.points.length > 3) ||
        (request.mode === "split" && request.points.length < 2) ||
        (request.mode === "arc_split" && !arcGeometryPath(request.geometry, request.page_geometry, request.source))) throw new Error("The path editing request is invalid.");
    return { request_id: request.request_id, source: clone(request.source), mode: request.mode,
      geometry: request.geometry ? clone(request.geometry) : null,
      points: clone(request.points), page_geometry: request.page_geometry ? clone(request.page_geometry) : null,
      selected_vertex: null, selected_edge: null, insert_pending: false,
      split: null, state: "editing", reason: null, history: [], drag: null };
  }

  function remember(state) {
    state.history.push({ points: clone(state.points), selected_vertex: state.selected_vertex,
      selected_edge: state.selected_edge, split: clone(state.split) });
    if (state.history.length > MAX_POINTS) state.history.shift();
  }

  function canFinish(state) {
    if (["split", "arc_split"].includes(state.mode)) return Boolean(state.split);
    if (state.mode === "circular_arc") return Boolean(arcPath(state.points, state.page_geometry, state.source));
    return state.points.length >= 2 && (state.mode !== "straight" || state.points.length === 2) &&
      state.points.every((point, index) => index === 0 || !equalPoint(point, state.points[index - 1]));
  }

  function transition(previous, action) {
    const state = clone(previous);
    if (!action || state.state !== "editing") return state;
    const editable = !["split", "arc_split"].includes(state.mode);
    if (action.type === "cancel") {
      state.state = "cancelled"; state.reason = action.reason || "cancelled"; state.drag = null;
      return state;
    }
    if (action.type === "cancel_drag") {
      if (state.drag) state.points = state.drag.points;
      state.drag = null;
      return state;
    }
    if (action.type === "begin_drag" && editable && Number.isInteger(action.index) && state.points[action.index]) {
      state.selected_vertex = action.index; state.selected_edge = null; state.insert_pending = false;
      state.drag = { index: action.index, pointer_id: action.pointer_id, points: clone(state.points) };
    } else if (["move_drag", "end_drag"].includes(action.type) && state.drag &&
        action.pointer_id === state.drag.pointer_id && validPoint(action.point)) {
      state.points[state.drag.index] = clone(action.point);
      if (action.type === "end_drag") {
        const points = clone(state.points);
        state.points = state.drag.points;
        if (JSON.stringify(points) !== JSON.stringify(state.points)) remember(state);
        state.points = points; state.drag = null;
      }
    } else if (state.drag) {
      return state; // A gesture is one undo step; other actions cannot interleave it.
    } else if (action.type === "select_vertex" && editable && Number.isInteger(action.index) && state.points[action.index]) {
      state.selected_vertex = action.index; state.selected_edge = null; state.insert_pending = false;
    } else if (action.type === "select_edge" && editable && Number.isInteger(action.index) &&
        action.index >= 0 && action.index + 1 < state.points.length) {
      state.selected_edge = action.index; state.selected_vertex = null; state.insert_pending = false;
    } else if (action.type === "insert" && state.mode === "polyline" && state.selected_edge !== null && state.points.length < MAX_POINTS) {
      state.insert_pending = true;
    } else if (action.type === "place" && editable && validPoint(action.point)) {
      const index = state.insert_pending ? state.selected_edge + 1 : state.points.length;
      if (state.points.length >= (state.mode === "straight" ? 2 : state.mode === "circular_arc" ? 3 : MAX_POINTS) ||
          (state.points[index - 1] && equalPoint(state.points[index - 1], action.point)) ||
          (state.points[index] && equalPoint(state.points[index], action.point))) return state;
      remember(state); state.points.splice(index, 0, clone(action.point));
      state.selected_vertex = index; state.selected_edge = null; state.insert_pending = false;
    } else if (action.type === "delete" && state.mode === "polyline" && state.points.length > 2) {
      const index = state.selected_vertex === null ? state.points.length - 1 : state.selected_vertex;
      remember(state); state.points.splice(index, 1);
      state.selected_vertex = Math.min(index, state.points.length - 1); state.selected_edge = null; state.insert_pending = false;
    } else if (action.type === "cut" && state.mode === "split" && Number.isInteger(action.edge_index) &&
        action.edge_index >= 0 && action.edge_index + 1 < state.points.length &&
        typeof action.fraction === "string" && /^(?:0\.\d+|1(?:\.0+)?)$/.test(action.fraction)) {
      const fraction = Number(action.fraction);
      if (!(fraction > 0 && fraction <= 1) || (action.edge_index === state.points.length - 2 && fraction === 1)) return state;
      remember(state); state.split = { edge_index: action.edge_index, fraction: action.fraction };
      state.selected_edge = action.edge_index;
    } else if (action.type === "arc_cut" && state.mode === "arc_split" &&
        arcCut(state.geometry, action.source_point, state.page_geometry, state.source)) {
      remember(state); state.split = { source_point: clone(action.source_point) };
    } else if (action.type === "undo" && state.history.length) {
      Object.assign(state, state.history.pop()); state.insert_pending = false;
    } else if (action.type === "finish" && canFinish(state)) {
      state.state = "finished";
    }
    return state;
  }

  function pointFromClient(event, bounds) {
    if (!bounds || !Number.isFinite(bounds.left) || !Number.isFinite(bounds.top) ||
        !Number.isFinite(bounds.width) || !Number.isFinite(bounds.height) || bounds.width <= 0 || bounds.height <= 0 ||
        !Number.isFinite(event.clientX) || !Number.isFinite(event.clientY)) return null;
    return [Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
      Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height))];
  }

  function projectEdge(points, point, width, height) {
    if (!validPoint(point) || !Number.isFinite(width) || !Number.isFinite(height) || !(width > 0 && height > 0)) return null;
    let nearest = null;
    for (let index = 0; index + 1 < points.length; index += 1) {
      const a = points[index], b = points[index + 1];
      const dx = (b[0] - a[0]) * width, dy = (b[1] - a[1]) * height;
      const square = dx * dx + dy * dy;
      if (square === 0) continue;
      const fraction = Math.max(0, Math.min(1, ((point[0] - a[0]) * width * dx + (point[1] - a[1]) * height * dy) / square));
      const projected = [a[0] + fraction * (b[0] - a[0]), a[1] + fraction * (b[1] - a[1])];
      const distance = Math.hypot((point[0] - projected[0]) * width, (point[1] - projected[1]) * height);
      if (!nearest || distance < nearest.distance) nearest = { edge_index: index, fraction, point: projected, distance };
    }
    return nearest;
  }

  function hitTest(points, point, width, height) {
    if (!validPoint(point)) return null;
    let vertex = null;
    points.forEach((entry, index) => {
      const distance = Math.hypot((point[0] - entry[0]) * width, (point[1] - entry[1]) * height);
      if (distance <= 10 && (!vertex || distance < vertex.distance)) vertex = { kind: "vertex", index, distance };
    });
    if (vertex) return vertex;
    const edge = projectEdge(points, point, width, height);
    return edge && edge.distance <= 10 ? { kind: "edge", index: edge.edge_index } : null;
  }

  function cutAt(points, point, width, height) {
    const edge = projectEdge(points, point, width, height);
    if (!edge || edge.distance > 10) return null;
    let { edge_index: index, fraction } = edge;
    // An existing interior vertex always has one canonical representation.
    if (fraction === 0 && index > 0) { index -= 1; fraction = 1; }
    if (fraction <= 0 || (fraction === 1 && index === points.length - 2)) return null;
    const text = fraction === 1 ? "1" : fraction.toFixed(15).replace(/0+$/, "");
    if (!(Number(text) > 0 && Number(text) < 1) && text !== "1") return null;
    return { edge_index: index, fraction: text };
  }

  function reply(state) {
    const value = { request_id: state.request_id, source: clone(state.source), points: clone(state.points),
      selected_vertex: state.selected_vertex, state: state.state };
    if (state.split) value.split = clone(state.split);
    if (state.reason) value.reason = state.reason;
    return value;
  }

  // Bounded display copy of sheet_geometry's decimal input and Context(34).
  // Only the source geometry module admits coordinates into stored authority.
  const power10 = places => 10n ** BigInt(places);
  // Decimal digit sets verified against local Python Unicode16/Decimal. This
  // translates digits only; punctuation, signs and decimal grammar stay intact.
  const decimalZeros = [
    48, 1632, 1776, 1984, 2406, 2534, 2662, 2790, 2918, 3046, 3174, 3302,
    3430, 3558, 3664, 3792, 3872, 4160, 4240, 6112, 6160, 6470, 6608, 6784,
    6800, 6992, 7088, 7232, 7248, 42528, 43216, 43264, 43472, 43504, 43600, 44016,
    65296, 66720, 68912, 68928, 69734, 69872, 69942, 70096, 70384, 70736, 70864, 71248,
    71360, 71376, 71386, 71472, 71904, 72016, 72688, 72784, 73040, 73120, 73552, 90416,
    92768, 92864, 93008, 93552, 118000, 120782, 120792, 120802, 120812, 120822, 123200, 123632,
    124144, 124401, 125264, 130032
  ];
  function sourceDigit(character) {
    const code = character.codePointAt(0);
    if (code >= 48 && code <= 57) return character;
    let lower = 0, upper = decimalZeros.length;
    while (lower < upper) {
      const middle = (lower + upper) >>> 1;
      if (decimalZeros[middle] <= code) lower = middle + 1; else upper = middle;
    }
    const digit = code - decimalZeros[lower - 1];
    return digit >= 0 && digit <= 9 ? String(digit) : character;
  }
  function sourceDecimal(value) {
    if (!["number", "string"].includes(typeof value)) return null;
    const original = String(value);
    // Python len counts scalars. Bound UTF16 allocation first, then count the
    // actual source scalars so supplementary-plane digits retain the same limit.
    if (original.length > 256) return null;
    const scalars = Array.from(original);
    if (scalars.length > 128) return null;
    const text = scalars.map(sourceDigit).join("").trim().replaceAll("_", "");
    if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(text)) return null;
    const [mantissa, scientific = "0"] = text.toLowerCase().split("e"), parts = mantissa.split(".");
    const exponent = Number(scientific) - (parts[1] || "").length;
    if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > 1000) return null;
    const coefficient = BigInt(parts.join(""));
    if (coefficient < 0n || (exponent >= 0 ? coefficient * power10(exponent) > 1n : coefficient > power10(-exponent))) return null;
    return { coefficient, exponent };
  }
  function roundQuotient(numerator, denominator) {
    const sign = numerator < 0n ? -1n : 1n, magnitude = numerator * sign;
    const quotient = magnitude / denominator, remainder = magnitude % denominator;
    return sign * (quotient + (2n * remainder > denominator || (2n * remainder === denominator && quotient % 2n !== 0n) ? 1n : 0n));
  }
  function sourcePrecision(decimal) {
    const digits = (decimal.coefficient < 0n ? -decimal.coefficient : decimal.coefficient).toString().length;
    if (digits <= 34) return decimal;
    const discarded = digits - 34;
    return { coefficient: roundQuotient(decimal.coefficient, power10(discarded)), exponent: decimal.exponent + discarded };
  }
  function sourceInteger(decimal, sign = 1n) {
    const coefficient = decimal.coefficient * sign;
    return decimal.exponent >= 0 ? coefficient * power10(decimal.exponent) : roundQuotient(coefficient, power10(-decimal.exponent));
  }
  // Approximate SVG presentation only: quantities use the certified Python
  // method. Convert through PDF coordinates before rounding, then forward into
  // physical display micropoints. Crop translations change HALF_EVEN tie parity.
  function arcModel(points, page, source) {
    if (!Array.isArray(points) || points.length !== 3 || !points.every(point => Array.isArray(point) && point.length === 2 && point.every(value => sourceDecimal(value))) || !page ||
        !sameSource(source, { ...page, geometry_fingerprint: page.fingerprint }) || page.unit !== "pt" ||
        page.parent_content_sha256 !== source.revision_id ||
        page.coordinate_space !== "display_page_normalized_top_left") return null;
    const w = page.width_micropoints, h = page.height_micropoints, matrix = page.transform;
    const rotations = { 0: [1, 0, 0, -1], 90: [0, 1, 1, 0], 180: [-1, 0, 0, 1], 270: [0, -1, -1, 0] };
    if (![w, h].every((v) => Number.isSafeInteger(v) && v > 0) || !matrix ||
        !rotations[page.rotation_degrees]?.every((v, i) => v === matrix[["m11", "m12", "m21", "m22"][i]]) ||
        ![matrix.tx_micropoints, matrix.ty_micropoints].every(Number.isSafeInteger)) return null;
    const canonical = point => {
      const offset = (value, extent, translation) => {
        const decimal = sourceDecimal(value);
        const scaled = sourcePrecision({ coefficient: decimal.coefficient * BigInt(extent), exponent: decimal.exponent });
        const exponent = Math.min(scaled.exponent, 0);
        return sourcePrecision({ coefficient: scaled.coefficient * power10(scaled.exponent - exponent) - BigInt(translation) * power10(-exponent), exponent });
      };
      const x = offset(point[0], w, matrix.tx_micropoints), y = offset(point[1], h, matrix.ty_micropoints);
      // Orthogonal inverse is the transpose; each coordinate uses one signed
      // axis. Keep rounding in PDF space, including negative ties.
      const px = matrix.m11 ? sourceInteger(x, BigInt(matrix.m11)) : sourceInteger(y, BigInt(matrix.m21));
      const py = matrix.m12 ? sourceInteger(x, BigInt(matrix.m12)) : sourceInteger(y, BigInt(matrix.m22));
      return [BigInt(matrix.m11) * px + BigInt(matrix.m12) * py + BigInt(matrix.tx_micropoints),
        BigInt(matrix.m21) * px + BigInt(matrix.m22) * py + BigInt(matrix.ty_micropoints)];
    };
    const [aa, tt, ee] = points.map(canonical);
    const dx = tt[0] - aa[0], dy = tt[1] - aa[1], ex = ee[0] - aa[0], ey = ee[1] - aa[1];
    const determinant = 2n * (dx * ey - dy * ex);
    if (determinant === 0n) return null;
    const hxNumerator = (dx * dx + dy * dy) * ey - (ex * ex + ey * ey) * dy;
    const hyNumerator = dx * (ex * ex + ey * ey) - ex * (dx * dx + dy * dy);
    const hx = Number(hxNumerator) / Number(determinant), hy = Number(hyNumerator) / Number(determinant);
    const radius = Math.hypot(hx, hy), major = -hxNumerator * ey + hyNumerator * ex < 0n;
    const rx = radius / w * 1000, ry = radius / h * 1000;
    if (![hx, hy, rx, ry].every(Number.isFinite) || !(radius > 0)) return null;
    const sign = determinant > 0n ? 1n : -1n, denominator = determinant * sign;
    const centerNumerators = [(aa[0] * determinant + hxNumerator) * sign, (aa[1] * determinant + hyNumerator) * sign];
    const radiusSquare = hxNumerator ** 2n + hyNumerator ** 2n;
    const vectorAt = point => canonical(point).map((coordinate, axis) => coordinate * denominator - centerNumerators[axis]);
    const startVector = [-hxNumerator * sign, -hyNumerator * sign], endVector = vectorAt(points[2]);
    const direction = determinant > 0n ? 1 : -1;
    // Rationalize opposing center/radical terms before converting to Number.
    // Direct C + R*u/|u| loses visible digits for shallow, very large circles.
    // These are display approximations, never emitted as source coordinates.
    const endpoint = vector => {
      const norm = vector[0] ** 2n + vector[1] ** 2n;
      if (!norm) return null;
      const factor = Math.sqrt(Number(radiusSquare) / Number(norm));
      return vector.map((v, axis) => {
        const c = centerNumerators[axis], term = Number(v) * factor;
        const numerator = (c > 0n && v < 0n) || (c < 0n && v > 0n) ?
          Number(radiusSquare * v ** 2n - c ** 2n * norm) / Number(norm) / (term - Number(c)) : Number(c) + term;
        return numerator / Number(denominator) / (axis ? h : w);
      });
    };
    return { radius, w, h, startVector, endVector, endpoint, vectorAt,
      sweep: vectorAngle(startVector, endVector, direction), direction,
      path: `M ${Number(aa[0]) / w * 1000} ${Number(aa[1]) / h * 1000} A ${rx} ${ry} 0 ${major ? 1 : 0} ${direction > 0 ? 1 : 0} ${Number(ee[0]) / w * 1000} ${Number(ee[1]) / h * 1000}` };
  }
  function vectorAngle(start, end, direction) {
    const cross = (start[0] * end[1] - start[1] * end[0]) * BigInt(direction);
    const dot = start[0] * end[0] + start[1] * end[1];
    const angle = Math.atan2(Number(cross), Number(dot));
    return angle < 0 ? angle + 2 * Math.PI : angle;
  }
  function arcPath(points, page, source) { return arcModel(points, page, source)?.path || null; }
  function geometryModel(geometry, page, source) {
    if (!geometry || !["circular_arc", "circular_arc_span"].includes(geometry.kind)) return null;
    const model = arcModel(geometry.kind === "circular_arc" ? geometry.points : geometry.base_points, page, source);
    if (!model || geometry.kind === "circular_arc") return model;
    const displayRay = (ray) => {
      if (!Array.isArray(ray) || ray.length !== 2 || !ray.every(v => typeof v === "string" && v.length <= 79 && /^(?:0|-?[1-9][0-9]*)$/.test(v))) return null;
      const values = ray.map(BigInt), abs = v => v < 0n ? -v : v;
      const max = values.reduce((a, v) => abs(v) > a ? abs(v) : a, 0n);
      if (!max || max >= 1n << 256n) return null;
      let a = abs(values[0]), b = abs(values[1]);
      while (b) { const remainder = a % b; a = b; b = remainder; }
      if (a !== 1n) return null;
      const [x, y] = values, m = page.transform;
      return [BigInt(m.m11) * x + BigInt(m.m21) * y, BigInt(m.m12) * x + BigInt(m.m22) * y];
    };
    const startVector = displayRay(geometry.start_ray), endVector = displayRay(geometry.end_ray);
    if (startVector === null || endVector === null) return null;
    const sweep = vectorAngle(startVector, endVector, model.direction);
    if (!(sweep > 0)) return null;
    return { ...model, startVector, endVector, sweep };
  }
  function arcCut(geometry, point, page, source, width, height) {
    if (!validPoint(point)) return null;
    const m = geometryModel(geometry, page, source); if (!m) return null;
    const vector = m.vectorAt(point);
    if (vector.every(value => value === 0n)) return null;
    const position = vectorAngle(m.startVector, vector, m.direction) / m.sweep;
    // Display tolerance only. Exact endpoint/ray admission remains server-owned.
    if (!(position > 1e-12 && position < 1 - 1e-12)) return null;
    const projected = m.endpoint(vector);
    if (!validPoint(projected)) return null;
    if (width !== undefined && (!(width > 0 && height > 0) || Math.hypot((point[0] - projected[0]) * width, (point[1] - projected[1]) * height) > 10)) return null;
    return { position, projected };
  }
  // Optional preview describes unsaved intervals only. Never serialize its SVG
  // endpoints into observations, cuts, or quantity authority.
  function arcGeometryPath(geometry, page, source, preview = null) {
    const m = geometryModel(geometry, page, source); if (!m) return null;
    let lower = 0, upper = 1, start = m.startVector, end = m.endVector;
    if (preview) {
      if (!Array.isArray(preview.cuts) || preview.cuts.length > MAX_POINTS || !Number.isInteger(preview.portion)) return null;
      const boundaries = preview.cuts.map(point => arcCut(geometry, point, page, source)?.position);
      if (boundaries.some((v, i) => v === undefined || (i && v <= boundaries[i - 1])) || preview.portion < 0 || preview.portion > boundaries.length) return null;
      const intervals = [0, ...boundaries, 1], rays = [m.startVector, ...preview.cuts.map(m.vectorAt), m.endVector];
      lower = intervals[preview.portion]; upper = intervals[preview.portion + 1];
      start = rays[preview.portion]; end = rays[preview.portion + 1];
    }
    const a = m.endpoint(start)?.map(value => value * 1000), e = m.endpoint(end)?.map(value => value * 1000);
    if (!a || !e || ![...a, ...e].every(Number.isFinite) || !(m.sweep > 0)) return null;
    return `M ${a[0]} ${a[1]} A ${m.radius / m.w * 1000} ${m.radius / m.h * 1000} 0 ${m.sweep * (upper - lower) > Math.PI ? 1 : 0} ${m.direction > 0 ? 1 : 0} ${e[0]} ${e[1]}`;
  }

  return { MAX_POINTS, validPoint, validSource, sameSource, createState, transition, canFinish,
    pointFromClient, hitTest, projectEdge, cutAt, reply, arcPath, arcGeometryPath, arcCut };
});
