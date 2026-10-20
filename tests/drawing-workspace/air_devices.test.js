"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const air = require("../../apps/drawing-workspace/air_devices.js");
const copy = (value) => JSON.parse(JSON.stringify(value));
const source = { revision_id: "original-revision", index: 0, sheet_id: "original-sheet", geometry_fingerprint: "original-transform" };
const fieldNames = ["family", "type_tag", "system", "service", "work_status", "face_size", "neck_size", "opening_size", "assembly_length", "slot_count"];
function fixture() {
  const attributes = Object.fromEntries(fieldNames.map((field) => [field, { state: "not_supplied", value: null, evidence_ids: [] }]));
  Object.assign(attributes, { family: { state: "known", value: "diffuser", evidence_ids: ["graphic"] },
    type_tag: { state: "known", value: "SD-1", evidence_ids: ["note"] },
    work_status: { state: "known", value: "new_install", evidence_ids: ["note"] },
    face_size: { state: "known", value: { shape: "rectangular", dimensions: ["24", "24"], unit: "in", original_text: "24 x 24" }, evidence_ids: ["note"] } });
  const observation = { schema: "air-device-observation-1", id: "device-a", source, bbox: [0.1, 0.2, 0.3, 0.4],
    depiction: "physical", attributes, evidence_ids: ["graphic", "note"], issues: [] };
  const entry = { observation, observation_sha256: "observation-a-hash", decision: { action: "include" } };
  const result = { complete: false, total_each: null, known_subtotal_each: 1, physical_known_each: 1,
    groups: [{ group_id: "group-a", key: [{ field: "family", state: "known", value: "diffuser" },
      { field: "type_tag", state: "known", value: "sd-1" }, { field: "work_status", state: "known", value: "new_install" }],
      row_ids: ["physical-a"], complete: false, total_each: null, known_subtotal_each: 1, physical_known_each: 1, issues: ["coverage_incomplete"] }],
    rows: [{ row_id: "physical-a", attributes, member_ids: ["device-a"], physical_each: 1, requested: true,
      operations: { remove: 0, reinstall: 0 }, new_purchase_each: null, issues: [] }], issues: ["coverage_incomplete"], declarations: [] };
  const view = { version: 1, inventory: [{ revision_id: source.revision_id, index: 0, document_name: "Mechanical plan",
    assignment: { role: "plan", label: "M1" } }], air_device_producer: { configured: true, jobs: [] }, air_device_takeoff: {
    available: true, generation: "generation-a", fingerprint: "state-a", stale: false, prepared_reading_ids: [], observations: [entry],
    evidence: [{ id: "graphic", source, bbox: observation.bbox, kind: "graphic", text: null, artifact_sha256: "image-a" },
      { id: "note", source, bbox: [0.1, 0.4, 0.3, 0.5], kind: "text", text: "SD-1 new 24 x 24", artifact_sha256: "image-a" }],
    scope: { id: "scope-a", version: 1, source_keys: ["source-a"], group_by: ["family", "type_tag", "work_status"], required_fields: ["family", "work_status"], work_statuses: ["new_install"] },
    sources: [{ key: "source-a", source, role: "plan", current: true, issues: [] }], coverage: { state: "unknown" },
    result, history: [], proposals: { readings: [], correspondences: [], multiplicities: [], relocations: [], schedules: [] } } };
  return { view, entry, observation, result };
}
function controllerFixture(options = {}) {
  const values = fixture(), calls = [];
  let busy = false, actor = "Estimator";
  const env = { getView: () => values.view, isBusy: () => busy, getActor: () => actor,
    save: async (...args) => { calls.push(copy(args)); return options.save ? options.save(...args) : true; } };
  return { ...values, calls, env, controller: air.createController(env), setBusy(value) { busy = value; }, setActor(value) { actor = value; } };
}
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.textContent = ""; this.value = ""; this.checked = false; this.listeners = {}; }
  append(...elements) { this.children.push(...elements); }
  addEventListener(name, action) { this.listeners[name] = action; }
  async fire(name) { await this.listeners[name]?.({ target: this }); }
}
const all = (root) => [root, ...root.children.flatMap(all)];
const text = (root) => [root.textContent, ...root.children.map(text)].join(" ");
function panelFixture() {
  const f = controllerFixture(), root = new Element("main"), errors = [], sources = [];
  let selection = { revision: source.revision_id, index: 0 }, nextId = 0;
  const panel = air.createPanel({ ...f.env, document: { createElement: (tag) => new Element(tag) },
    getSelection: () => selection, showSource: (value) => sources.push(copy(value)), error: (message) => errors.push(message),
    newId: () => "review-" + ++nextId, render: () => render() });
  function render() { root.children = []; panel.render(root); }
  render();
  const button = (title) => all(root).find((element) => element.tagName === "button" && element.textContent === title);
  const input = (name) => all(root).find((element) => element.name === name);
  const change = async (name, value) => {
    const control = input(name); assert.ok(control, name); control.value = value;
    await control.fire(control.tagName === "select" ? "change" : "input");
  };
  const click = async (title) => { const control = button(title); assert.ok(control, title); await control.fire("click"); };
  return { ...f, panel, root, render, button, input, change, click, errors, sources, setSelection(value) { selection = value; } };
}

