"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const filename = path.join(__dirname, "../../apps/drawing-workspace/workflow.js");
const code = fs.readFileSync(filename, "utf8");
const clone = (value) => JSON.parse(JSON.stringify(value));
const flush = () => new Promise(setImmediate);

class Element {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.textContent = ""; this.value = "";
    this.listeners = {}; this.classList = { add() {} };
  }
  append(...elements) { this.children.push(...elements); }
  replaceChildren(...elements) { this.children = elements; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, listener) { (this.listeners[name] ||= []).push(listener); }
  async fire(name) { for (const listener of this.listeners[name] || []) await listener({ target: this, preventDefault() {} }); }
}
const all = (element) => [element, ...element.children.flatMap(all)];
const text = (element) => [element.textContent, ...element.children.map(text)].join(" ");
const buttons = (element, title) => all(element).filter((item) => item.tagName === "button" && item.textContent === title);
const input = (element, name) => all(element).find((item) => item.name === name);
const detail = (element, prefix) => all(element).find((item) => item.tagName === "details" && item.children[0]?.textContent.startsWith(prefix));
const source = { revision_id: "original-specification", index: 2, sheet_id: "original-sheet", bbox: [.05, .2, .85, .3] };
function requirement(id, statement, disposition = "pending", review_state = "pending") {
  return { id, text: statement, source: clone(source), qualifiers: ["conditional"], categories: ["piping"],
    disposition, review_state, stored_disposition: review_state === "stale" ? "excluded" : null,
    reason: review_state === "pending" ? "" : "Checked project conditions" };
}
function fixtureView() {
  const pipe = requirement("pipe", "Provide hydronic piping where shown.", "applicable", "current");
  const control = requirement("control", "Connect the DDC controls unless supplied by others.");
  const insulation = requirement("insulation", "Insulate the piping except where noted.", "pending", "stale");
  const tab = requirement("tab", "Submit TAB reports after balancing.", "excluded", "current");
  const unassigned = requirement("unassigned", "Verify service access before installation.");
  const other = { ...requirement("other", "Coordinate electrical connections."), section: "26 05 00", tags: [] };
  const section = (number, title, row, state) => ({ section: number,
    headings: [{ text: title, source: { ...source, bbox: [.05, .05, .85, .1] } }], requirements: row ? [row] : [],
    counts: { requirements: row ? 1 : 0, applicable: row?.disposition === "applicable" ? 1 : 0,
      excluded: row?.disposition === "excluded" ? 1 : 0, pending: row?.review_state === "pending" ? 1 : 0,
      stale: row?.review_state === "stale" ? 1 : 0 }, review_state: state });
  return { version: 7, selected_stage: "documents", project: { name: "Original specification fixture", scopes: ["piping"], units: "ft" },
    stages: [{ id: "documents", title: "Documents", status: "incomplete", can_review: false }],
    issues_view: [], capability_notes: [], history: [], inventory: [], pages: {}, project_knowledge: null,
    requirement_reviews: { insulation: { disposition: "excluded", reason: "An earlier saved review" } },
    model_baseline: null, model_inference: { jobs: [] }, duct_producer: { jobs: [] }, air_device_producer: { jobs: [] },
    document_reading: { state: "completed", stale: false, progress: 1, total_pages: 1, schedule_rows: [], issues: [],
      requirements: [pipe, control, insulation, tab, unassigned].map((row) => ({ ...clone(row), tags: [] })).concat(other) },
    mechanical_scope: { available: true, state: "current", section_index_available: true,
      sections: [section("23 21 13", "SECTION 23 21 13 - HYDRONIC PIPING", pipe, "requirements_reviewed"),
        section("23 09 23", "SECTION 23 09 23 - DDC CONTROLS", control, "needs_review"),
        section("23 07 19", "SECTION 23 07 19 - PIPING INSULATION", insulation, "needs_review"),
        section("23 05 93.01", "SECTION 23 05 93.01 - TAB", tab, "requirements_reviewed"),
        section("23 08 00", "SECTION 23 08 00 - COMMISSIONING", null, "no_requirements_identified")],
      unassigned_requirements: [unassigned], other_division_requirement_count: 1, unread_pages: [], issues: [],
      project_coverage_verified: false, product_coverage_verified: false } };
}
async function fixture(view = fixtureView(), response = null) {
  const nodes = new Map(), calls = [], events = [], listeners = new Map(), before = clone(view);
  const document = { getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element("div")); return nodes.get(id); },
    createElement: (tag) => new Element(tag), createTextNode(value) { const element = new Element("text"); element.textContent = value; return element; },
    querySelectorAll: () => [] };
  const window = { HeleosDucts: require("../../apps/drawing-workspace/ducts.js"),
    HeleosAirDevices: require("../../apps/drawing-workspace/air_devices.js"),
    addEventListener(name, listener) { listeners.set(name, listener); },
    dispatchEvent(event) { events.push(event); listeners.get(event.type)?.(event); } };
  vm.runInNewContext(code, { window, document, location: { pathname: "/mechanical-scope-fixture/" },
    sessionStorage: { getItem: () => null, setItem() {} }, TextEncoder,
    CustomEvent: class { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } },
    fetch: async (url, request) => {
      if (request.method === "POST") {
        const call = { url, body: JSON.parse(request.body) }; calls.push(call);
        return { ok: true, json: async () => response ? response(call) : view };
      }
      return { ok: true, json: async () => view };
    }, setTimeout: () => 1, clearTimeout() {} });
  await flush();
  document.getElementById("workflow-actor").value = "Specification reviewer";
  assert.equal(document.getElementById("workflow-error").textContent, "");
  const root = document.getElementById("workflow-content");
  const panel = () => all(root).find((item) => item.tagName === "section" && text(item).includes("Mechanical specification scope"));
  const click = async (control) => { assert.ok(control); assert.notEqual(control.disabled, true); await control.fire("click"); await flush(); };
  return { root, panel, view, before, calls, events, click, error: () => document.getElementById("workflow-error").textContent };
}

