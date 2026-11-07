"use strict";

(() => {
  const el = (id) => document.getElementById("equipment-" + id);
  const ui = Object.fromEntries(["add-plan", "add-schedule", "pages", "reader", "reader-status",
    "run", "message", "error", "history", "results", "summary", "export", "reviewer",
    "warnings", "rows", "manual-tag", "manual-reason", "place", "cancel-place", "audit"].map((id) => [id, el(id)]));
  let capabilities = null;
  let selection = null;
  let selectedPages = [];
  let activeRun = null;
  let pending = false;
  let placing = false;
  let timer = null;
  let requestSequence = 0;
  const issueNames = { plan_only: "Not found in the selected schedule pages",
    schedule_only: "Not found on the selected plan pages", repeated_plan_tag: "Repeated plan tag",
    repeated_schedule_tag: "Repeated schedule tag", count_conflict: "More than one occurrence included",
    schedule_quantity_mismatch: "Draft tag quantity differs from the written schedule quantity" };

  function text(tag, value, className = "") {
    const node = document.createElement(tag);
    node.textContent = value;
    node.className = className;
    return node;
  }
  function message(value) { ui.message.textContent = value; }
  function error(value) { ui.error.textContent = value; ui.error.hidden = !value; }
  async function request(path, body) {
    const response = await fetch(path, { cache: "no-store", credentials: "same-origin",
      ...(body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}) });
    let result;
    try { result = await response.json(); }
    catch (_) { throw new Error("The workspace returned an unreadable equipment response."); }
    if (!response.ok) throw new Error(result?.error?.message || "The equipment operation failed.");
    return result;
  }
  function pageName(source) {
    const doc = selection?.state.documents.find((item) => item.revision_id === source.revision_id);
    return (doc?.name || "Drawing") + " · page " + (source.index + 1);
  }
  function controls() {
    const hasPage = selection?.revision && Number.isInteger(selection.index);
    ui["add-plan"].disabled = !hasPage || pending;
    ui["add-schedule"].disabled = !hasPage || pending;
    const reader = ui.reader.value === "text" ? capabilities?.text_reader : capabilities?.vision_configured;
    ui.run.disabled = pending || !selectedPages.length || !reader;
    ui.reader.disabled = pending;
    ui.history.disabled = pending;
    ui.place.disabled = pending || !activeRun || activeRun.state !== "completed" || selection?.state.preview_mode !== "image";
  }
  function renderPages() {
    ui.pages.replaceChildren();
    for (const source of selectedPages) {
      const li = text("li", "");
      li.append(text("span", (source.role === "plan" ? "Plan" : "Schedule") + " — " + pageName(source)));
      const remove = text("button", "Remove", "button button-quiet");
      remove.type = "button";
      remove.disabled = pending;
      remove.addEventListener("click", () => {
        selectedPages = selectedPages.filter((item) => item !== source);
        renderPages();
      });
      li.append(remove);
      ui.pages.append(li);
    }
    controls();
  }
  function addPage(role) {
    if (!selection?.revision || !Number.isInteger(selection.index)) return;
    selectedPages = selectedPages.filter((item) => !(item.revision_id === selection.revision && item.index === selection.index));
    if (selectedPages.length >= (capabilities?.max_pages || 12)) {
      error("Choose at most twelve pages for this extraction.");
      return;
    }
    selectedPages.push({ revision_id: selection.revision, index: selection.index, role });
    error("");
    renderPages();
  }
  function showSource(item) {
    window.dispatchEvent(new CustomEvent("heleos:show-source", { detail: item }));
  }
  async function saveReview(item, inputs, button) {
    if (pending || !activeRun) return;
    if (!ui.reviewer.value.trim() || !inputs.reason.value.trim()) {
      error("Enter your name and a reason for this review.");
      return;
    }
    pending = true;
    button.disabled = true;
    controls();
    error("");
    try {
      const run = await request("api/equipment/runs/" + activeRun.id + "/review", {
        finding_id: item.id, version: activeRun.review_version, state: inputs.state.value,
        tag: inputs.tag.value, reason: inputs.reason.value, actor: ui.reviewer.value,
      });
      renderRun(run);
      message("Review saved. Counts and conflicts have been recalculated.");
      window.dispatchEvent(new CustomEvent("heleos:equipment-updated"));
    } catch (err) { error(err.message); }
    finally { pending = false; button.disabled = false; controls(); }
  }
  function findingCard(item, run) {
    const card = text("div", "", "equipment-finding");
    const source = text("button", (item.role === "plan" ? "Plan" : "Schedule") + " · page " + (item.index + 1) + " ↗", "button button-quiet source-link");
    source.type = "button";
    source.title = pageName(item);
    source.addEventListener("click", () => showSource(item));
    card.append(source, text("p", item.source_text || "Equipment region added by reviewer", "source-excerpt"));
    const decision = run.decisions[item.id];
    const form = document.createElement("form");
    const inputs = {};
    for (const [key, label] of [["tag", "Tag"], ["state", "Review"], ["reason", "Reason"]]) {
      const wrapper = text("label", label, "field-label");
      const input = document.createElement(key === "state" ? "select" : "input");
      if (key === "state") {
        for (const [value, name] of [["pending", "Needs review"], ["include", item.role === "plan" ? "Use this plan reference" : "Use this schedule entry"], ["exclude", "Exclude this occurrence"]]) {
          const option = text("option", name); option.value = value; input.append(option);
        }
      } else {
        input.maxLength = key === "tag" ? 40 : 500;
        input.required = true;
      }
      input.value = decision?.[key] || (key === "tag" ? item.tag : key === "state" ? "pending" : "");
      if (key === "reason") input.placeholder = "What did you verify or correct?";
      wrapper.append(input); inputs[key] = input; form.append(wrapper);
    }
    const button = text("button", "Save review", "button button-outline");
    button.type = "submit";
    form.append(button);
    form.addEventListener("submit", (event) => { event.preventDefault(); void saveReview(item, inputs, button); });
    card.append(form);
    return card;
  }
  function renderRun(run) {
    activeRun = run;
    ui.results.hidden = run.state !== "completed";
    if (run.state === "failed" || run.state === "interrupted") {
      error(run.error || "Extraction did not finish.");
      message("No completed equipment result was produced.");
      controls();
      return;
    }
    if (run.state !== "completed") {
      message("Reading equipment · " + run.progress + " of " + run.total_pages + " pages processed…");
      return;
    }
    const included = run.rows.reduce((sum, row) => sum + (row.reviewed_quantity ?? 0), 0);
    const unknown = run.rows.filter((row) => row.reviewed_quantity === null).length;
    ui.summary.textContent = run.rows.length + " tags · " + included + " draft tag quantities · " + unknown + " unknown";
    ui.export.href = "api/equipment/runs/" + run.id + "/export.csv";
    ui.warnings.replaceChildren();
    for (const warning of run.warnings) {
      ui.warnings.append(text("p", pageName(warning.page) + ": " + warning.message, "equipment-warning"));
    }
    ui.rows.replaceChildren();
    if (!run.rows.length) {
      ui.rows.append(text("p", "No supported equipment tags were found. This does not establish zero equipment. Inspect the pages or add a missed item.", "equipment-warning"));
    }
    for (const row of run.rows) {
      const group = text("details", "", "equipment-row");
      const heading = text("summary", row.tag + " · draft tag quantity " + (row.reviewed_quantity ?? "UNKNOWN"));
      group.append(heading);
      const status = row.issues.length ? row.issues.map((issue) => issueNames[issue] || issue).join("; ") :
        row.pending ? "Plan and schedule tags match; review required." : "Plan and schedule reviewed.";
      group.append(text("p", status, "equipment-row-status"));
      for (const item of [...row.plan, ...row.schedule]) group.append(findingCard(item, run));
      ui.rows.append(group);
    }
    const excluded = [...run.findings, ...(run.manual_findings || [])].filter((item) => run.decisions[item.id]?.state === "exclude");
    if (excluded.length) {
      const group = text("details", "", "equipment-row");
      group.append(text("summary", excluded.length + " excluded occurrences"));
      for (const item of excluded) group.append(findingCard(item, run));
      ui.rows.append(group);
    }
    ui.audit.textContent = JSON.stringify({ extraction: run.extractor, sources: run.sources,
      review_version: run.review_version, changes: run.history }, null, 2);
    message("Equipment extraction saved. " + run.total_pages + " selected pages processed.");
    controls();
  }
  async function refreshHistory() {
    capabilities = await request("api/equipment");
    ui.reader.options[0].disabled = !capabilities.text_reader;
    ui.reader.options[1].disabled = !capabilities.vision_configured;
    if (!capabilities.text_reader && capabilities.vision_configured) ui.reader.value = "vision";
    ui["reader-status"].textContent = capabilities.text_reader
      ? "PDF text reader ready." + (capabilities.vision_configured ? " Local AI: " + capabilities.vision_model + "." : " Local AI is not configured yet.")
      : capabilities.vision_configured ? "Local AI: " + capabilities.vision_model : "A local reader must be configured to extract equipment.";
    ui.history.replaceChildren(text("option", "Choose a saved extraction"));
    ui.history.options[0].value = "";
    for (const run of capabilities.runs) {
      const option = text("option", new Date(run.created_at).toLocaleString() + " · " + run.mode + " · " + run.state);
      option.value = run.id; ui.history.append(option);
    }
    if (activeRun) ui.history.value = activeRun.id;
    controls();
  }
  async function poll(runId, sequence) {
    try {
      const run = await request("api/equipment/runs/" + runId);
      if (sequence !== requestSequence) return;
      renderRun(run);
      if (run.state === "queued" || run.state === "running") {
        timer = setTimeout(() => { void poll(runId, sequence); }, 1500);
      } else {
        pending = false;
        await refreshHistory();
        renderPages();
        window.dispatchEvent(new CustomEvent("heleos:equipment-updated"));
      }
    } catch (err) {
      if (sequence !== requestSequence) return;
      pending = false; error(err.message); controls();
    }
  }
  ui["add-plan"].addEventListener("click", () => addPage("plan"));
  ui["add-schedule"].addEventListener("click", () => addPage("schedule"));
  ui.reader.addEventListener("change", controls);
  ui.run.addEventListener("click", async () => {
    if (pending) return;
    pending = true; error(""); renderPages(); message("Starting equipment extraction…");
    clearTimeout(timer);
    const sequence = ++requestSequence;
    try {
      const run = await request("api/equipment/runs", { selections: selectedPages, mode: ui.reader.value });
      renderRun(run);
      void poll(run.id, sequence);
    } catch (err) { pending = false; error(err.message); controls(); }
  });
  async function openRun(runId) {
    if (!/^[a-f0-9]{32}$/.test(runId || "")) return;
    clearTimeout(timer);
    const sequence = ++requestSequence;
    error(""); pending = true; controls();
    try {
      const run = await request("api/equipment/runs/" + runId);
      selectedPages = run.sources.map(({ revision_id, index, role }) => ({ revision_id, index, role }));
      renderPages(); renderRun(run);
      void poll(run.id, sequence);
    } catch (err) { pending = false; error(err.message); controls(); }
  }
  ui.history.addEventListener("change", () => { void openRun(ui.history.value); });
  window.addEventListener("heleos:open-equipment-run", (event) => { void openRun(event.detail); });
  function cancelPlace() {
    placing = false; ui["cancel-place"].hidden = true;
    window.dispatchEvent(new CustomEvent("heleos:pick-region", { detail: false }));
  }
  ui.place.addEventListener("click", () => {
    if (!ui.reviewer.value.trim() || !ui["manual-tag"].value.trim() || !ui["manual-reason"].value.trim()) {
      error("Enter your name, the equipment tag and a reason first."); return;
    }
    if (!activeRun.sources.some((source) => source.revision_id === selection.revision && source.index === selection.index)) {
      error("Open a page used in this extraction before placing the missing item."); return;
    }
    error(""); placing = true; ui["cancel-place"].hidden = false;
    message("Drag a box around the missing equipment on the drawing.");
    window.dispatchEvent(new CustomEvent("heleos:pick-region", { detail: true }));
  });
  ui["cancel-place"].addEventListener("click", cancelPlace);
  window.addEventListener("heleos:region-picked", async (event) => {
    if (!placing || !activeRun || pending) return;
    cancelPlace(); pending = true; controls();
    try {
      const run = await request("api/equipment/runs/" + activeRun.id + "/add", {
        ...event.detail, version: activeRun.review_version, tag: ui["manual-tag"].value,
        reason: ui["manual-reason"].value, actor: ui.reviewer.value,
      });
      renderRun(run); message("Missing equipment added with its drawing location.");
      window.dispatchEvent(new CustomEvent("heleos:equipment-updated"));
    } catch (err) { error(err.message); }
    finally { pending = false; controls(); }
  });
  window.addEventListener("heleos:selection", (event) => {
    if (placing) cancelPlace();
    selection = event.detail; renderPages();
  });
  window.addEventListener("heleos:workflow-stage", (event) => {
    if (event.detail !== "equipment" && placing) cancelPlace();
  });
  window.addEventListener("pagehide", () => { clearTimeout(timer); requestSequence += 1; });
  window.dispatchEvent(new CustomEvent("heleos:request-selection"));
  void refreshHistory().catch((err) => error(err.message));
})();