test("completed readings prepare once across concurrent refreshes and saved reopen", async () => {
  let release; const waiting = new Promise((resolve) => { release = resolve; });
  const f = controllerFixture({ save: () => waiting });
  f.view.air_device_producer.jobs = [{ id: "complete", state: "completed" }, { id: "failed", state: "failed" },
    { id: "running", state: "running" }, { id: "cancelled", state: "cancelled" }];
  const first = f.controller.reconcile(); await f.controller.reconcile(); release(true); await first; await f.controller.reconcile();
  assert.equal(f.calls.length, 1); assert.deepEqual(f.calls[0].slice(0, 2), ["air_device_prepare", { reading_id: "complete" }]);
  f.view.air_device_takeoff.prepared_reading_ids.push("complete");
  await air.createController(f.env).reconcile(); assert.equal(f.calls.length, 1);
});

test("automatic preparation waits for actor and idle state; failures require explicit retry without blocking other sources", async () => {
  const f = controllerFixture({ save: async (_, body) => { if (body.reading_id === "bad") throw new Error("Source changed"); return true; } });
  f.view.air_device_producer.jobs = [{ id: "bad", state: "completed" }, { id: "good", state: "completed" }];
  f.setActor(""); await f.controller.reconcile(); f.setActor("Estimator"); f.setBusy(true); await f.controller.reconcile();
  assert.equal(f.calls.length, 0); f.setBusy(false);
  await assert.rejects(f.controller.reconcile(), /Source changed/); await f.controller.reconcile();
  assert.deepEqual(f.calls.map((call) => call[1].reading_id), ["bad", "good"]);
  await assert.rejects(f.controller.retry("bad"), /Source changed/);
  assert.deepEqual(f.calls.map((call) => call[1].reading_id), ["bad", "good", "bad"]);
});

test("observation decisions and attribute corrections carry exact pins and no final quantity", async () => {
  const f = controllerFixture(), original = copy(f.observation), pin = f.controller.snapshot(f.entry);
  await f.controller.review(pin, "exclude", "Graphic belongs to the legend");
  const attrs = copy(f.observation.attributes); attrs.system = { state: "known", value: "AHU-1", evidence_ids: ["note"] };
  await f.controller.correct(pin, attrs, "physical", "AHU designation verified in original note");
  assert.deepEqual(f.calls[0][1], { generation: "generation-a", observation_id: "device-a", observation_sha256: "observation-a-hash", action: "exclude" });
  assert.deepEqual(f.calls[1][1], { generation: "generation-a", observation_id: "device-a", observation_sha256: "observation-a-hash", attributes: attrs, depiction: "physical" });
  assert.deepEqual(f.observation, original); assert.equal("total_each" in f.calls[1][1], false);
  assert.equal("fingerprint" in f.calls[1][1], false, "browser-only stale-state pin is not an extra server field");
});

