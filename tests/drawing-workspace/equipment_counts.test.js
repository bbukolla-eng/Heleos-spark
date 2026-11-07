"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const filename = path.resolve(__dirname, "../../apps/drawing-workspace/equipment_counts.js");
const equipment = fs.existsSync(filename) ? require(filename) : {};
const copy = (value) => JSON.parse(JSON.stringify(value));
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.textContent = ""; this.value = ""; this.checked = false; this.listeners = {}; }
  append(...elements) { this.children.push(...elements); }
  addEventListener(name, action) { this.listeners[name] = action; }
  async fire(name) { await this.listeners[name]?.({ target: this }); }
}
const all = (root) => [root, ...root.children.flatMap(all)];
const text = (root) => [root.textContent, ...root.children.map(text)].join(" ");
const source = { revision_id: "revision-a", index: 0, sheet_id: "sheet-a", geometry_fingerprint: "geometry-a" };
function fixture(overrides = {}) {
  const view = { equipment_count_takeoff: { available: false, generation: null, stale: false,
    current_source_contexts: [{ source, role: "plan", artifact_sha256: "artifact-a", source_key: "source-a" }],
    request: {}, result: null, observations: [], history: [], issues: [], ...overrides } };
  const root = new Element("main"), calls = [], errors = [], sources = [], picks = [];
  let nextId = 0, actor = "Estimator", busy = false, saveError = null;
  assert.equal(typeof equipment.createPanel, "function", "physical equipment panel must exist");
  const panel = equipment.createPanel({ document: { createElement: (tag) => new Element(tag) },
    getView: () => view, getSelection: () => ({ revision: "revision-a", index: 0 }), isBusy: () => busy,
    getActor: () => actor, newId: () => "eq-" + ++nextId, pickRegion: (value) => picks.push(value),
    showSource: (value) => sources.push(copy(value)), error: (value) => errors.push(value),
    save: async (...args) => { if (saveError) throw new Error(saveError); calls.push(copy(args)); return true; }, render: () => render() });
  function render() { root.children = []; panel.render(root); }
  function button(title) { return all(root).find((item) => item.tagName === "button" && item.textContent === title); }
  function input(name) { return all(root).find((item) => item.name === name); }
  async function click(title) { const control = button(title); assert.ok(control, title); await control.fire("click"); }
  async function change(name, value) { const control = input(name); assert.ok(control, name); control.value = value; await control.fire(control.tagName === "select" ? "change" : "input"); }
  render();
  return { view, root, panel, calls, errors, sources, picks, render, button, input, click, change,
    setActor(value) { actor = value; }, setBusy(value) { busy = value; }, setSaveError(value) { saveError = value; } };
}

test("unread equipment remains unknown without rendering writes or editable final totals", () => {
  const f = fixture();
  assert.match(text(f.root), /unknown/i);
  assert.equal(f.calls.length, 0);
  assert.equal(all(f.root).some((item) => /final.*total|total.*each/.test(item.name || "")), false);
});

test("atomic review preserves request and uses the generation when editing began", async () => {
  const f = fixture({ generation: "generation-a", request: { observations: [], evidence: [], coverage: { state: "unknown" } } });
  const before = copy(f.view);
  await f.click("Edit equipment review");
  await f.change("equipment-count-reason", "Verified source instances and scope");
  await f.click("Save equipment review");
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0][0], "equipment_count_save");
  assert.equal(f.calls[0][1].generation, "generation-a");
  assert.deepEqual(f.calls[0][1].request, before.equipment_count_takeoff.request);
  assert.deepEqual(f.view, before);
});

test("changed generation retains unfinished draft and disables stale overwrite", async () => {
  const f = fixture({ generation: "generation-a", request: { observations: [], evidence: [] } });
  await f.click("Edit equipment review");
  await f.change("equipment-count-reason", "Unfinished source verification");
  f.view.equipment_count_takeoff.generation = "generation-b";
  f.render();
  assert.equal(f.input("equipment-count-reason").value, "Unfinished source verification");
  assert.equal(f.button("Save equipment review").disabled, true);
  await f.click("Save equipment review");
  assert.equal(f.calls.length, 0);
  assert.match(text(f.root), /changed|reopen/i);
});

