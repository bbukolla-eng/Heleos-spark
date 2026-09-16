"use strict";

(() => {
  const MAX_PDF_BYTES = 256 * 1024 * 1024;
  const selectionKey = `heleos-drawing-selection:${window.location.pathname}`;
  const element = (id) => document.getElementById(id);
  const ui = Object.fromEntries([
    "project-name", "import-button", "pdf-input", "error-notice", "error-message",
    "dismiss-error", "activity", "workspace", "refresh-button", "documents-hint",
    "document-list", "sheets-section", "sheet-count", "sheet-list", "empty-state",
    "empty-heading", "drop-zone", "choose-pdf-button", "drawing-view", "selected-document",
    "selected-sheet", "previous-sheet", "next-sheet", "page-position", "open-original",
    "page-size", "page-rotation", "pdf-preview", "fallback-original", "revision-id", "sheet-id",
    "image-controls", "fit-preview", "zoom-out", "zoom-level", "zoom-in", "image-viewport",
    "image-canvas", "image-status", "preview-message", "retry-preview",
  ].map((id) => [id, element(id)]));
  let state = { documents: [] };
  let selectedRevision = null;
  let selectedIndex = null;
  let busy = false;
  let loaded = false;
  let dragDepth = 0;
  let previewImage = null;
  let previewUrl = null;
  let previewRequest = 0;
  let zoomIndex = 0;
  let sourceHighlight = null;
  let pickingRegion = false;
  let pickingLine = false;
  let linePurpose = null;
  let regionStart = null;
  const pathTools = window.HeleosPathEditor;
  let pathEdit = null;
  let pathSheetBinding = null;
  let pathPointer = null;
  let lastPathClick = null;
  let pathControls = null;
  let pathStatus = null;
  const pathButtons = {};
  const zoomLevels = [1, 1.25, 1.5, 2, 3, 4];
  const rememberedPages = new Map();

  try {
    const saved = JSON.parse(sessionStorage.getItem(selectionKey));
    if (saved && typeof saved.revision === "string" && Number.isInteger(saved.index)) {
      selectedRevision = saved.revision;
      selectedIndex = saved.index;
    }
  } catch (_) {
    // A restricted browser may disable storage; the workspace still works.
  }

  function saveSelection() {
    try {
      sessionStorage.setItem(selectionKey, JSON.stringify({ revision: selectedRevision, index: selectedIndex }));
    } catch (_) { /* Selection remains available in this page. */ }
  }

  function setBusy(value, message = "") {
    busy = value;
    ui["workspace"].setAttribute("aria-busy", String(value));
    ui["import-button"].disabled = value;
    ui["choose-pdf-button"].disabled = value;
    ui["refresh-button"].disabled = value;
    ui["pdf-input"].disabled = value;
    ui["import-button"].textContent = value && loaded ? "Working…" : "＋ Import PDF";
    ui["activity"].textContent = message;
    ui["activity"].hidden = !message;
    ui["drop-zone"].setAttribute("aria-disabled", String(value));
  }

  function showError(message) {
    ui["error-message"].textContent = message;
    ui["error-notice"].hidden = false;
  }

  function clearError() {
    ui["error-notice"].hidden = true;
    ui["error-message"].textContent = "";
  }

  async function api(path, options = {}) {
    const response = await fetch(path, { cache: "no-store", credentials: "same-origin", ...options });
    let result;
    try {
      result = await response.json();
    } catch (_) {
      throw new Error("The workspace returned an unreadable response. Refresh and try again.");
    }
    if (!response.ok) {
      throw new Error(result?.error?.message || `The request could not be completed (${response.status}).`);
    }
    if (!result || !result.project || !Array.isArray(result.documents)) {
      throw new Error("The workspace response is incomplete. Refresh and try again.");
    }
    for (const drawing of result.documents) {
      if (!/^[a-f0-9]{64}$/i.test(drawing.revision_id) || !Array.isArray(drawing.sheets)
          || typeof drawing.name !== "string" || !Number.isInteger(drawing.page_count)
          || drawing.page_count < 0 || drawing.sheets.some((sheet) => !Number.isSafeInteger(sheet.index) || sheet.index < 0)) {
        throw new Error("The workspace returned invalid drawing details. Refresh and try again.");
      }
    }
    return result;
  }

  function sortedSheets(drawing) {
    return [...drawing.sheets].sort((a, b) => a.index - b.index);
  }

  function currentDocument() {
    return state.documents.find((drawing) => drawing.revision_id === selectedRevision);
  }

  function applyState(nextState, preferredRevision = null) {
    state = nextState;
    loaded = true;
    if (preferredRevision) selectedRevision = preferredRevision;
    const drawing = currentDocument() || state.documents[0];
    selectedRevision = drawing?.revision_id || null;
    const sheets = drawing ? sortedSheets(drawing) : [];
    if (!sheets.some((sheet) => sheet.index === selectedIndex)) selectedIndex = sheets[0]?.index ?? null;
    saveSelection();
    render();
  }

  function createText(tag, className, value) {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = value;
    return node;
  }

  function selectDocument(revision) {
    if (selectedRevision) rememberedPages.set(selectedRevision, selectedIndex);
    selectedRevision = revision;
    const drawing = currentDocument();
    const sheets = sortedSheets(drawing);
    const previousIndex = rememberedPages.get(revision);
    selectedIndex = sheets.some((sheet) => sheet.index === previousIndex) ? previousIndex : sheets[0]?.index ?? null;
    saveSelection();
    render();
  }

  function selectSheet(index) {
    selectedIndex = index;
    rememberedPages.set(selectedRevision, index);
    saveSelection();
    render();
  }

  function formatNumber(value) {
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
  }

  function pageSize(sheet) {
    const width = sheet.width_micropoints / 1_000_000;
    const height = sheet.height_micropoints / 1_000_000;
    if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return "Unavailable";
    return `${formatNumber(width / 72)} × ${formatNumber(height / 72)} in (${formatNumber(width)} × ${formatNumber(height)} pt)`;
  }

  function sizePreview() {
    const ready = previewImage && !previewImage.hidden && previewImage.naturalWidth > 0;
    ui["fit-preview"].disabled = !ready || zoomIndex === 0;
    ui["zoom-out"].disabled = !ready || zoomIndex === 0;
    ui["zoom-in"].disabled = !ready || zoomIndex === zoomLevels.length - 1;
    ui["zoom-level"].textContent = zoomIndex === 0 ? "Fit" : `${zoomLevels[zoomIndex] * 100}% of fit`;
    if (!ready) return;
    const viewport = ui["image-viewport"];
    const fit = Math.min(
      Math.max(viewport.clientWidth - 48, 1) / previewImage.naturalWidth,
      Math.max(viewport.clientHeight - 48, 1) / previewImage.naturalHeight,
      1,
    );
    const scale = fit * zoomLevels[zoomIndex];
    // HTML dimensions avoid inline styles and preserve the drawing's aspect ratio.
    previewImage.width = Math.max(1, Math.round(previewImage.naturalWidth * scale));
    previewImage.height = Math.max(1, Math.round(previewImage.naturalHeight * scale));
    if (pathEdit) drawSourceHighlight();
  }

  function selectedSheetBinding() {
    const sheet = currentDocument()?.sheets.find((entry) => entry.index === selectedIndex);
    if (!sheet) return null;
    // The preview API owns these source dimensions. Any changed page geometry
    // cancels its gesture, even when the revision and page selection are retained.
    return JSON.stringify([selectedRevision, selectedIndex, sheet.sheet_id,
      sheet.width_micropoints, sheet.height_micropoints, sheet.rotation_degrees,
      sheet.unit, sheet.parent_content_sha256, sheet.transform,
      sheet.geometry_fingerprint, sheet.media_box, sheet.crop_box]);
  }

  function pathSourceCurrent() {
    return pathEdit && pathEdit.source.revision_id === selectedRevision && pathEdit.source.index === selectedIndex &&
      pathSheetBinding === selectedSheetBinding();
  }

  function arcPageMatchesSheet(page, sheet, source) {
    if (!page || !sheet || page.revision_id !== source.revision_id || page.index !== sheet.index ||
        page.sheet_id !== sheet.sheet_id || page.fingerprint !== source.geometry_fingerprint ||
        (sheet.geometry_fingerprint && sheet.geometry_fingerprint !== source.geometry_fingerprint)) return false;
    return ["width_micropoints", "height_micropoints", "unit", "rotation_degrees", "parent_content_sha256"]
      .every((key) => page[key] !== undefined && page[key] === sheet[key]) &&
      ["m11", "m12", "m21", "m22", "tx_micropoints", "ty_micropoints"]
        .every((key) => page.transform?.[key] !== undefined && page.transform[key] === sheet.transform?.[key]);
  }

  function notifyPath() {
    if (!pathEdit) return;
    const detail = pathTools.reply(pathEdit);
    if (pathEdit.state !== "editing") {
      pathEdit = null; pathPointer = null; pathSheetBinding = null; lastPathClick = null;
      ui["image-canvas"].classList.remove("picking-region");
    }
    renderPathControls();
    drawSourceHighlight();
    window.dispatchEvent(new CustomEvent("heleos:path-draft", { detail }));
  }

  function changePath(action) {
    if (!pathEdit) return;
    if (!pathSourceCurrent() && action.type !== "cancel") action = { type: "cancel", reason: "source_changed" };
    pathEdit = pathTools.transition(pathEdit, action);
    notifyPath();
  }

  function cancelPath(reason) {
    if (pathEdit) changePath({ type: "cancel", reason });
  }

  function renderPathControls() {
    if (!pathControls && pathEdit) {
      pathControls = createText("div", "path-controls", "");
      pathControls.setAttribute("role", "group");
      pathControls.setAttribute("aria-label", "Edit drawing path");
      pathStatus = createText("p", "section-hint", "");
      pathStatus.setAttribute("role", "status");
      pathStatus.setAttribute("aria-live", "polite");
      pathControls.append(pathStatus);
      for (const [action, label] of [["undo", "Undo"], ["insert", "Insert vertex"], ["delete", "Delete vertex"],
        ["finish", "Finish path"], ["cancel", "Cancel"]]) {
        const button = createText("button", "button button-quiet", label);
        button.type = "button";
        button.addEventListener("click", () => changePath({ type: action }));
        pathButtons[action] = button; pathControls.append(button);
      }
      ui["image-controls"].insertAdjacentElement("afterend", pathControls);
    }
    if (!pathControls) return;
    pathControls.hidden = !pathEdit;
    if (!pathEdit) return;
    pathStatus.textContent = pathEdit.mode === "arc_split" ? "Click near the circular centerline at the size change. The marker is a radial preview; source support is checked when saved." :
      pathEdit.mode === "split" ? "Click a leg at the size change, then finish the cut selection." :
      pathEdit.mode === "circular_arc" ? `${pathEdit.points.length} of 3 arc anchors. Place start, a point along the sweep, then end; drag anchors to adjust. Enter finishes; Escape cancels.` :
      pathEdit.insert_pending ? "Click the drawing to insert a vertex into the selected leg." :
      `${pathEdit.points.length} vertices. ${pathEdit.mode === "straight" ? "Place two endpoints or drag an endpoint." :
        "Click to add; drag a vertex to move it. Select a leg before inserting."} Enter finishes; Escape cancels.`;
    pathButtons.undo.disabled = !pathEdit.history.length;
    pathButtons.insert.disabled = pathEdit.mode !== "polyline" || pathEdit.selected_edge === null || pathEdit.points.length >= pathTools.MAX_POINTS;
    pathButtons.delete.disabled = pathEdit.mode !== "polyline" || pathEdit.points.length <= 2;
    pathButtons.finish.disabled = !pathTools.canFinish(pathEdit);
    pathButtons.finish.textContent = ["split", "arc_split"].includes(pathEdit.mode) ? "Finish cut selection" : "Finish path";
  }

  function drawPathDraft(overlay) {
    const bounds = overlay.parentNode.getBoundingClientRect();
    const makeSvg = (tag, attributes) => {
      const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
      for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, String(value));
      overlay.append(node); return node;
    };
    if (pathEdit.mode === "arc_split") {
      const path = pathTools.arcGeometryPath(pathEdit.geometry, pathEdit.page_geometry, pathEdit.source);
      if (path) makeSvg("path", { d: path });
    } else if (pathEdit.mode === "circular_arc") {
      const path = pathTools.arcPath(pathEdit.points, pathEdit.page_geometry, pathEdit.source);
      if (path) makeSvg("path", { d: path });
    } else pathEdit.points.forEach((point, index) => {
      if (index) makeSvg("line", { x1: pathEdit.points[index - 1][0] * 1000, y1: pathEdit.points[index - 1][1] * 1000,
        x2: point[0] * 1000, y2: point[1] * 1000, stroke: pathEdit.selected_edge === index - 1 ? "#1b6e80" : "#ad4d17",
        "stroke-width": pathEdit.selected_edge === index - 1 ? 5 : 3, "vector-effect": "non-scaling-stroke" });
    });
    const handle = (point, selected) => makeSvg("ellipse", { cx: point[0] * 1000, cy: point[1] * 1000,
      rx: 6000 / Math.max(1, bounds.width), ry: 6000 / Math.max(1, bounds.height),
      fill: selected ? "#1b6e80" : "#ffffff", stroke: "#ad4d17", "stroke-width": 2, "vector-effect": "non-scaling-stroke" });
    pathEdit.points.forEach((point, index) => handle(point, pathEdit.selected_vertex === index));
    if (pathEdit.mode === "arc_split" && pathEdit.split) {
      const marker = pathTools.arcCut(pathEdit.geometry, pathEdit.split.source_point, pathEdit.page_geometry, pathEdit.source);
      if (marker) handle(marker.projected, true);
    } else if (pathEdit.split) {
      const a = pathEdit.points[pathEdit.split.edge_index], b = pathEdit.points[pathEdit.split.edge_index + 1];
      const fraction = Number(pathEdit.split.fraction);
      handle([a[0] + fraction * (b[0] - a[0]), a[1] + fraction * (b[1] - a[1])], true);
    }
  }

  function drawSourceHighlight() {
    const overlay = ui["image-canvas"].querySelector(".source-overlay");
    if (!overlay) return;
    overlay.replaceChildren();
    if (pathSourceCurrent()) { drawPathDraft(overlay); return; }
    if (!sourceHighlight || sourceHighlight.revision_id !== selectedRevision || sourceHighlight.index !== selectedIndex) return;
    if (["circular_arc", "circular_arc_span"].includes(sourceHighlight.geometry?.kind)) {
      const sheet = currentDocument()?.sheets.find((entry) => entry.index === selectedIndex);
      if (!arcPageMatchesSheet(sourceHighlight.page_geometry, sheet, sourceHighlight)) return;
      const path = pathTools.arcGeometryPath(sourceHighlight.geometry, sourceHighlight.page_geometry, sourceHighlight, sourceHighlight.arc_preview);
      if (path) {
        const line = document.createElementNS("http://www.w3.org/2000/svg", "path");
        line.setAttribute("d", path); overlay.append(line);
        const selected = pathTools.arcCut(sourceHighlight.geometry, sourceHighlight.arc_marker, sourceHighlight.page_geometry, sourceHighlight);
        if (selected) {
          const bounds = overlay.parentNode.getBoundingClientRect();
          const marker = document.createElementNS("http://www.w3.org/2000/svg", "ellipse");
          for (const [name, value] of Object.entries({ cx: selected.projected[0] * 1000, cy: selected.projected[1] * 1000,
            rx: 6000 / Math.max(1, bounds.width), ry: 6000 / Math.max(1, bounds.height), fill: "#1b6e80",
            stroke: "#ffffff", "stroke-width": 2, "vector-effect": "non-scaling-stroke" })) marker.setAttribute(name, String(value));
          overlay.append(marker);
        }
      }
      return;
    }
    if (Array.isArray(sourceHighlight.points)) {
      const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
      line.setAttribute("points", sourceHighlight.points.map((point) => point[0] * 1000 + "," + point[1] * 1000).join(" "));
      overlay.append(line);
      return;
    }
    const [x0, y0, x1, y1] = sourceHighlight.bbox;
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    for (const [name, value] of Object.entries({ x: x0 * 1000, y: y0 * 1000, width: (x1 - x0) * 1000, height: (y1 - y0) * 1000 })) {
      rect.setAttribute(name, String(value));
    }
    overlay.append(rect);
  }

  function regionPoint(event, surface) {
    const bounds = surface.getBoundingClientRect();
    return [Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)),
      Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height))];
  }

  function pathPointerDown(event, surface) {
    if (!pathEdit || event.button !== 0 || !pathSourceCurrent()) return;
    event.preventDefault();
    if (pathPointer) return;
    const bounds = surface.getBoundingClientRect();
    const point = pathTools.pointFromClient(event, bounds);
    if (!point) return;
    ui["image-viewport"].focus({ preventScroll: true });
    // Browsers may report detail=0 for PointerEvent, so also suppress the second
    // near-identical click of a double-click sequence using display pixels.
    if (event.detail > 1 || (lastPathClick && event.timeStamp - lastPathClick.time >= 0 &&
        event.timeStamp - lastPathClick.time < 350 && Math.hypot(event.clientX - lastPathClick.x, event.clientY - lastPathClick.y) <= 4)) return;
    pathPointer = { pointer_id: event.pointerId, request_id: pathEdit.request_id, source: pathEdit.source,
      start: point, x: event.clientX, y: event.clientY, action: "place", hit: null };
    const hit = pathTools.hitTest(pathEdit.points, point, bounds.width, bounds.height);
    if (pathEdit.mode === "arc_split") pathPointer.action = "arc_cut";
    else if (pathEdit.mode === "split") pathPointer.action = "cut";
    else if (!pathEdit.insert_pending && hit?.kind === "vertex") {
      pathPointer.action = "drag";
      changePath({ type: "begin_drag", index: hit.index, pointer_id: event.pointerId });
    } else if (pathEdit.mode !== "circular_arc" && !pathEdit.insert_pending && hit?.kind === "edge") {
      pathPointer.action = "select_edge"; pathPointer.hit = hit.index;
    }
    surface.setPointerCapture(event.pointerId);
  }

  function pathPointerMove(event, surface, finish = false) {
    if (!pathEdit || !pathPointer || pathPointer.pointer_id !== event.pointerId ||
        pathPointer.request_id !== pathEdit.request_id || !pathTools.sameSource(pathPointer.source, pathEdit.source)) return;
    event.preventDefault();
    if (!pathSourceCurrent()) { cancelPath("source_changed"); return; }
    const pointer = pathPointer, bounds = surface.getBoundingClientRect();
    const point = pathTools.pointFromClient(event, bounds);
    if (!point) return;
    if (pointer.action === "drag") {
      changePath({ type: finish ? "end_drag" : "move_drag", pointer_id: event.pointerId, point });
    } else if (finish && Math.hypot(event.clientX - pointer.x, event.clientY - pointer.y) <= 5) {
      if (pointer.action === "arc_cut") {
        if (pathTools.arcCut(pathEdit.geometry, point, pathEdit.page_geometry, pathEdit.source, bounds.width, bounds.height))
          changePath({ type: "arc_cut", source_point: point });
      } else if (pointer.action === "cut") {
        const cut = pathTools.cutAt(pathEdit.points, point, bounds.width, bounds.height);
        if (cut) changePath({ type: "cut", ...cut });
      } else if (pointer.action === "select_edge") changePath({ type: "select_edge", index: pointer.hit });
      else changePath({ type: "place", point });
    }
    if (finish) {
      pathPointer = null;
      lastPathClick = { time: event.timeStamp, x: event.clientX, y: event.clientY };
      if (surface.hasPointerCapture(event.pointerId)) surface.releasePointerCapture(event.pointerId);
    }
  }

  function loadImagePreview(drawing, sheet, retry = false) {
    const url = `api/page/${encodeURIComponent(drawing.revision_id)}/${sheet.index}.png`;
    if (previewUrl === url && !retry) return;
    cancelPath(retry ? "preview_reloaded" : "source_changed");
    pickingRegion = false; pickingLine = false; regionStart = null;
    ui["image-canvas"].classList.remove("picking-region");
    previewUrl = url;
    const request = ++previewRequest;
    zoomIndex = 0;
    const img = document.createElement("img");
    img.alt = `${drawing.name}, page ${sheet.index + 1} — view-only drawing preview`;
    img.hidden = true;
    img.draggable = false;
    previewImage = img;
    const surface = document.createElement("div");
    surface.className = "page-surface";
    const overlay = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    overlay.classList.add("source-overlay");
    overlay.setAttribute("viewBox", "0 0 1000 1000");
    overlay.setAttribute("preserveAspectRatio", "none");
    overlay.setAttribute("aria-hidden", "true");
    surface.append(img, overlay);
    ui["image-canvas"].replaceChildren(surface);
    surface.addEventListener("pointerdown", (event) => {
      if (request !== previewRequest) return;
      if (pathEdit) { if (!img.hidden) pathPointerDown(event, surface); return; }
      if ((!pickingRegion && !pickingLine) || img.hidden || event.button !== 0) return;
      event.preventDefault();
      regionStart = regionPoint(event, surface);
      surface.setPointerCapture(event.pointerId);
    });
    surface.addEventListener("pointermove", (event) => {
      if (request !== previewRequest) return;
      if (pathEdit) { pathPointerMove(event, surface); return; }
      if ((!pickingRegion && !pickingLine) || !regionStart) return;
      const end = regionPoint(event, surface);
      sourceHighlight = pickingLine ? { revision_id: selectedRevision, index: selectedIndex, points: [regionStart, end] } : { revision_id: selectedRevision, index: selectedIndex,
        bbox: [Math.min(regionStart[0], end[0]), Math.min(regionStart[1], end[1]),
          Math.max(regionStart[0], end[0]), Math.max(regionStart[1], end[1])] };
      drawSourceHighlight();
    });
    surface.addEventListener("pointerup", (event) => {
      if (request !== previewRequest) return;
      if (pathEdit) { pathPointerMove(event, surface, true); return; }
      if ((!pickingRegion && !pickingLine) || !regionStart) return;
      const end = regionPoint(event, surface);
      if (pickingLine) {
        const start = regionStart;
        regionStart = null;
        surface.releasePointerCapture(event.pointerId);
        if (Math.hypot(start[0] - end[0], start[1] - end[1]) < 0.002) return;
        sourceHighlight = { revision_id: selectedRevision, index: selectedIndex, points: [start, end] };
        pickingLine = false;
        ui["image-canvas"].classList.remove("picking-region");
        drawSourceHighlight();
        window.dispatchEvent(new CustomEvent("heleos:line-picked", {
          detail: { ...sourceHighlight, purpose: linePurpose },
        }));
        return;
      }
      const bbox = [Math.min(regionStart[0], end[0]), Math.min(regionStart[1], end[1]),
        Math.max(regionStart[0], end[0]), Math.max(regionStart[1], end[1])];
      regionStart = null;
      surface.releasePointerCapture(event.pointerId);
      if (bbox[2] - bbox[0] < 0.002 || bbox[3] - bbox[1] < 0.002) return;
      sourceHighlight = { revision_id: selectedRevision, index: selectedIndex, bbox };
      drawSourceHighlight();
      window.dispatchEvent(new CustomEvent("heleos:region-picked", { detail: sourceHighlight }));
    });
    surface.addEventListener("pointercancel", (event) => {
      regionStart = null;
      if (pathPointer?.pointer_id === event.pointerId) { pathPointer = null; changePath({ type: "cancel_drag" }); }
    });
    surface.addEventListener("lostpointercapture", (event) => {
      if (pathPointer?.pointer_id === event.pointerId) { pathPointer = null; changePath({ type: "cancel_drag" }); }
    });
    surface.addEventListener("dblclick", (event) => { if (pathEdit) event.preventDefault(); });
    drawSourceHighlight();
    ui["image-viewport"].scrollTop = 0;
    ui["image-viewport"].scrollLeft = 0;
    ui["image-viewport"].setAttribute("aria-busy", "true");
    ui["image-status"].hidden = false;
    ui["preview-message"].textContent = `Preparing page ${sheet.index + 1} preview…`;
    ui["retry-preview"].hidden = true;
    sizePreview();
    img.addEventListener("load", () => {
      if (request !== previewRequest) return;
      img.hidden = false;
      ui["image-viewport"].setAttribute("aria-busy", "false");
      ui["image-status"].hidden = true;
      ui["preview-message"].textContent = "";
      sizePreview();
    });
    img.addEventListener("error", () => {
      if (request !== previewRequest) return;
      ui["image-viewport"].setAttribute("aria-busy", "false");
      ui["preview-message"].textContent = "This page preview could not be loaded. Retry, or open the original PDF using the link below.";
      ui["retry-preview"].hidden = false;
    });
    img.src = url;
  }

  function clearImagePreview() {
    cancelPath("preview_unavailable");
    previewRequest += 1;
    previewUrl = null;
    previewImage = null;
    ui["image-canvas"].replaceChildren();
    ui["image-status"].hidden = true;
    ui["image-viewport"].hidden = true;
    ui["image-controls"].hidden = true;
  }

  function render() {
    if (pathEdit && !pathSourceCurrent()) cancelPath("source_changed");
    const focusedRevision = document.activeElement?.dataset.revision;
    const focusedSheet = document.activeElement?.dataset.sheetIndex;
    let focusTarget = null;
    ui["project-name"].textContent = state.project?.name || "Drawing workspace";
    ui["documents-hint"].textContent = state.documents.length ? "Select a PDF to browse its sheets." : "Your imported drawing sets appear here.";
    ui["document-list"].replaceChildren();
    for (const drawing of state.documents) {
      const item = document.createElement("li");
      const button = createText("button", "document-button", "");
      button.type = "button";
      button.dataset.revision = drawing.revision_id;
      if (focusedRevision === drawing.revision_id) focusTarget = button;
      button.setAttribute("aria-current", String(drawing.revision_id === selectedRevision));
      button.append(createText("span", "document-icon", "PDF"));
      const text = createText("span", "document-copy", "");
      text.append(createText("span", "document-name", drawing.name));
      text.append(createText("span", "document-pages", `${drawing.page_count} ${drawing.page_count === 1 ? "page" : "pages"}`));
      button.append(text);
      button.addEventListener("click", () => selectDocument(drawing.revision_id));
      item.append(button);
      ui["document-list"].append(item);
    }

    const drawing = currentDocument();
    const sheets = drawing ? sortedSheets(drawing) : [];
    const sheet = sheets.find((entry) => entry.index === selectedIndex);
    window.dispatchEvent(new CustomEvent("heleos:selection", {
      detail: { state, revision: selectedRevision, index: selectedIndex },
    }));
    ui["sheets-section"].hidden = !drawing;
    ui["sheet-count"].textContent = String(sheets.length);
    ui["sheet-list"].replaceChildren();
    for (const entry of sheets) {
      const item = document.createElement("li");
      const button = createText("button", "sheet-button", "");
      button.type = "button";
      button.dataset.sheetIndex = String(entry.index);
      if (focusedSheet === String(entry.index)) focusTarget = button;
      button.setAttribute("aria-current", String(entry.index === selectedIndex));
      button.append(createText("span", "sheet-number", String(entry.index + 1).padStart(2, "0")));
      button.append(createText("span", "sheet-name", `Page ${entry.index + 1}`));
      button.append(createText("span", "sheet-arrow", "↗"));
      button.addEventListener("click", () => selectSheet(entry.index));
      item.append(button);
      ui["sheet-list"].append(item);
    }
    focusTarget?.focus({ preventScroll: true });

    ui["empty-state"].hidden = Boolean(sheet);
    ui["drawing-view"].hidden = !sheet;
    ui["empty-heading"].textContent = drawing ? "No sheets are available in this PDF." : "Bring your first drawing in.";
    if (!sheet) {
      ui["pdf-preview"].removeAttribute("src");
      clearImagePreview();
      return;
    }

    const pdfUrl = `api/pdf/${encodeURIComponent(drawing.revision_id)}#page=${sheet.index + 1}`;
    ui["selected-document"].textContent = drawing.name;
    ui["selected-sheet"].textContent = `Page ${sheet.index + 1}`;
    ui["page-position"].textContent = `${sheets.indexOf(sheet) + 1} / ${sheets.length}`;
    ui["previous-sheet"].disabled = sheets.indexOf(sheet) === 0;
    ui["next-sheet"].disabled = sheets.indexOf(sheet) === sheets.length - 1;
    ui["page-size"].textContent = pageSize(sheet);
    ui["page-rotation"].textContent = Number.isFinite(sheet.rotation_degrees) ? `${sheet.rotation_degrees}°` : "Unavailable";
    ui["revision-id"].textContent = drawing.revision_id;
    ui["sheet-id"].textContent = sheet.sheet_id;
    ui["open-original"].href = pdfUrl;
    ui["fallback-original"].href = pdfUrl;
    ui["pdf-preview"].title = `${drawing.name}, page ${sheet.index + 1} — original PDF`;
    const imageMode = state.preview_mode === "image";
    ui["pdf-preview"].hidden = imageMode;
    if (imageMode) {
      ui["pdf-preview"].removeAttribute("src");
      ui["image-viewport"].hidden = false;
      ui["image-controls"].hidden = false;
      loadImagePreview(drawing, sheet);
    } else {
      clearImagePreview();
      if (ui["pdf-preview"].getAttribute("src") !== pdfUrl) ui["pdf-preview"].src = pdfUrl;
    }
  }

  async function refresh() {
    if (busy) return;
    clearError();
    setBusy(true, loaded ? "Refreshing drawing library…" : "Opening your workspace…");
    try {
      applyState(await api("api/state"));
      setBusy(false, "");
    } catch (error) {
      setBusy(false, "");
      showError(error instanceof TypeError ? "The local workspace could not be reached. Check that it is running, then refresh." : error.message);
    }
  }

  async function importPdf(files) {
    if (busy || !files?.length) return;
    clearError();
    if (files.length !== 1) {
      showError("Choose one PDF at a time.");
      return;
    }
    const file = files[0];
    if (file.size > MAX_PDF_BYTES) {
      showError("This file exceeds the 256 MiB import limit. Choose a smaller PDF.");
      return;
    }
    if (file.size === 0) {
      showError("This file is empty. Choose a PDF containing drawing pages.");
      return;
    }
    const previousRevisions = new Set(state.documents.map((drawing) => drawing.revision_id));
    setBusy(true, `Importing ${file.name}… Checking the original PDF and reading its pages.`);
    try {
      const nextState = await api(`api/import?name=${encodeURIComponent(file.name)}`, {
        method: "POST",
        headers: { "Content-Type": "application/pdf" },
        body: file,
      });
      const imported = nextState.documents.find((drawing) => !previousRevisions.has(drawing.revision_id));
      // Keep the current sheet on duplicate import; focus the first sheet of a new drawing.
      if (imported) selectedIndex = sortedSheets(imported)[0]?.index ?? null;
      applyState(nextState, imported?.revision_id);
      setBusy(false, imported ? `Imported ${file.name}. ${imported.page_count} ${imported.page_count === 1 ? "page" : "pages"} ready to browse.` : "This PDF is already in the workspace. Your drawing library is up to date.");
    } catch (error) {
      setBusy(false, "");
      showError(error instanceof TypeError ? "The local workspace could not be reached. Check that it is running, then refresh before retrying the import." : error.message);
    }
  }

  for (const button of [ui["import-button"], ui["choose-pdf-button"]]) {
    button.addEventListener("click", () => { if (!busy) ui["pdf-input"].click(); });
  }
  ui["pdf-input"].addEventListener("change", () => {
    const files = Array.from(ui["pdf-input"].files || []);
    ui["pdf-input"].value = "";
    void importPdf(files);
  });
  ui["refresh-button"].addEventListener("click", () => { void refresh(); });
  window.addEventListener("heleos:request-selection", () => {
    window.dispatchEvent(new CustomEvent("heleos:selection", {
      detail: { state, revision: selectedRevision, index: selectedIndex },
    }));
  });
  window.addEventListener("heleos:pick-region", (event) => {
    if (event.detail === true) cancelPath("tool_changed");
    pickingRegion = event.detail === true;
    pickingLine = false;
    regionStart = null;
    ui["image-canvas"].classList.toggle("picking-region", pickingRegion || Boolean(pathEdit));
  });
  window.addEventListener("heleos:pick-line", (event) => {
    if (typeof event.detail === "string") cancelPath("tool_changed");
    pickingLine = typeof event.detail === "string";
    linePurpose = pickingLine ? event.detail : null;
    pickingRegion = false;
    regionStart = null;
    ui["image-canvas"].classList.toggle("picking-region", pickingLine || Boolean(pathEdit));
  });
  window.addEventListener("heleos:edit-path", (event) => {
    if (!pathTools) { showError("The drawing path editor is unavailable. Refresh the workspace."); return; }
    let next;
    try { next = pathTools.createState(event.detail); }
    catch (error) { showError(error.message); return; }
    const drawing = state.documents.find((entry) => entry.revision_id === next.source.revision_id);
    const sheet = drawing?.sheets.find((entry) => entry.index === next.source.index && entry.sheet_id === next.source.sheet_id);
    if (!sheet || state.preview_mode !== "image" ||
        (sheet.geometry_fingerprint && sheet.geometry_fingerprint !== next.source.geometry_fingerprint) ||
        (["circular_arc", "arc_split"].includes(next.mode) && !arcPageMatchesSheet(next.page_geometry, sheet, next.source))) {
      window.dispatchEvent(new CustomEvent("heleos:path-draft", {
        detail: pathTools.reply(pathTools.transition(next, { type: "cancel", reason: "source_unavailable" })),
      }));
      return;
    }
    cancelPath("request_replaced");
    pickingRegion = false; pickingLine = false; regionStart = null;
    selectedRevision = next.source.revision_id; selectedIndex = next.source.index;
    saveSelection(); render();
    pathEdit = next; pathSheetBinding = selectedSheetBinding(); lastPathClick = null;
    ui["image-canvas"].classList.add("picking-region");
    ui["image-viewport"].focus({ preventScroll: true });
    ui["drawing-view"].scrollIntoView({ block: "start", behavior: "auto" });
    notifyPath();
  });
  window.addEventListener("heleos:path-command", (event) => {
    const command = event.detail;
    if (!pathEdit || command?.request_id !== pathEdit.request_id || !pathTools.sameSource(command.source, pathEdit.source) ||
        !["finish", "cancel", "undo", "delete", "insert"].includes(command.action)) return;
    changePath({ type: command.action });
  });
  window.addEventListener("keydown", (event) => {
    if (!pathEdit || event.altKey || event.metaKey || event.ctrlKey || event.shiftKey || event.isComposing) return;
    const target = event.target;
    if (["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName) || target?.isContentEditable ||
        (["BUTTON", "A"].includes(target?.tagName) && event.key !== "Escape")) return;
    const action = { Enter: "finish", Escape: "cancel", Backspace: "delete", Delete: "delete" }[event.key];
    if (!action) return;
    event.preventDefault(); changePath({ type: action });
  });
  window.addEventListener("pagehide", () => cancelPath("navigation"));
  window.addEventListener("heleos:workflow-stage", () => cancelPath("workflow_changed"));
  window.addEventListener("heleos:show-source", (event) => {
    const source = event.detail;
    const drawing = state.documents.find((entry) => entry.revision_id === source?.revision_id);
    const validPoint = (point) => Array.isArray(point) && point.length === 2 &&
      point.every((number) => Number.isFinite(number) && number >= 0 && number <= 1);
    const validLine = Array.isArray(source?.points) && source.points.length >= 2 &&
      source.points.length <= 128 && source.points.every(validPoint);
    const validBox = Array.isArray(source?.bbox) && source.bbox.length === 4 &&
      source.bbox.every((number) => Number.isFinite(number) && number >= 0 && number <= 1);
    const arcSheet = drawing?.sheets.find((entry) => entry.index === source.index);
    if (["circular_arc", "circular_arc_span"].includes(source?.geometry?.kind) && !arcPageMatchesSheet(source.page_geometry, arcSheet, source)) return;
    const validArc = ["circular_arc", "circular_arc_span"].includes(source?.geometry?.kind) &&
      pathTools.arcGeometryPath(source.geometry, source.page_geometry, source, source.arc_preview);
    if (["circular_arc", "circular_arc_span"].includes(source?.geometry?.kind) && !validArc) return;
    if (!drawing?.sheets.some((entry) => entry.index === source.index) || (!validArc && !validLine && !validBox)) return;
    if (pathEdit && source.geometry_fingerprint && source.geometry_fingerprint !== pathEdit.source.geometry_fingerprint)
      cancelPath("source_changed");
    selectedRevision = source.revision_id;
    selectedIndex = source.index;
    sourceHighlight = source;
    saveSelection();
    render();
    drawSourceHighlight();
    ui["drawing-view"].scrollIntoView({ block: "start", behavior: "auto" });
  });
  ui["dismiss-error"].addEventListener("click", clearError);
  ui["retry-preview"].addEventListener("click", () => {
    const drawing = currentDocument();
    const sheet = drawing?.sheets.find((entry) => entry.index === selectedIndex);
    if (drawing && sheet && state.preview_mode === "image") loadImagePreview(drawing, sheet, true);
  });
  for (const [id, direction] of [["zoom-out", -1], ["zoom-in", 1]]) {
    ui[id].addEventListener("click", () => {
      zoomIndex = Math.max(0, Math.min(zoomLevels.length - 1, zoomIndex + direction));
      sizePreview();
    });
  }
  ui["fit-preview"].addEventListener("click", () => {
    zoomIndex = 0;
    sizePreview();
    ui["image-viewport"].scrollTop = 0;
    ui["image-viewport"].scrollLeft = 0;
  });
  window.addEventListener("resize", sizePreview);
  for (const [id, direction] of [["previous-sheet", -1], ["next-sheet", 1]]) {
    ui[id].addEventListener("click", () => {
      const drawing = currentDocument();
      if (!drawing) return;
      const sheets = sortedSheets(drawing);
      const next = sheets[sheets.findIndex((sheet) => sheet.index === selectedIndex) + direction];
      if (next) selectSheet(next.index);
    });
  }

  // Prevent the browser navigating away when a drawing is dropped outside the empty state.
  const carriesFiles = (event) => Array.from(event.dataTransfer?.types || []).includes("Files");
  window.addEventListener("dragenter", (event) => {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    dragDepth += 1;
    if (!busy) document.body.classList.add("dragging-file");
  });
  window.addEventListener("dragover", (event) => {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = busy ? "none" : "copy";
  });
  window.addEventListener("dragleave", (event) => {
    if (!carriesFiles(event)) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) document.body.classList.remove("dragging-file");
  });
  window.addEventListener("drop", (event) => {
    if (!carriesFiles(event)) return;
    event.preventDefault();
    dragDepth = 0;
    document.body.classList.remove("dragging-file");
    void importPdf(Array.from(event.dataTransfer.files));
  });

  void refresh();
})();