test("changed generation, fingerprint, observation bytes or source state block stale editing before any save", () => {
  for (const mutate of [
    (f) => { f.view.air_device_takeoff.generation = "new"; },
    (f) => { f.view.air_device_takeoff.fingerprint = "changed"; },
    (f) => { f.entry.observation_sha256 = "changed"; },
    (f) => { f.view.air_device_takeoff.stale = true; },
  ]) {
    const f = controllerFixture(), pin = f.controller.snapshot(f.entry); mutate(f);
    assert.throws(() => f.controller.correct(pin, f.observation.attributes, "physical", "reason"), /changed.*Reopen/);
    assert.throws(() => f.controller.review(pin, "include", "reason"), /changed.*Reopen/);
    assert.equal(f.calls.length, 0);
  }
});

test("unknown final counts keep known subtotals, while an explicitly complete empty scope shows zero", () => {
  const f = panelFixture(); const before = copy(f.view);
  assert.match(text(f.root), /Selected scope total: Unknown · incomplete/);
  assert.match(text(f.root), /Known subtotal in requested work scope: 1 each/);
  assert.deepEqual(f.view, before);
  Object.assign(f.result, { complete: false, total_each: null, known_subtotal_each: 0, physical_known_each: 0, rows: [], groups: [] });
  f.render(); assert.match(text(f.root), /Selected scope total: Unknown/); assert.doesNotMatch(text(f.root), /Selected scope total: 0 each/);
  Object.assign(f.result, { complete: true, total_each: 0 }); f.render();
  assert.match(text(f.root), /Selected scope total: 0 each/);
  f.view.air_device_takeoff.stale = true; f.render();
  assert.match(text(f.root), /Selected scope total: Unknown/); assert.match(text(f.root), /earlier quantities remain in history/);
});

test("reader uses the explicitly selected eligible drawing and exposes cancellation without choosing another page", async () => {
  const f = panelFixture(); f.setSelection(null); f.render();
  assert.equal(f.button("Find air devices on selected drawing").disabled, true);
  f.setSelection({ revision: source.revision_id, index: 0 }); f.render();
  await f.click("Find air devices on selected drawing");
  assert.deepEqual(f.calls[0].slice(0, 2), ["air_device_find", { source: { revision_id: source.revision_id, index: 0 } }]);
  f.view.air_device_producer.jobs = [{ id: "job-a", state: "running", source }]; f.render();
  assert.equal(f.panel.active(), true); assert.equal(f.button("Find air devices on selected drawing").disabled, true);
  await f.click("Stop air-device reading"); assert.deepEqual(f.calls[1].slice(0, 2), ["air_device_find_cancel", { job_id: "job-a" }]);
  f.view.air_device_producer.configured = false; f.render();
  assert.match(text(f.root), /Configure a local drawing model/);
});

test("source navigation retains exact revision, page, transform and evidence box; labels remain inert", async () => {
  const f = panelFixture(); f.observation.attributes.type_tag.value = '<img src=x onerror="bad()">'; f.render();
  await f.click("Show on drawing"); assert.deepEqual(f.sources[0], { ...source, bbox: f.observation.bbox });
  await f.click("Show source text"); assert.deepEqual(f.sources[1], { ...source, bbox: [0.1, 0.4, 0.3, 0.5] });
  assert.match(text(f.root), /<img src=x/); assert.equal(all(f.root).some((element) => element.tagName === "img"), false);
  assert.doesNotMatch(fs.readFileSync(path.resolve(__dirname, "../../apps/drawing-workspace/air_devices.js"), "utf8"), /innerHTML|outerHTML|insertAdjacentHTML/);
});

test("correction form preserves source dimensions and references without taking manual final totals", async () => {
  const f = panelFixture(), before = copy(f.observation);
  await f.click("Review occurrence");
  await f.change("air-device-face_size-dimension-0", "25.4");
  await f.change("air-device-face_size-unit", "mm");
  await f.change("air-device-face_size-text", "25.4 mm x 24 mm");
  await f.change("air-device-review-reason", "Verified the drawing dimensions");
  await f.click("Save attribute correction");
  const body = f.calls[0][1];
  assert.equal(body.attributes.face_size.value.dimensions[0], "25.4"); assert.equal(body.attributes.face_size.value.unit, "mm");
  assert.deepEqual(body.attributes.face_size.evidence_ids, ["note"]); assert.deepEqual(f.observation, before);
  assert.equal(all(f.root).some((element) => /total|quantity/.test(element.name || "")), false);
});