test("review requires actor and evidence reason without sending an empty mutation", async () => {
  const f = fixture();
  await f.click("Edit equipment review");
  await f.click("Save equipment review");
  assert.equal(f.calls.length, 0);
  assert.match(f.errors.at(-1), /reason|source evidence/i);
  await f.change("equipment-count-reason", "Checked source graphic");
  f.setActor("");
  await f.click("Save equipment review");
  assert.equal(f.calls.length, 0);
  assert.match(f.errors.at(-1), /reviewer|name/i);
});

function emptyRequest() {
  return { schema: "equipment-calculation-request-1", binding: { rule_sha256: "approved-rules" }, sources: [], evidence: [], observations: [], relations: [], schedules: [],
    scope: { source_keys: [], work_statuses: ["new", "existing", "demolition", "relocation", "spare", "unknown"], required_attributes: [] },
    coverage: { state: "unknown", evidence_ids: [], unresolved_requirements: [] } };
}
const observation = (id, family, extra = {}) => ({ id, source, bbox: [0.1, 0.1, 0.2, 0.2], kind: "assembly", family,
  tag: id, work_status: "new", disposition: "include", identity: "established", procurement: "separate", installation: "field",
  parent_id: null, system_id: null, attributes: {}, evidence_ids: ["graphic"], issues: [], ...extra });
function populated() {
  const request = emptyRequest(); request.sources = [{ source, role: "plan", artifact_sha256: "artifact-a" }];
  request.scope.source_keys = ["source-a"];
  request.evidence = [{ id: "graphic", source, bbox: [0.1, 0.1, 0.2, 0.2], kind: "graphic", text: "Reviewed original symbol", artifact_sha256: "artifact-a" }];
  request.observations = [observation("P-1", "pump"), observation("P-2", "pump"), observation("P-3", "pump"), observation("AHU-1", "air_handler")];
  return request;
}

test("source-region admission binds exact page geometry and requires supported physical source", async () => {
  const f = fixture({ request: emptyRequest() });
  await f.click("Edit equipment review"); await f.click("Add source instance");
  assert.deepEqual(f.picks, [true]);
  await f.panel.regionPicked({ revision_id: "revision-a", index: 0, bbox: [0.2, 0.3, 0.4, 0.5] });
  const family = all(f.root).find((item) => item.name?.endsWith("-family"));
  assert.ok(family); await f.change(family.name, "pump");
  await f.change("equipment-count-evidence-eq-1-text", "Pump symbol on original plan");
  await f.change("equipment-count-reason", "Checked physical pump and source graphic");
  await f.click("Save equipment review");
  const request = f.calls[0][1].request;
  assert.equal(request.observations.length, 1);
  assert.deepEqual(request.observations[0].source, source);
  assert.deepEqual(request.observations[0].bbox, [0.2, 0.3, 0.4, 0.5]);
  assert.equal(request.observations[0].identity, "unresolved");
  assert.equal(request.observations[0].procurement, "unknown");
  assert.deepEqual(request.scope.source_keys, ["source-a"]);
  assert.equal(request.evidence[0].kind, "graphic");
});

test("region pick from another page cannot be admitted into pinned source", async () => {
  const f = fixture({ request: emptyRequest() });
  await f.click("Edit equipment review"); await f.click("Add source instance");
  await f.panel.regionPicked({ revision_id: "revision-b", index: 0, bbox: [0.2, 0.3, 0.4, 0.5] });
  assert.match(f.errors.at(-1), /source|page/i);
  await f.change("equipment-count-reason", "No admission"); await f.click("Save equipment review");
  assert.equal(f.calls[0][1].request.observations.length, 0);
});

