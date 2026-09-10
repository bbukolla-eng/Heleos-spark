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
  }

  function loadImagePreview(drawing, sheet, retry = false) {
    const url = `api/page/${encodeURIComponent(drawing.revision_id)}/${sheet.index}.png`;
    if (previewUrl === url && !retry) return;
    previewUrl = url;
    const request = ++previewRequest;
    zoomIndex = 0;
    const img = document.createElement("img");
    img.alt = `${drawing.name}, page ${sheet.index + 1} — view-only drawing preview`;
    img.hidden = true;
    img.draggable = false;
    previewImage = img;
    ui["image-canvas"].replaceChildren(img);
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
    previewRequest += 1;
    previewUrl = null;
    previewImage = null;
    ui["image-canvas"].replaceChildren();
    ui["image-status"].hidden = true;
    ui["image-viewport"].hidden = true;
    ui["image-controls"].hidden = true;
  }

  function render() {
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