test("draft becomes visibly stale after refresh, with edits retained and saving disabled", async () => {
  const f = panelFixture(); await f.click("Review occurrence");
  await f.change("air-device-review-reason", "My unfinished source review");
  f.entry.observation_sha256 = "new-bytes"; f.render();
  assert.equal(f.input("air-device-review-reason").value, "My unfinished source review");
  assert.equal(f.button("Save occurrence decision").disabled, true);
  await f.click("Save occurrence decision"); assert.equal(f.calls.length, 0);
  assert.match(text(f.root), /Reopen this review/);
});

test("scope and coverage decisions are explicit and source-pinned, and recalculation permits stale retained results", async () => {
  const f = panelFixture(); await f.click("Review air-device scope");
  await f.change("air-device-scope-reason", "Only current new-work drawings selected"); await f.click("Save air-device scope");
  assert.deepEqual(f.calls[0][1], { generation: "generation-a", source_keys: ["source-a"], group_by: ["family", "type_tag", "work_status"], required_fields: ["family", "work_status"], work_statuses: ["new_install"] });
  await f.click("Review drawing coverage");
  assert.match(text(f.root), /empty detector response does not establish zero/);
  await f.change("air-device-coverage-state", "complete"); await f.change("air-device-coverage-reason", "Reviewed all selected drawings and resolved exceptions");
  await f.click("Save coverage review");
  assert.deepEqual(f.calls[1].slice(0, 2), ["air_device_coverage", { generation: "generation-a", state: "complete" }]);
  f.view.air_device_takeoff.stale = true; f.render(); await f.click("Recalculate air-device counts");
  assert.deepEqual(f.calls[2][1], { generation: "generation-a" });
});

test("new reading intake needs explicit add or replacement decision pinned to the retained reading", async () => {
  const f = panelFixture();
  f.view.air_device_takeoff.proposals.readings = [{ reading_id: "reread", reading_sha256: "reading-hash", source, role: "plan", state: "pending", same_page: true }];
  f.render(); assert.equal(f.calls.length, 0);
  await f.change("air-device-intake-action-reread", "replace"); await f.change("air-device-intake-reason-reread", "Reviewed the replacement page reading");
  await f.click("Save reading review · reread");
  assert.deepEqual(f.calls[0][1], { generation: "generation-a", reading_id: "reread", reading_sha256: "reading-hash", action: "replace" });
  f.view.air_device_takeoff.proposals.readings[0].reading_sha256 = "changed";
  await f.click("Save reading review · reread"); assert.equal(f.calls.length, 1); assert.match(f.errors.at(-1), /reading changed/);
});

test("model relationship proposals remain unaccepted until source review; schedule acceptance requires named occurrences", async () => {
  const f = panelFixture(), proposals = f.view.air_device_takeoff.proposals;
  proposals.correspondences.push({ id: "same-a", sha256: "same-hash", state: "pending", record: {
    id: "same-a", members: [{ observation_id: "device-a", observation_sha256: "observation-a-hash" }], state: "same", evidence_ids: ["note"] } });
  proposals.schedules.push({ id: "schedule-a", sha256: "schedule-hash", state: "pending", record: { declared_each: 4, evidence_ids: ["note"] } });
  f.render(); assert.equal(f.calls.length, 0); assert.match(text(f.root), /model relationship is a proposal/);
  await f.change("air-device-proposal-action-same-a", "accept"); await f.change("air-device-proposal-reason-same-a", "Matched original depiction references");
  await f.click("Save relationship review · same-a");
  assert.deepEqual(f.calls[0][1], { generation: "generation-a", proposal_id: "same-a", proposal_sha256: "same-hash", action: "accept", member_ids: [] });
  await f.change("air-device-proposal-action-schedule-a", "accept"); await f.change("air-device-proposal-reason-schedule-a", "Verified this schedule entry");
  await f.click("Save relationship review · schedule-a"); assert.equal(f.calls.length, 1); assert.match(f.errors.at(-1), /Select the physical occurrences/);
  const member = f.input("air-device-schedule-members-schedule-a"); member.checked = true; await member.fire("change");
  await f.click("Save relationship review · schedule-a"); assert.deepEqual(f.calls[1][1].member_ids, ["device-a"]);
  proposals.schedules[0].sha256 = "changed"; await f.click("Save relationship review · schedule-a");
  assert.equal(f.calls.length, 2); assert.match(f.errors.at(-1), /relationship changed/);
});