test("EC20 exclusion changes only reviewed draft and preserves prior calculation history", async () => {
  const request = populated(); const before = copy(request);
  const f = fixture({ generation: "generation-a", request, result: { complete: true, total_each: 4, known_subtotal_each: 4, rows: [], groups: [], declarations: [] },
    history: [{ actor: "Estimator", reason: "Before false-positive correction", result: { complete: true, total_each: 4, known_subtotal_each: 4 } }] });
  await f.click("Edit equipment review");
  await f.change("equipment-count-P-3-disposition", "exclude");
  await f.change("equipment-count-reason", "Source confirms third pump was a false positive");
  await f.click("Save equipment review");
  assert.equal(f.calls[0][1].request.observations[2].disposition, "exclude");
  assert.deepEqual(f.calls[0][1].request.observations[3], before.observations[3]);
  assert.deepEqual(request, before);
  assert.match(text(f.root), /Before false-positive correction/);
});

test("included loose starter retains distinct channels and source navigation", async () => {
  const request = populated(); request.observations = [observation("CH-1", "chiller"), observation("START-1", "starter", { kind: "component", parent_id: "CH-1", procurement: "included" })];
  const f = fixture({ request, available: true, result: { complete: true, total_each: 1, known_subtotal_each: 1, groups: [], declarations: [],
    rows: [{ row_id: "starter", member_ids: ["START-1"], family: "starter", tag: "START-1", kind: "component", work_status: "new", physical_each: 0,
      component_each: 1, procurement_each: 0, installation_each: 1, remove_each: 0, reinstall_each: 0, issues: [] }] } });
  assert.match(text(f.root), /Component.*1 each/);
  assert.match(text(f.root), /Procurement.*0 each/);
  assert.match(text(f.root), /Installation.*1 each/);
  await f.click("Show source · CH-1");
  assert.deepEqual(f.sources[0], { ...source, bbox: request.observations[0].bbox });
});

test("zero requires saved complete result and coverage is an explicit draft decision", async () => {
  const request = emptyRequest(); const f = fixture({ request, result: { complete: false, total_each: null, known_subtotal_each: 0, rows: [], groups: [], declarations: [] } });
  assert.match(text(f.root), /Selected scope total: Unknown/);
  await f.click("Edit equipment review"); await f.change("equipment-count-coverage", "complete");
  await f.change("equipment-count-reason", "Reviewed selected empty scope"); await f.click("Save equipment review");
  assert.equal(f.calls[0][1].request.coverage.state, "complete");
  assert.match(text(f.root), /Selected scope total: Unknown/);
  f.view.equipment_count_takeoff.result = { complete: true, total_each: 0, known_subtotal_each: 0, rows: [], groups: [], declarations: [] };
  f.render(); assert.match(text(f.root), /Selected scope total: 0 each/);
});

async function check(f, name, value, checked = true) {
  const input = all(f.root).find((item) => item.name === name && item.value === value);
  assert.ok(input, name + "=" + value); input.checked = checked; await input.fire("change");
}
test("multiplicity requires explicit positive integer and selected source evidence without entering final total", async () => {
  const f = fixture({ generation: "generation-a", request: populated() });
  await f.click("Edit equipment review"); await f.click("Add relationship");
  await f.change("equipment-count-relation-eq-1-kind", "multiplicity");
  await f.change("equipment-count-relation-eq-1-each", "3");
  await f.change("equipment-count-relation-eq-1-scope", "Three total including representative on level 1");
  await check(f, "equipment-count-relation-eq-1-members", "P-1");
  await check(f, "equipment-count-relation-eq-1-evidence", "graphic");
  await f.change("equipment-count-reason", "Verified scoped multiplicity note"); await f.click("Save equipment review");
  assert.deepEqual(f.calls[0][1].request.relations[0], { id: "eq-1", kind: "multiplicity", member_ids: ["P-1"], each: 3,
    evidence_ids: ["graphic"], scope_text: "Three total including representative on level 1" });
});

test("invalid multiplier blocks save and keeps draft for correction", async () => {
  const f = fixture({ request: populated() });
  await f.click("Edit equipment review"); await f.click("Add relationship");
  await f.change("equipment-count-relation-eq-1-kind", "multiplicity");
  await f.change("equipment-count-relation-eq-1-each", "0");
  await f.change("equipment-count-reason", "Checked multiplier"); await f.click("Save equipment review");
  assert.equal(f.calls.length, 0); assert.match(f.errors.at(-1), /positive integer/);
  assert.equal(f.input("equipment-count-relation-eq-1-each").value, "0");
});

