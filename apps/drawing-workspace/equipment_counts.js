"use strict";

// The server owns deterministic quantities. Edits are source-bound atomic
// review drafts; this panel never accepts or derives a final equipment total.
((root, factory) => {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.HeleosEquipmentCounts = api;
})(typeof globalThis === "object" ? globalThis : this, () => {
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const list = (value) => Array.isArray(value) ? value : [];
  const label = (value) => String(value || "Unknown").replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());
  const count = (value) => Number.isSafeInteger(value) && value >= 0 ? value + " each" : "Unknown";
  const statuses = ["new", "existing", "demolition", "relocation", "spare", "unknown"];
  const physicalRoles = ["plan", "enlarged_plan", "detail", "riser"];
  function createPanel(env) {
    let draft = null, picking = null, coverageControl = null;
    const data = () => env.getView()?.equipment_count_takeoff || {};
    const sourcePin = () => JSON.stringify(data().current_source_contexts || []);
    const stale = () => draft && (draft.generation !== data().generation || draft.sources !== sourcePin());
    function node(tag, value = "", className = "") {
      const element = env.document.createElement(tag); element.textContent = value; element.className = className; return element;
    }
    function button(title, action, disabled = false) {
      const element = node("button", title, "button button-outline"); element.type = "button";
      element.disabled = disabled || Boolean(env.isBusy());
      element.addEventListener("click", async () => {
        if (element.disabled || env.isBusy()) return;
        try { await action(); } catch (error) { env.error(error.message); }
      });
      return element;
    }
    function detail(target, title, open = false) {
      const element = node("details", "", "workflow-record"); element.open = open;
      element.append(node("summary", title)); target.append(element); return element;
    }
    function field(target, title, name, value, change, options) {
      const wrapper = node("label", title, "field-label"), input = node(options ? "select" : "input");
      input.name = name; input.maxLength = 1000;
      if (options) for (const option of options) {
        const pair = Array.isArray(option) ? option : [option, label(option)], element = node("option", pair[1]);
        element.value = pair[0]; input.append(element);
      }
      input.value = value ?? "";
      input.addEventListener(options ? "change" : "input", () => change(input.value));
      wrapper.append(input); target.append(wrapper); return input;
    }
    function choices(target, title, name, options, selected, change) {
      const group = node("fieldset"); group.append(node("legend", title));
      for (const [value, title] of options) {
        const wrapper = node("label", title, "field-label"), input = node("input");
        input.type = "checkbox"; input.name = name; input.value = value; input.checked = selected.includes(value);
        input.addEventListener("change", () => {
          const values = input.checked ? [...selected.filter((item) => item !== value), value] : selected.filter((item) => item !== value);
          selected.splice(0, selected.length, ...values); change(selected);
        }); wrapper.append(input); group.append(wrapper);
      }
      target.append(group);
    }
    function invalidateCoverage() {
      if (draft?.request.coverage) {
        draft.request.coverage.state = "unknown";
        if (coverageControl) coverageControl.value = "unknown";
      }
    }
    function selectedContext() {
      const selected = env.getSelection();
      return list(data().current_source_contexts).find((context) => context.source.revision_id === selected?.revision && context.source.index === selected?.index);
    }
    function admitContext(context) {
      const request = draft.request;
      request.sources ||= [];
      if (!request.sources.some((item) => JSON.stringify(item.source) === JSON.stringify(context.source)))
        request.sources.push({ source: clone(context.source), role: context.role, artifact_sha256: context.artifact_sha256 });
      if (!request.scope.source_keys.includes(context.source_key)) request.scope.source_keys.push(context.source_key);
    }
    const sourceIdentity = (source) => JSON.stringify([source?.revision_id, source?.index, source?.sheet_id, source?.geometry_fingerprint]);
    function unavailableContexts() {
      return list(draft?.request.sources).filter((context) => !list(data().current_source_contexts).some((current) =>
        sourceIdentity(current.source) === sourceIdentity(context.source) && current.role === context.role && current.artifact_sha256 === context.artifact_sha256));
    }
    function replaceStaleSources() {
      if (!draft || stale()) throw new Error("The source changed again. Reopen the current equipment review before replacing evidence.");
      const unavailable = unavailableContexts();
      if (!unavailable.length) return;
      cancelRegion();
      const request = draft.request, removedSources = new Set(unavailable.map((context) => sourceIdentity(context.source)));
      const removedEvidence = new Set(list(request.evidence).filter((entry) => removedSources.has(sourceIdentity(entry.source))).map((entry) => entry.id));
      const removedObservations = new Set(list(request.observations).filter((entry) => removedSources.has(sourceIdentity(entry.source)) ||
        list(entry.evidence_ids).some((id) => removedEvidence.has(id))).map((entry) => entry.id));
      let changed = true;
      while (changed) {
        changed = false;
        for (const item of list(request.observations)) if (removedObservations.has(item.parent_id) && !removedObservations.has(item.id)) {
          removedObservations.add(item.id); changed = true;
        }
      }
      const affected = (entry) => list(entry.member_ids).some((id) => removedObservations.has(id)) || list(entry.evidence_ids).some((id) => removedEvidence.has(id));
      const removedRelations = list(request.relations).filter(affected), removedSchedules = list(request.schedules).filter(affected);
      const pending = request.coverage.unresolved_requirements;
      function unresolved(message) { if (!pending.includes(message)) pending.push(message); }
      for (const context of unavailable) unresolved("Review replacement source page " + (context.source.index + 1) + " from " + context.source.revision_id + ": previous source evidence was retired");
      for (const id of removedObservations) unresolved("Re-establish source instance " + id + " after source replacement");
      for (const entry of removedRelations) unresolved("Resolve source relationship " + entry.id + " (" + entry.kind + ") after source replacement");
      for (const entry of removedSchedules) unresolved("Resolve schedule requirement " + entry.id + " (" + entry.family + ", declared " + entry.declared_each + ") after source replacement");
      request.sources = request.sources.filter((context) => !removedSources.has(sourceIdentity(context.source)));
      request.evidence = list(request.evidence).filter((entry) => !removedEvidence.has(entry.id));
      request.observations = list(request.observations).filter((entry) => !removedObservations.has(entry.id));
      request.relations = list(request.relations).filter((entry) => !affected(entry));
      request.schedules = list(request.schedules).filter((entry) => !affected(entry));
      request.coverage.evidence_ids = request.coverage.evidence_ids.filter((id) => !removedEvidence.has(id));
      const retainedKeys = new Set(list(data().current_source_contexts).filter((current) => request.sources.some((context) =>
        sourceIdentity(current.source) === sourceIdentity(context.source) && current.role === context.role)).map((context) => context.source_key));
      request.scope.source_keys = request.scope.source_keys.filter((key) => retainedKeys.has(key));
      for (const previous of unavailable) {
        const replacement = list(data().current_source_contexts).find((context) => context.source.revision_id === previous.source.revision_id && context.source.index === previous.source.index);
        if (replacement) admitContext(replacement);
      }
      request.coverage.state = "unknown";
      env.render();
    }
    function cancelRegion() { if (picking) env.pickRegion?.(false); picking = null; }
    function startRegion(kind) {
      if (!draft || stale()) throw new Error("Reopen the current equipment review before adding source evidence.");
      const context = selectedContext();
      if (!context || (kind === "instance" && !physicalRoles.includes(context.role)))
        throw new Error("Open a current plan, detail or riser to review a physical source instance.");
      if (!env.pickRegion) throw new Error("Drawing region selection is unavailable.");
      picking = { kind, context: clone(context) }; env.pickRegion(true); env.render();
    }
    async function regionPicked(value) {
      if (!picking) return false;
      const capture = picking; cancelRegion();
      try {
        if (!draft || stale()) throw new Error("The source changed. Reopen the current equipment review.");
        if (value?.revision_id !== capture.context.source.revision_id || value?.index !== capture.context.source.index)
          throw new Error("The selected region belongs to another source page. Select the region again.");
        const box = value.bbox;
        if (!Array.isArray(box) || box.length !== 4 || !box.every((n) => Number.isFinite(n) && n >= 0 && n <= 1) || box[0] >= box[2] || box[1] >= box[3])
          throw new Error("Select a nonempty source region inside the drawing.");
        const request = draft.request, context = capture.context;
        request.sources ||= []; request.evidence ||= []; request.observations ||= [];
        admitContext(context);
        const evidence = { id: env.newId(), source: clone(context.source), bbox: clone(box),
          kind: capture.kind === "instance" ? "graphic" : context.role === "schedule" ? "schedule" : "requirement",
          text: "", artifact_sha256: context.artifact_sha256 };
        request.evidence.push(evidence);
        if (capture.kind === "instance") request.observations.push({ id: env.newId(), source: clone(context.source), bbox: clone(box),
          kind: "assembly", family: "", tag: null, work_status: "unknown", disposition: "unresolved", identity: "unresolved",
          procurement: "unknown", installation: "unknown", parent_id: null, system_id: null, attributes: {}, evidence_ids: [evidence.id], issues: [] });
        invalidateCoverage(); env.render();
      } catch (error) { env.error(error.message); env.render(); }
      return true;
    }
    function showSource(target, item, title) {
      if (item.source) target.append(button(title, () => env.showSource({ ...clone(item.source), bbox: clone(item.bbox) })));
    }
    function renderResult(target) {
      const current = data(), result = current.result;
      target.append(node("strong", "Selected scope total: " + (!current.stale && result?.complete ? count(result.total_each) : "Unknown · incomplete")),
        node("p", "Known physical subtotal: " + count(result?.known_subtotal_each)));
      if (current.stale) target.append(node("p", "Source dependencies changed. Earlier results remain in history; review current evidence to recalculate.", "error"));
      for (const issue of [...list(current.issues), ...list(result?.issues)]) target.append(node("p", typeof issue === "string" ? label(issue) : issue.message || label(issue.code), "error"));
      for (const group of list(result?.groups)) {
        const entry = detail(target, "Equipment group · " + (group.key?.family || "Unknown family") + " · " + label(group.key?.work_status), true);
        entry.append(node("p", "Known subtotal: " + count(group.known_subtotal_each) + " · Group final: " + (group.complete ? count(group.total_each) : "Unknown")));
        for (const issue of list(group.issues)) entry.append(node("p", label(issue), "error"));
      }
      for (const row of list(result?.rows)) {
        const entry = detail(target, (row.tag || row.family || "Equipment") + " · " + label(row.kind) + " · " + label(row.work_status));
        for (const [key, title] of [["physical_each", "Physical assemblies"], ["component_each", "Components"], ["procurement_each", "Procurement"],
          ["installation_each", "Installation"], ["remove_each", "Removal"], ["reinstall_each", "Reinstallation"]]) entry.append(node("p", title + ": " + count(row[key])));
        for (const issue of list(row.issues)) entry.append(node("p", label(issue), "error"));
      }
      for (const declaration of list(result?.declarations)) {
        const entry = detail(target, "Schedule declaration · " + declaration.id);
        entry.append(node("p", "Observed: " + count(declaration.observed_each) + " · Declared: " + count(declaration.declared_each)),
          node("p", "Schedule quantities do not create physical instances."));
        for (const issue of list(declaration.issues)) entry.append(node("p", label(issue), "error"));
      }
      if (result) target.append(node("p", "Procurement packages: " + count(result.procurement_packages)));
      for (const system of list(result?.systems)) {
        const entry = detail(target, "System · " + system.id);
        entry.append(node("p", "Physical assemblies: " + count(system.physical_each)), node("p", "Named instances: " + list(system.member_ids).join(", ")));
      }
      for (const observation of list(current.request?.observations)) {
        const entry = detail(target, "Source instance · " + (observation.tag || observation.id));
        entry.append(node("p", label(observation.disposition) + " · Identity " + label(observation.identity)));
        showSource(entry, observation, "Show source · " + (observation.tag || observation.id));
      }
      const history = detail(target, "Equipment calculation history · " + list(current.history).length);
      for (const saved of list(current.history).slice().reverse()) history.append(node("p", (saved.reason || "Saved source review") + " · " +
        (saved.actor || "Recorded reviewer") + " · " + (saved.result?.complete ? count(saved.result.total_each) : "Final unknown") +
        " · Known subtotal " + count(saved.result?.known_subtotal_each)));
    }
    function observationEditor(target, item) {
      const entry = detail(target, "Review instance · " + (item.tag || item.id), !item.family);
      showSource(entry, item, "Show review source · " + (item.tag || item.id));
      const update = (key) => (value) => { item[key] = ["tag", "parent_id", "system_id"].includes(key) ? value.trim() || null : value; invalidateCoverage(); };
      for (const [key, title, options] of [["family", "Equipment family"], ["tag", "Tag (optional)"],
        ["kind", "Assembly level", ["assembly", "component", "shipping", "accessory", "spare"]],
        ["work_status", "Work scope", statuses], ["disposition", "Occurrence review", ["include", "exclude", "unresolved"]],
        ["identity", "Physical identity", ["established", "unresolved"]], ["procurement", "Procurement responsibility", ["separate", "included", "none", "unknown"]],
        ["installation", "Installation responsibility", ["field", "factory", "none", "unknown"]],
        ["parent_id", "Included in parent assembly", [["", "No parent"], ...list(draft.request.observations).filter((candidate) => candidate.id !== item.id).map((candidate) => [candidate.id, candidate.tag || candidate.id])]],
        ["system_id", "System identity (optional)"]]) field(entry, title, "equipment-count-" + item.id + "-" + key, item[key], update(key), options);
      const attributes = detail(entry, "Source attributes");
      for (const key of Object.keys(item.attributes)) field(attributes, key, "equipment-count-" + item.id + "-attribute-" + key, item.attributes[key],
        (value) => { item.attributes[key] = value; invalidateCoverage(); });
      const pending = draft.attributes[item.id] ||= { name: "", value: "" };
      field(attributes, "Attribute name", "equipment-count-" + item.id + "-attribute-name", pending.name, (value) => { pending.name = value; });
      field(attributes, "Original value and units", "equipment-count-" + item.id + "-attribute-value", pending.value, (value) => { pending.value = value; });
      attributes.append(button("Add attribute · " + item.id, () => {
        if (!pending.name.trim() || !pending.value.trim() || ["__proto__", "constructor", "prototype"].includes(pending.name.trim())) throw new Error("Enter an attribute name and its source value.");
        item.attributes[pending.name.trim()] = pending.value.trim(); pending.name = ""; pending.value = ""; invalidateCoverage(); env.render();
      }));
      choices(entry, "Supporting source evidence", "equipment-count-" + item.id + "-evidence", list(draft.request.evidence).map((evidence) => [evidence.id, label(evidence.kind) + " · " + (evidence.text || evidence.id)]),
        item.evidence_ids, invalidateCoverage);
    }
    function relationshipEditor(target) {
      const request = draft.request, parent = detail(target, "Assembly relationships and schedule requirements");
      parent.append(node("p", "Relationships require selected original-source evidence. Multiplicity is the total including the representative. Packages, systems and physical assemblies remain separate."));
      parent.append(button("Add relationship", () => {
        request.relations ||= []; request.relations.push({ id: env.newId(), kind: "same", member_ids: [], each: null, evidence_ids: [], scope_text: "" });
        invalidateCoverage(); env.render();
      }), button("Add schedule declaration", () => {
        request.schedules ||= []; request.schedules.push({ id: env.newId(), family: "", work_status: "unknown", member_ids: [], declared_each: null, evidence_ids: [] });
        invalidateCoverage(); env.render();
      }));
      const members = list(request.observations).map((item) => [item.id, (item.tag || item.id) + " · " + (item.family || "Unknown family")]);
      const evidence = list(request.evidence).map((item) => [item.id, label(item.kind) + " · " + (item.text || item.id)]);
      for (const relation of list(request.relations)) {
        const entry = detail(parent, "Source relationship · " + relation.id, true), prefix = "equipment-count-relation-" + relation.id;
        field(entry, "Relationship", prefix + "-kind", relation.kind, (value) => {
          relation.kind = value; relation.each = null; delete draft.numbers[relation.id]; invalidateCoverage(); env.render();
        }, ["same", "distinct", "multiplicity", "relocation", "system", "package", "tag_reuse"]);
        if (["multiplicity", "package"].includes(relation.kind)) field(entry, relation.kind === "multiplicity" ? "Explicit total including representative" : "Explicit procurement package count",
          prefix + "-each", draft.numbers[relation.id] ?? relation.each, (value) => { draft.numbers[relation.id] = value; invalidateCoverage(); });
        field(entry, "Applicable source scope", prefix + "-scope", relation.scope_text, (value) => { relation.scope_text = value; invalidateCoverage(); });
        choices(entry, "Named source instances", prefix + "-members", members, relation.member_ids, invalidateCoverage);
        choices(entry, "Relationship source evidence", prefix + "-evidence", evidence, relation.evidence_ids, invalidateCoverage);
        entry.append(button("Remove relationship · " + relation.id, () => { request.relations = request.relations.filter((item) => item !== relation); invalidateCoverage(); env.render(); }));
      }
      for (const schedule of list(request.schedules)) {
        const entry = detail(parent, "Schedule requirement · " + schedule.id, true), prefix = "equipment-count-schedule-" + schedule.id;
        field(entry, "Equipment family", prefix + "-family", schedule.family, (value) => { schedule.family = value; invalidateCoverage(); });
        field(entry, "Work scope", prefix + "-status", schedule.work_status, (value) => { schedule.work_status = value; invalidateCoverage(); }, statuses);
        field(entry, "Schedule-declared quantity", prefix + "-each", draft.numbers[schedule.id] ?? schedule.declared_each,
          (value) => { draft.numbers[schedule.id] = value; invalidateCoverage(); });
        entry.append(node("p", "Leave instances unselected for a schedule-only outstanding requirement; this declaration does not create physical equipment."));
        choices(entry, "Corresponding physical instances", prefix + "-members", members, schedule.member_ids, invalidateCoverage);
        choices(entry, "Schedule source evidence", prefix + "-evidence", list(request.evidence).filter((item) => item.kind === "schedule").map((item) => [item.id, item.text || item.id]), schedule.evidence_ids, invalidateCoverage);
        entry.append(button("Remove schedule requirement · " + schedule.id, () => { request.schedules = request.schedules.filter((item) => item !== schedule); invalidateCoverage(); env.render(); }));
      }
    }
    function editor(target) {
      const request = draft.request;
      if (stale()) target.append(node("p", "The equipment sources or results changed. Your draft is retained; discard it and reopen the current review before saving.", "error"));
      const unavailable = unavailableContexts();
      if (unavailable.length) {
        const repair = detail(target, "Changed source evidence · " + unavailable.length, true);
        repair.append(node("p", "Replace evidence from unavailable or reassigned pages, then redraw the affected source regions. Unrelated instances and saved history remain. Removed instance, relationship and schedule obligations stay unresolved until reviewed."));
        for (const context of unavailable) repair.append(node("p", label(context.role) + " · page " + (context.source.index + 1) + " · " + context.source.revision_id));
        repair.append(button("Replace stale source evidence", replaceStaleSources, stale()));
      }
      const selected = selectedContext();
      target.append(node("p", "Review source instances, attributes and obligations. Changing a source instance resets drawing coverage to unknown."));
      target.append(button("Add source instance", () => startRegion("instance"), stale() || !selected || !physicalRoles.includes(selected.role)),
        button("Add supporting source evidence", () => startRegion("evidence"), stale() || !selected));
      if (picking) target.append(node("p", "Drag a box around the supporting source region on the drawing."), button("Cancel equipment region", () => { cancelRegion(); env.render(); }));
      for (const item of list(request.observations)) observationEditor(target, item);
      const evidencePanel = detail(target, "Source evidence · " + list(request.evidence).length, list(request.evidence).some((item) => !item.text));
      for (const evidence of list(request.evidence)) {
        const entry = detail(evidencePanel, evidence.text || evidence.id, !evidence.text);
        showSource(entry, evidence, "Show evidence · " + evidence.id);
        field(entry, "Evidence kind", "equipment-count-evidence-" + evidence.id + "-kind", evidence.kind, (value) => { evidence.kind = value; invalidateCoverage(); },
          ["graphic", "requirement", "schedule", "relationship", "coverage", "tag"]);
        field(entry, "Source excerpt or review description", "equipment-count-evidence-" + evidence.id + "-text", evidence.text, (value) => { evidence.text = value; invalidateCoverage(); });
      }
      relationshipEditor(target);
      if (request.scope && request.coverage) {
        const scope = detail(target, "Selected scope and drawing coverage", true);
        choices(scope, "Current source pages", "equipment-count-source-scope", list(data().current_source_contexts).map((context) => [context.source_key,
          label(context.role) + " · page " + (context.source.index + 1)]), request.scope.source_keys, (keys) => {
            for (const context of list(data().current_source_contexts).filter((item) => keys.includes(item.source_key))) admitContext(context);
            invalidateCoverage();
          });
        choices(scope, "Requested work statuses", "equipment-count-status-scope", statuses.map((status) => [status, label(status)]), request.scope.work_statuses, invalidateCoverage);
        field(scope, "Required grouping attributes (comma separated)", "equipment-count-required-attributes", request.scope.required_attributes.join(", "),
          (value) => { request.scope.required_attributes = value.split(",").map((part) => part.trim()).filter(Boolean); invalidateCoverage(); });
        scope.append(node("p", "An empty detector response never establishes zero. Complete coverage requires review of the entire selected scope and unresolved requirements."));
        field(scope, "Selected-page coverage description", "equipment-count-coverage-description", draft.coverageDescription,
          (value) => { draft.coverageDescription = value; });
        scope.append(button("Record selected page coverage", () => {
          if (stale()) throw new Error("Source changed. Reopen the current equipment review.");
          const context = selectedContext();
          if (!context || !draft.coverageDescription.trim()) throw new Error("Open a current page and describe the full-page coverage you reviewed.");
          admitContext(context); request.evidence ||= [];
          const evidence = { id: env.newId(), source: clone(context.source), bbox: [0, 0, 1, 1], kind: "coverage",
            text: draft.coverageDescription.trim(), artifact_sha256: context.artifact_sha256 };
          request.evidence.push(evidence); request.coverage.evidence_ids.push(evidence.id); request.coverage.state = "unknown";
          draft.coverageDescription = ""; env.render();
        }, stale() || !selected));
        coverageControl = field(scope, "Drawing coverage review", "equipment-count-coverage", request.coverage.state, (value) => { request.coverage.state = value; }, ["unknown", "complete"]);
        choices(scope, "Coverage supporting evidence", "equipment-count-coverage-evidence", list(request.evidence).map((evidence) => [evidence.id, evidence.text || evidence.id]), request.coverage.evidence_ids, () => {});
        field(scope, "Unresolved requirements (one per semicolon)", "equipment-count-unresolved", request.coverage.unresolved_requirements.join("; "),
          (value) => { request.coverage.unresolved_requirements = value.split(";").map((part) => part.trim()).filter(Boolean); });
      }
      field(target, "Review reason", "equipment-count-reason", draft.reason, (value) => { draft.reason = value; });
      target.append(button("Save equipment review", save, stale()), button("Discard equipment draft", () => { cancelRegion(); draft = null; env.render(); }));
    }
    async function save() {
      if (!draft || stale()) throw new Error("Equipment results changed. Reopen the current review before saving.");
      if (!draft.reason.trim()) throw new Error("Enter a reason describing the source evidence you reviewed.");
      if (!env.getActor()?.trim()) throw new Error("Enter your reviewer name before saving equipment review.");
      const request = clone(draft.request);
      if (list(request.evidence).some((entry) => !entry.text?.trim())) throw new Error("Enter a source excerpt or evidence description for each reviewed region.");
      if (list(request.observations).some((entry) => !entry.family?.trim())) throw new Error("Enter the supported equipment family for each source instance.");
      function integer(raw, positive) {
        if (!/^[0-9]+$/.test(String(raw ?? "")) || !Number.isSafeInteger(Number(raw)) || Number(raw) < (positive ? 1 : 0))
          throw new Error(positive ? "An explicit multiplier or package count must be a positive integer." : "A schedule quantity must be a nonnegative integer.");
        return Number(raw);
      }
      for (const relation of list(request.relations)) relation.each = ["multiplicity", "package"].includes(relation.kind) ?
        integer(draft.numbers[relation.id] ?? relation.each, true) : null;
      for (const schedule of list(request.schedules)) schedule.declared_each = integer(draft.numbers[schedule.id] ?? schedule.declared_each, false);
      const saved = await env.save("equipment_count_save", { generation: draft.generation, request }, draft.reason.trim());
      if (saved) { cancelRegion(); draft = null; env.render(); }
    }
    function render(target) {
      coverageControl = null;
      target.append(node("h3", "Reviewed physical equipment"), node("p", "Physical equipment quantity is unknown until source instances and selected drawing coverage are reviewed. Draft tags and schedules supply context; they do not establish physical assemblies."));
      renderResult(target);
      if (!draft) target.append(button("Edit equipment review", () => {
        draft = { generation: data().generation, sources: sourcePin(), request: clone(data().request || {}), reason: "", attributes: {}, numbers: {}, coverageDescription: "" }; env.render();
      }));
      else editor(target);
    }
    return { render, regionPicked, cancelRegion };
  }
  return { createPanel };
});