test("saved history shows previous results separately and rendering never changes source records", () => {
  const f = panelFixture(); f.view.air_device_takeoff.history = [{ reason: "Before false-positive review", actor: "Estimator", result: {
    complete: true, total_each: 3, known_subtotal_each: 3 } }];
  const before = copy(f.view); f.render();
  assert.match(text(f.root), /Saved air-device calculation history · 1/); assert.match(text(f.root), /Before false-positive review · Estimator · 3 each/);
  assert.match(text(f.root), /Selected scope total: Unknown/); assert.deepEqual(f.view, before);
});

async function check(f, name, value, checked = true) {
  const input = all(f.root).find((element) => element.name === name && element.value === value);
  assert.ok(input, name + "=" + value); input.checked = checked; await input.fire("change");
}
function secondOccurrence(f) {
  const entry = copy(f.entry); entry.observation.id = "device-b"; entry.observation.bbox = [0.6, 0.2, 0.8, 0.4];
  entry.observation_sha256 = "observation-b-hash"; f.view.air_device_takeoff.observations.push(entry); return entry;
}
test("source-supported manual correspondence names exact occurrences and evidence, with no inferred duplicate or count", async () => {
  const f = panelFixture(); secondOccurrence(f); f.render();
  await f.click("Review correspondence between drawings");
  await check(f, "air-device-relation-member", "device-a"); await check(f, "air-device-relation-member", "device-b");
  await check(f, "air-device-relation-evidence", "note");
  await f.change("air-device-relation-state", "same"); await f.change("air-device-relation-reason", "Verified explicit enlarged-view callout");
  await f.click("Save source-supported relationship");
  assert.equal(f.calls[0][0], "air_device_relation"); const body = f.calls[0][1];
  assert.equal(body.kind, "correspondence"); assert.equal(body.relation_sha256, null); assert.equal(body.record.state, "same");
  assert.deepEqual(body.record.members, [{ observation_id: "device-a", observation_sha256: "observation-a-hash" },
    { observation_id: "device-b", observation_sha256: "observation-b-hash" }]);
  assert.deepEqual(body.record.evidence_ids, ["note"]); assert.equal("total_each" in body.record, false);
});

test("scoped multiplicity requires a positive integer, named representative and explicit note scope", async () => {
  const f = panelFixture(); await f.click("Review a scoped quantity note");
  await check(f, "air-device-relation-member", "device-a"); await check(f, "air-device-relation-evidence", "note");
  await f.change("air-device-relation-each", "4.5"); await f.change("air-device-relation-scope", "Room 104");
  await f.change("air-device-relation-representative", "device-a"); await f.change("air-device-relation-reason", "Four total in Room 104 note");
  await f.click("Save source-supported relationship"); assert.equal(f.calls.length, 0); assert.match(f.errors.at(-1), /positive whole-number/);
  await f.change("air-device-relation-each", "4"); await f.click("Save source-supported relationship");
  assert.equal(f.calls[0][1].record.each, 4); assert.equal(f.calls[0][1].record.representative, "device-a");
  assert.equal(f.calls[0][1].record.scope_text, "Room 104");
});