test("schedule-only declaration retains zero members and cannot create an observed assembly", async () => {
  const request = populated(); request.evidence.push({ ...copy(request.evidence[0]), id: "schedule-evidence", kind: "schedule", text: "One boiler" });
  const f = fixture({ request });
  await f.click("Edit equipment review"); await f.click("Add schedule declaration");
  await f.change("equipment-count-schedule-eq-1-family", "boiler");
  await f.change("equipment-count-schedule-eq-1-status", "new");
  await f.change("equipment-count-schedule-eq-1-each", "1");
  await check(f, "equipment-count-schedule-eq-1-evidence", "schedule-evidence");
  await f.change("equipment-count-reason", "Schedule-only requirement remains unresolved"); await f.click("Save equipment review");
  const saved = f.calls[0][1].request;
  assert.equal(saved.observations.length, 4);
  assert.deepEqual(saved.schedules[0], { id: "eq-1", family: "boiler", work_status: "new", member_ids: [], declared_each: 1, evidence_ids: ["schedule-evidence"] });
});

test("same and package links retain exact members, allow correction and never auto accept inferred relationships", async () => {
  const request = populated(); request.relations = [{ id: "same-1", kind: "same", member_ids: ["P-1", "P-2"], each: null, evidence_ids: ["graphic"], scope_text: "Original duplicate view" }];
  const f = fixture({ request }); assert.equal(f.calls.length, 0);
  await f.click("Edit equipment review"); await f.change("equipment-count-relation-same-1-kind", "distinct");
  await f.click("Add relationship"); await f.change("equipment-count-relation-eq-1-kind", "package");
  await f.change("equipment-count-relation-eq-1-each", "1");
  await check(f, "equipment-count-relation-eq-1-members", "P-1"); await check(f, "equipment-count-relation-eq-1-members", "P-2");
  await check(f, "equipment-count-relation-eq-1-evidence", "graphic");
  await f.change("equipment-count-reason", "Verified distinct pumps in one package"); await f.click("Save equipment review");
  assert.equal(f.calls[0][1].request.relations[0].kind, "distinct");
  assert.equal(f.calls[0][1].request.relations[0].each, null);
  assert.deepEqual(f.calls[0][1].request.relations[1].member_ids, ["P-1", "P-2"]);
  assert.equal(request.relations[0].kind, "same");
});

test("coverage review adds exact empty-page context and evidence without manufacturing an instance", async () => {
  const f = fixture({ request: emptyRequest() }); await f.click("Edit equipment review");
  await f.change("equipment-count-coverage-description", "Reviewed entire plan: no equipment in selected scope");
  await f.click("Record selected page coverage");
  await f.change("equipment-count-coverage", "complete"); await f.change("equipment-count-reason", "Checked complete empty drawing");
  await f.click("Save equipment review");
  const request = f.calls[0][1].request;
  assert.equal(request.observations.length, 0);
  assert.deepEqual(request.sources, [{ source, role: "plan", artifact_sha256: "artifact-a" }]);
  assert.deepEqual(request.scope.source_keys, ["source-a"]);
  assert.equal(request.evidence[0].kind, "coverage"); assert.deepEqual(request.evidence[0].bbox, [0, 0, 1, 1]);
  assert.deepEqual(request.coverage.evidence_ids, [request.evidence[0].id]);
  assert.equal(request.coverage.state, "complete");
});

test("failed source review retains draft and exact generation for safe correction", async () => {
  const f = fixture({ generation: "generation-a", request: populated() }); await f.click("Edit equipment review");
  await f.change("equipment-count-P-1-family", "circulating pump"); await f.change("equipment-count-reason", "Verified family from drawing");
  f.setSaveError("Evidence must contain original source text"); await f.click("Save equipment review");
  assert.match(f.errors.at(-1), /original source text/);
  assert.equal(f.input("equipment-count-P-1-family").value, "circulating pump");
  f.setSaveError(null); await f.click("Save equipment review");
  assert.equal(f.calls[0][1].generation, "generation-a");
  assert.equal(f.calls[0][1].request.observations[0].family, "circulating pump");
});