test("Documents groups the actual mechanical sections and preserves heading-only and unassigned scope", async () => {
  const f = await fixture(); assert.ok(f.panel(), "Mechanical section scope must be visible in Documents");
  for (const number of ["23 21 13", "23 09 23", "23 07 19", "23 05 93.01", "23 08 00"]) assert.ok(detail(f.panel(), "Section " + number));
  const tab = detail(f.panel(), "Section 23 05 93.01");
  assert.match(text(tab), /Extracted requirements reviewed/); assert.match(text(tab), /Submit TAB reports after balancing/);
  const headingOnly = detail(f.panel(), "Section 23 08 00");
  assert.match(text(headingOnly), /no requirements identified/i); assert.match(text(headingOnly), /unverified/i);
  assert.match(text(f.panel()), /Requirements without an identified section/); assert.match(text(f.panel()), /Verify service access/);
  assert.match(text(f.root), /Coordinate electrical connections/);
  assert.match(text(f.panel()), /1 requirement.*other division/i);
  assert.equal(buttons(f.root, "Save requirement review").length, 6, "Grouped requirements must not duplicate existing review controls");
  assert.doesNotMatch(text(f.panel()), /takeoff complete|scope complete|100%|0 each/i);
  assert.deepEqual(f.view, f.before, "Rendering must leave source records and reviews unchanged");
});

test("Current and stale saved reviews are distinguished without selecting a stale disposition", async () => {
  const f = await fixture(); assert.ok(f.panel());
  const current = detail(f.panel(), "Section 23 21 13"), stale = detail(f.panel(), "Section 23 07 19");
  const pending = detail(f.panel(), "Section 23 09 23");
  assert.match(text(current), /Current review.*Applies/); assert.equal(input(current, "disposition").value, "applicable");
  assert.match(text(stale), /Stale review/); assert.match(text(stale), /Previously.*Does not apply/);
  assert.equal(input(stale, "disposition").value, "pending");
  assert.match(text(pending), /Awaiting applicability review/);
});