test("relocation review requires separate supported removal and reinstallation sides and retains operation identities", async () => {
  const f = panelFixture(); secondOccurrence(f); f.render(); await f.click("Review a relocated assembly");
  await check(f, "air-device-relation-member", "device-a"); await check(f, "air-device-relation-member", "device-b");
  await check(f, "air-device-relation-evidence", "note"); await f.change("air-device-relation-reason", "Verified old and new reused assembly locations");
  await f.click("Save source-supported relationship"); assert.equal(f.calls.length, 0); assert.match(f.errors.at(-1), /Assign every named depiction/);
  await f.change("air-device-relocation-side-device-a", "remove"); await f.change("air-device-relocation-side-device-b", "reinstall");
  await f.click("Save source-supported relationship"); const record = f.calls[0][1].record;
  assert.deepEqual(record.remove_members, ["device-a"]); assert.deepEqual(record.reinstall_members, ["device-b"]);
  assert.notEqual(record.remove_operation_id, record.reinstall_operation_id); assert.equal(record.reused, true);
  assert.equal("new_purchase_each" in record, false, "deterministic engine computes operations and purchase quantities");
});

test("saved relationship correction and withdrawal keep record identity and reject a changed relationship hash", async () => {
  const f = panelFixture(); const relation = { id: "relation-a", sha256: "relation-hash", record: {
    id: "relation-a", members: [{ observation_id: "device-a", observation_sha256: "observation-a-hash" }],
    representative: "device-a", each: 4, scope_text: "Room 104", evidence_ids: ["note"] } };
  f.view.air_device_takeoff.relationships = { multiplicities: [relation] }; f.render();
  await f.click("Correct relationship · relation-a"); await f.change("air-device-relation-reason", "Revised note withdraws this total");
  relation.sha256 = "changed"; await f.click("Remove saved relationship"); assert.equal(f.calls.length, 0); assert.match(f.errors.at(-1), /relationship changed/);
  relation.sha256 = "relation-hash"; await f.click("Remove saved relationship");
  assert.deepEqual(f.calls[0][1], { generation: "generation-a", kind: "multiplicity", relation_id: "relation-a",
    relation_sha256: "relation-hash", action: "remove", record: null });
});

test("source issues are retained unless explicitly resolved in an evidence-backed correction", async () => {
  const f = panelFixture(); f.observation.issues = ["family_unclear", "note_scope_unclear"]; f.render();
  await f.click("Review occurrence");
  await check(f, "air-device-unresolved-issue", "family_unclear", false);
  await f.change("air-device-review-reason", "Legend verified family; note scope remains unresolved");
  await f.click("Save attribute correction");
  assert.deepEqual(f.calls[0][1].issues, ["note_scope_unclear"]);
  assert.deepEqual(f.observation.issues, ["family_unclear", "note_scope_unclear"]);
});

test("stale relationship and intake drafts offer an explicit restart using current pins", async () => {
  const f = panelFixture(); f.view.air_device_takeoff.proposals.readings = [{ reading_id: "incoming", reading_sha256: "old", source, state: "pending", same_page: false }];
  f.render(); await f.change("air-device-intake-action-incoming", "add"); await f.change("air-device-intake-reason-incoming", "Draft explanation");
  f.view.air_device_takeoff.fingerprint = "new-state"; f.view.air_device_takeoff.proposals.readings[0].reading_sha256 = "new"; f.render();
  assert.equal(f.button("Save reading review · incoming").disabled, true);
  assert.equal(f.input("air-device-intake-reason-incoming").value, "Draft explanation");
  await f.click("Reopen reading review · incoming");
  assert.equal(f.input("air-device-intake-reason-incoming").value, "");
  await f.change("air-device-intake-action-incoming", "add"); await f.change("air-device-intake-reason-incoming", "Reviewed current source bytes");
  await f.click("Save reading review · incoming"); assert.equal(f.calls[0][1].reading_sha256, "new");
});

test("schedule and legend reading is explicit context evidence and requires a prior plan or detail generation", async () => {
  for (const role of ["schedule", "legend"]) {
    const f = panelFixture(); f.view.inventory[0].assignment.role = role;
    const title = "Read selected " + role + " for air-device evidence";
    f.view.air_device_takeoff.available = false; f.view.air_device_takeoff.generation = null; f.render();
    assert.equal(f.button(title).disabled, true); assert.match(text(f.root), /Read a plan or detail drawing first/);
    await f.click(title); assert.equal(f.calls.length, 0);
    f.view.air_device_takeoff.available = true; f.view.air_device_takeoff.generation = "generation-a"; f.render();
    assert.match(text(f.root), /do not create installed assemblies/);
    await f.click(title); assert.equal(f.calls[0][0], "air_device_find");
  }
});