test("source context changes block stale save even when generation has not refreshed", async () => {
  const f = fixture({ generation: "generation-a", request: populated() }); await f.click("Edit equipment review");
  await f.change("equipment-count-reason", "Unfinished review");
  f.view.equipment_count_takeoff.current_source_contexts[0].source = { ...source, geometry_fingerprint: "new-geometry" };
  f.render(); assert.equal(f.button("Save equipment review").disabled, true);
  await f.click("Save equipment review"); assert.equal(f.calls.length, 0);
});

test("blank source evidence cannot save a newly drawn equipment region", async () => {
  const f = fixture({ request: emptyRequest() }); await f.click("Edit equipment review"); await f.click("Add source instance");
  await f.panel.regionPicked({ revision_id: "revision-a", index: 0, bbox: [0.2, 0.3, 0.4, 0.5] });
  await f.change("equipment-count-eq-2-family", "pump"); await f.change("equipment-count-reason", "Reviewed region");
  await f.click("Save equipment review"); assert.equal(f.calls.length, 0);
  assert.match(f.errors.at(-1), /source excerpt|evidence description/);
});

test("system and package counts are shown independently from physical quantity", () => {
  const f = fixture({ request: emptyRequest(), result: { complete: true, total_each: 3, known_subtotal_each: 3, rows: [], groups: [], declarations: [],
    systems: [{ id: "system-1", member_ids: ["outdoor", "indoor-a", "indoor-b"], physical_each: 3 }], procurement_packages: 1 } });
  assert.match(text(f.root), /Selected scope total: 3 each/);
  assert.match(text(f.root), /Procurement packages: 1 each/);
  assert.match(text(f.root), /System · system-1.*Physical assemblies: 3 each/);
});