test("Source controls open exact original headings, requirements and unread pages while source text stays inert", async () => {
  const view = fixtureView(); view.mechanical_scope.sections[0].headings[0].text = '<img src=x onerror="bad()">';
  view.mechanical_scope.unread_pages = [{ source: { ...source, index: 3, bbox: null }, state: "needs_ocr" }];
  view.mechanical_scope.issues = [{ code: "unattached_section", message: "A section heading needs review.", source }];
  const f = await fixture(view); assert.ok(f.panel()); const first = detail(f.panel(), "Section 23 21 13");
  await f.click(buttons(first, "Show section heading")[0]);
  assert.deepEqual(clone(f.events.at(-1).detail), { ...source, bbox: [.05, .05, .85, .1] });
  await f.click(buttons(first, "Show written source")[0]); assert.deepEqual(clone(f.events.at(-1).detail), source);
  await f.click(buttons(f.panel(), "Show unread page")[0]);
  assert.deepEqual(clone(f.events.at(-1).detail), { ...source, index: 3, bbox: [0, 0, 1, 1] });
  await f.click(buttons(f.panel(), "Show scope issue source")[0]); assert.deepEqual(clone(f.events.at(-1).detail), source);
  assert.match(text(f.panel()), /<img src=x/); assert.equal(all(f.panel()).some((item) => item.tagName === "img"), false);
});

test("Section requirement review uses the existing audited command and refreshes from its response", async () => {
  const view = fixtureView();
  const f = await fixture(view, () => {
    const next = clone(view), section = next.mechanical_scope.sections[1]; next.version = 8;
    Object.assign(section.requirements[0], { disposition: "applicable", review_state: "current", reason: "Controls responsibility checked" });
    section.review_state = "requirements_reviewed"; section.counts.applicable = 1; section.counts.pending = 0;
    return next;
  });
  assert.ok(f.panel()); const control = detail(f.panel(), "Section 23 09 23");
  input(control, "disposition").value = "applicable";
  await f.click(buttons(control, "Save requirement review")[0]); assert.equal(f.calls.length, 0); assert.match(f.error(), /reason/);
  input(control, "reason").value = "Controls responsibility checked";
  await f.click(buttons(control, "Save requirement review")[0]);
  assert.deepEqual(f.calls, [{ url: "api/workflow/requirement_review", body: { version: 7, actor: "Specification reviewer",
    reason: "Controls responsibility checked", values: { requirement_id: "control", disposition: "applicable" } } }]);
  assert.match(text(detail(f.panel(), "Section 23 09 23")), /Current review.*Applies/);
  assert.deepEqual(view, f.before);
});

test("Stale and in-progress readings retain section evidence but prevent requirement edits", async () => {
  for (const state of ["stale", "reading"]) {
    const view = fixtureView(); view.mechanical_scope.state = state;
    view.document_reading.stale = state === "stale"; view.document_reading.state = state === "reading" ? "running" : "completed";
    const f = await fixture(view); assert.ok(f.panel());
    assert.ok(detail(f.panel(), "Section 23 21 13"));
    assert.ok(buttons(f.panel(), "Save requirement review").every((control) => control.disabled));
    assert.match(text(f.panel()), state === "stale" ? /Read project documents again/ : /reading.*progress/i);
    assert.equal(f.calls.length, 0);
  }
});

test("Older readings request a section refresh without hiding known requirements", async () => {
  const view = fixtureView(); view.mechanical_scope.section_index_available = false;
  view.mechanical_scope.sections = [];
  view.mechanical_scope.unassigned_requirements = view.document_reading.requirements.slice(0, 5);
  const f = await fixture(view); assert.ok(f.panel());
  assert.match(text(f.panel()), /Read project documents again.*section headings/i);
  assert.match(text(f.panel()), /Provide hydronic piping where shown/);
  assert.equal(buttons(f.root, "Read project documents").length, 1);
  assert.equal(buttons(f.root, "Save requirement review").length, 6);
});

test("Failed and interrupted readings show retry guidance with disabled review controls", async () => {
  for (const state of ["failed", "interrupted"]) {
    const view = fixtureView();
    view.mechanical_scope.state = view.document_reading.state = state;
    const f = await fixture(view);
    assert.ok(f.panel());
    assert.match(text(f.panel()), /Read project documents again to refresh section scope/);
    assert.doesNotMatch(text(f.panel()), /in progress/);
    assert.ok(buttons(f.panel(), "Save requirement review").every((control) => control.disabled));
    assert.equal(f.calls.length, 0);
  }
});