test("attribute correction can explicitly cite retained schedule evidence beyond the original occurrence", async () => {
  const f = panelFixture(); f.view.air_device_takeoff.evidence.push({ id: "schedule-system", source: { ...source, index: 2 },
    bbox: [0.1, 0.1, 0.5, 0.2], kind: "text", text: "AHU-1", artifact_sha256: "schedule-image" });
  f.render(); await f.click("Review occurrence");
  await f.change("air-device-system-state", "known"); await f.change("air-device-system-value", "AHU-1");
  await check(f, "air-device-system-evidence", "schedule-system"); await f.change("air-device-review-reason", "Explicit schedule match verified");
  await f.click("Save attribute correction");
  assert.deepEqual(f.calls[0][1].attributes.system, { state: "known", value: "AHU-1", evidence_ids: ["schedule-system"] });
  assert.equal(f.observation.evidence_ids.includes("schedule-system"), false, "persisted adapter binds the correction; the UI does not mutate originals");
});

test("actual kernel group keys render family, tag, work scope, qualified rational dimensions and missingness states", () => {
  const f = panelFixture();
  f.result.groups[0].key.push(
    { field: "face_size", state: "known", value: { shape: "rectangular", dimensions: [{ n: "24", d: "1" }, { n: "3", d: "2" }], unit: "in" } },
    { field: "assembly_length", state: "known", value: { value: { n: "1250", d: "127" }, unit: "in" } },
    { field: "system", state: "unknown", value: null },
    { field: "neck_size", state: "not_supplied", value: null },
    { field: "opening_size", state: "not_applicable", value: null });
  const before = copy(f.result); f.render();
  const group = all(f.root).find((element) => element.tagName === "details" && element.children[0]?.textContent === "Air-device count group");
  const dl = group.children.find((element) => element.tagName === "dl");
  const values = Object.fromEntries(dl.children.filter((_, index) => index % 2 === 0).map((element, index) =>
    [element.textContent, dl.children[index * 2 + 1].textContent]));
  assert.equal(values.Family, "Diffuser"); assert.equal(values["Type tag"], "Sd-1");
  assert.equal(values["Work scope"], "New installation");
  assert.equal(values["Face size"], "Rectangular · 24 × 3/2 in");
  assert.equal(values["Assembly length"], "1250/127 in");
  assert.equal(values.System, "Unknown"); assert.equal(values["Neck / connection size"], "Not supplied");
  assert.equal(values["Opening size"], "Not applicable");
  assert.doesNotMatch(text(group), /\[object Object\]/); assert.deepEqual(f.result, before);
});

test("actual kernel declarations retain observed and declared counts, comparison state and issues separately", () => {
  const f = panelFixture();
  f.result.declarations = [{ id: "schedule-a", declared_each: 4, observed_each: 3, state: "conflict",
    row_ids: ["physical-a"], member_ids: ["device-a"], issues: ["schedule_quantity_mismatch"] },
  { id: "schedule-b", declared_each: 2, observed_each: null, state: "unresolved", row_ids: [], member_ids: [], issues: ["schedule_support_unresolved"] }];
  f.render();
  const first = all(f.root).find((element) => element.tagName === "details" && element.children[0]?.textContent === "Schedule declaration · schedule-a");
  assert.match(text(first), /Observed assemblies: 3 each/); assert.match(text(first), /Schedule-declared assemblies: 4 each/);
  assert.match(text(first), /Schedule comparison: Conflict/); assert.match(text(first), /Schedule quantity mismatch/);
  const unresolved = all(f.root).find((element) => element.tagName === "details" && element.children[0]?.textContent === "Schedule declaration · schedule-b");
  assert.match(text(unresolved), /Observed assemblies: Unknown/); assert.match(text(unresolved), /Schedule comparison: Needs review/);
  assert.match(text(unresolved), /Schedule support unresolved/);
});