test("stale source replacement removes only affected dependencies and preserves unrelated AHU and history", async () => {
  const request = emptyRequest();
  const ahuSource = { revision_id: "ahu-revision", index: 1, sheet_id: "ahu-sheet", geometry_fingerprint: "ahu-geometry" };
  const currentPump = { ...source, geometry_fingerprint: "geometry-new" };
  request.sources = [{ source, role: "plan", artifact_sha256: "artifact-a" }, { source: ahuSource, role: "plan", artifact_sha256: "ahu-revision" }];
  request.scope.source_keys = ["old-pump-key", "ahu-key"];
  request.coverage = { state: "complete", evidence_ids: ["old-coverage", "ahu-coverage"], unresolved_requirements: ["Existing unrelated requirement"] };
  request.evidence = [
    { id: "pump-graphic", source, bbox: [0.1, 0.1, 0.2, 0.2], kind: "graphic", text: "Pump", artifact_sha256: "artifact-a" },
    { id: "old-coverage", source, bbox: [0, 0, 1, 1], kind: "coverage", text: "Old pump plan reviewed", artifact_sha256: "artifact-a" },
    { id: "ahu-graphic", source: ahuSource, bbox: [0.4, 0.4, 0.6, 0.6], kind: "graphic", text: "AHU", artifact_sha256: "ahu-revision" },
    { id: "ahu-coverage", source: ahuSource, bbox: [0, 0, 1, 1], kind: "coverage", text: "AHU plan reviewed", artifact_sha256: "ahu-revision" }
  ];
  const ahu = observation("AHU-safe", "air_handler", { source: ahuSource, evidence_ids: ["ahu-graphic"] });
  request.observations = [observation("pump", "pump", { evidence_ids: ["pump-graphic"] }),
    observation("motor", "motor", { source: ahuSource, kind: "component", parent_id: "pump", evidence_ids: ["ahu-graphic"] }), ahu];
  request.relations = [{ id: "pump-package", kind: "package", member_ids: ["pump", "motor"], each: 1, evidence_ids: ["pump-graphic"], scope_text: "One supplied package" }];
  request.schedules = [{ id: "pump-schedule", family: "pump", work_status: "new", member_ids: ["pump"], declared_each: 2, evidence_ids: ["pump-graphic"] }];
  const prior = copy(request);
  const history = [{ actor: "Estimator", reason: "Prior pump and AHU review", request: prior, result: { complete: true, total_each: 2, known_subtotal_each: 2 } }];
  const f = fixture({ generation: "generation-a", request, history, stale: true, current_source_contexts: [
    { source: currentPump, role: "plan", artifact_sha256: "artifact-a", source_key: "new-pump-key" },
    { source: ahuSource, role: "plan", artifact_sha256: "ahu-revision", source_key: "ahu-key" }
  ] });
  await f.click("Edit equipment review"); await f.click("Replace stale source evidence");
  await f.change("equipment-count-reason", "Replace stale pump geometry and redraw affected source instances");
  await f.click("Save equipment review");
  const saved = f.calls[0][1].request;
  assert.deepEqual(saved.observations, [ahu]);
  assert.deepEqual(saved.evidence, prior.evidence.slice(2));
  assert.equal(saved.sources.some((entry) => entry.source.geometry_fingerprint === "geometry-a"), false);
  assert.ok(saved.sources.some((entry) => entry.source.geometry_fingerprint === "geometry-new"));
  assert.deepEqual(saved.scope.source_keys.sort(), ["ahu-key", "new-pump-key"]);
  assert.deepEqual(saved.relations, []); assert.deepEqual(saved.schedules, []);
  assert.equal(saved.coverage.state, "unknown"); assert.deepEqual(saved.coverage.evidence_ids, ["ahu-coverage"]);
  assert.ok(saved.coverage.unresolved_requirements.includes("Existing unrelated requirement"));
  assert.match(saved.coverage.unresolved_requirements.join(" "), /pump-package/);
  assert.match(saved.coverage.unresolved_requirements.join(" "), /pump-schedule/);
  assert.deepEqual(request, prior); assert.deepEqual(f.view.equipment_count_takeoff.history, history);
  assert.match(text(f.root), /Prior pump and AHU review/);
});

test("role changes expose source replacement even when source geometry identity stays equal", async () => {
  const request = populated();
  const f = fixture({ request, stale: true, current_source_contexts: [{ source, role: "schedule", artifact_sha256: "artifact-a", source_key: "source-a" }] });
  await f.click("Edit equipment review"); await f.click("Replace stale source evidence");
  await f.change("equipment-count-reason", "Page reassigned as schedule; physical source admission must be replaced"); await f.click("Save equipment review");
  const saved = f.calls[0][1].request;
  assert.equal(saved.observations.length, 0);
  assert.equal(saved.sources[0].role, "schedule"); assert.equal(saved.coverage.state, "unknown");
  assert.equal(saved.evidence.length, 0);
  assert.ok(saved.coverage.unresolved_requirements.length > 0);
});

test("occurrence correction visibly resets complete coverage without replacing focused inputs", async () => {
  const request = populated(); request.coverage.state = "complete";
  const f = fixture({ request, generation: "generation-a" }); await f.click("Edit equipment review");
  const coverage = f.input("equipment-count-coverage"), disposition = f.input("equipment-count-P-3-disposition");
  assert.equal(coverage.value, "complete");
  await f.change("equipment-count-P-3-disposition", "exclude");
  assert.equal(coverage.value, "unknown");
  assert.equal(f.input("equipment-count-P-3-disposition"), disposition, "input must remain mounted while editing");
  assert.equal(f.input("equipment-count-coverage"), coverage);
  await f.change("equipment-count-coverage", "complete");
  await f.change("equipment-count-reason", "False pump excluded and current coverage rechecked");
  await f.click("Save equipment review");
  assert.equal(f.calls[0][1].request.coverage.state, "complete");
  assert.equal(f.calls[0][1].request.observations[2].disposition, "exclude");
});
