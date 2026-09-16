"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const content = byId("workflow-content");
  const scopes = ["ductwork", "air_devices", "equipment", "piping", "fittings", "insulation", "controls", "accessories", "demolition"];
  const roles = ["plan", "schedule", "specification", "detail", "riser", "legend", "addendum", "excluded"];
  const label = (value) => value.replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());
  let view = null;
  let stage = "setup";
  let selection = null;
  let busy = false;
  let pickedView = null;
  let pickedLine = null;
  let pickingView = false;
  let workflowPoll = null;
  let itemEditing = null;
  let measurementDraft = { calibration: {}, measurement: {}, declared: {} };
  const scaleDrafts = { decisions: {}, recalculations: {} };
  const reconciliationDrafts = {};
  let fieldSequence = 0;
  let modelPanelOpen = false;
  const modelInferenceDraft = { plan_id: "", reason: "" };
  const actorKey = "heleos-workflow-actor:" + location.pathname;
  try { byId("workflow-actor").value = sessionStorage.getItem(actorKey) || ""; } catch (_) {}
  const ductPanel = window.HeleosDucts.createPanel({ document, getView: () => view,
    getSelection: () => selection, isBusy: () => busy, save, render, error, showSource,
    getActor: () => byId("workflow-actor").value.trim(),
    newId: () => "duct-review-" + crypto.randomUUID(),
    editPath: (request) => window.dispatchEvent(new CustomEvent("heleos:edit-path", { detail: request })),
    pathCommand: (command) => window.dispatchEvent(new CustomEvent("heleos:path-command", { detail: command })),
    pickRegion: (active) => window.dispatchEvent(new CustomEvent("heleos:pick-region", { detail: active })),
    pickLine: (purpose) => window.dispatchEvent(new CustomEvent("heleos:pick-line", { detail: purpose })) });
  let ductPreparation = false;
  const airPanel = window.HeleosAirDevices.createPanel({ document, getView: () => view,
    getSelection: () => selection, isBusy: () => busy, save, render, error, showSource,
    getActor: () => byId("workflow-actor").value.trim(),
    newId: () => "air-review-" + crypto.randomUUID() });
  let airPreparation = false;
  const equipmentCountPanel = window.HeleosEquipmentCounts.createPanel({ document, getView: () => view,
    getSelection: () => selection, isBusy: () => busy, save, render, error, showSource,
    getActor: () => byId("workflow-actor").value.trim(),
    newId: () => "equipment-review-" + crypto.randomUUID(),
    pickRegion: (active) => window.dispatchEvent(new CustomEvent("heleos:pick-region", { detail: active })) });

  async function prepareCompletedAirReadings() {
    if (busy || airPreparation || !view || !byId("workflow-actor").value.trim()) return;
    airPreparation = true;
    try { if (await airPanel.reconcile()) render(); }
    catch (e) { error(e.message); render(); }
    finally { airPreparation = false; }
  }

  async function prepareCompletedDuctReadings() {
    if (busy || ductPreparation || !view || !byId("workflow-actor").value.trim()) return;
    ductPreparation = true;
    try { if (await ductPanel.controller.reconcile()) render(); }
    catch (e) { error(e.message); render(); }
    finally { ductPreparation = false; }
  }
  byId("workflow-actor").addEventListener("change", () => {
    prepareCompletedDuctReadings().catch((e) => error(e.message));
    prepareCompletedAirReadings().catch((e) => error(e.message));
  });

  function node(tag, value = "", className = "") {
    const element = document.createElement(tag);
    element.textContent = value;
    element.className = className;
    return element;
  }
  function error(message = "") {
    byId("workflow-error").textContent = message;
    byId("workflow-error").hidden = !message;
  }
  function info(message) { byId("workflow-message").textContent = message; }
  function button(title, action, secondary = false) {
    const element = node("button", title, "button " + (secondary ? "button-outline" : "button-primary"));
    element.type = "button";
    element.disabled = busy;
    element.addEventListener("click", () => {
      Promise.resolve().then(action).catch((e) => error(e.message));
    });
    return element;
  }
  function field(form, title, key, value = "", options = null) {
    const wrapper = node("label", title, "field-label");
    const input = document.createElement(options ? "select" : "input");
    input.name = key;
    input.id = "workflow-field-" + key + "-" + (++fieldSequence);
    if (options) {
      for (const option of options) {
        const pair = Array.isArray(option) ? option : [option, label(option)];
        const element = node("option", pair[1]);
        element.value = pair[0];
        input.append(element);
      }
    } else {
      input.maxLength = 500;
    }
    input.value = value ?? "";
    wrapper.append(input);
    form.append(wrapper);
    return input;
  }
  function form() {
    const element = node("form", "", "workflow-form");
    element.addEventListener("submit", (event) => event.preventDefault());
    content.append(element);
    return element;
  }
  function values(element) { return Object.fromEntries(new FormData(element)); }
  function hint(value) { content.append(node("p", value, "section-hint")); }
  function list(value) { return Array.isArray(value) ? value : []; }
  function knowledgeLabels(knowledge) {
    if (!knowledge || typeof knowledge !== "object") return [];
    const matches = [...list(knowledge.matches)];
    for (const context of list(knowledge.context)) matches.push(...list(context?.matches));
    return [...new Set(matches.map((match) => match?.label).filter((value) => typeof value === "string" && value.trim()))];
  }
  function knowledgeDescription(knowledge) {
    const terms = knowledgeLabels(knowledge);
    const categories = [...new Set(list(knowledge?.categories).filter((value) => typeof value === "string"))].map(label);
    if (!terms.length && !categories.length) return "No known mechanical terms classified";
    const parts = [];
    if (terms.length) parts.push("Terms / services: " + terms.join(", "));
    if (categories.length) parts.push("Scope: " + categories.join(", "));
    return parts.join(" · ");
  }
  function sourceIssuesFor(knowledge) {
    if (!knowledge || typeof knowledge !== "object") return [];
    const sourceIds = new Set(list(knowledge.source_ids));
    return list(view.project_knowledge?.sources).filter((sourceEntry) =>
      sourceIds.has(sourceEntry?.id) && sourceEntry?.state && sourceEntry.state !== "current");
  }
  function sourceRef(value) {
    const candidate = value && typeof value === "object" && value.source && typeof value.source === "object" ?
      value.source : value;
    return candidate && typeof candidate === "object" && typeof candidate.revision_id === "string" &&
      candidate.revision_id && Number.isInteger(candidate.index) ? candidate : null;
  }
  function applicabilityLabel(status) {
    return ({
      matched: "Matches this item",
      needs_context: "Needs more context",
      source_blocked: "Source unavailable",
      unsupported: "Wording not supported yet",
      conflict: "Conflicting requirements",
      confirmed_applies: "Recorded item decision: applies",
      confirmed_excluded: "Recorded item decision: does not apply",
    })[status] || "Needs more context";
  }
  function applicabilityReason(value) {
    if (typeof value !== "string" || !value.trim()) return null;
    const reason = value.trim();
    const known = {
      condition_requires_context: "A stated condition needs review.",
      source_context_requires_resolution: "The source context needs resolution.",
      "requirement_qualifier:conditional": "A conditional requirement needs review.",
      unsupported_grammar: "The requirement wording is not supported yet.",
    };
    if (known[reason]) return known[reason];
    if (reason.startsWith("Stored applicability decision is stale:"))
      return "The recorded item decision is stale because the requirement, source, or item changed.";
    if (reason.startsWith("rule_issue:")) return "A cited rule issue needs resolution.";
    if (reason.startsWith("requirement_qualifier:")) return "A requirement qualifier needs review.";
    if (reason.startsWith("unsupported_grammar:")) return "The requirement wording is not supported yet.";
    if (/^[a-z][a-z0-9_]*(?::[a-z0-9_.-]+)?$/.test(reason))
      return "Additional applicability context needs review.";
    return reason;
  }
  function applicabilityReasons(values) {
    return [...new Set(list(values).map(applicabilityReason).filter(Boolean))];
  }
  function decisionState(record) {
    const disposition = record?.payload?.disposition;
    if (record?.resolution_state === "current" && disposition === "applies") return "Current decision · Applies";
    if (record?.resolution_state === "current" && disposition === "excludes") return "Current decision · Does not apply";
    return ({
      superseded: "Superseded decision",
      orphaned: "No longer linked to a current item",
      withdrawn: "Decision withdrawn",
      deferred: "Kept unresolved",
      stale: "Stale decision · Needs review",
      current: "Current decision",
    })[record?.resolution_state] || "Recorded decision";
  }
  function decisionIssue(value) {
    return ({
      binding_changed: "The requirement or item changed.",
      source_pins_changed: "Supporting source evidence changed.",
      current_target_missing: "The requirement or item is no longer current.",
      document_run_changed: "The document reading changed.",
    })[value] || applicabilityReason(value);
  }
  function decisionHistory(evaluation) {
    const records = list(view.rule_applicability?.decision_records).filter((record) =>
      record?.payload?.requirement_id === evaluation.requirement_id &&
      record?.payload?.object_id === evaluation.object_id);
    if (!records.length && evaluation?.decision && typeof evaluation.decision === "object") records.push(evaluation.decision);
    return records.sort((left, right) => Number(right?.sequence || 0) - Number(left?.sequence || 0));
  }
  function appendDecisionRecords(records, target, summary, showContext = false) {
    if (!records.length) return;
    const history = node("details", "", "workflow-record");
    history.append(node("summary", summary));
    for (const record of records) {
      const payload = record?.payload || {};
      const actor = typeof payload.actor === "string" && payload.actor.trim() ? payload.actor.trim() : "Unknown reviewer";
      const reason = typeof payload.reason === "string" && payload.reason.trim() ? payload.reason.trim() : "No reason recorded";
      const when = typeof record?.recorded_at === "string" && record.recorded_at.trim() ? " · " + record.recorded_at.trim() : "";
      if (showContext) {
        const item = typeof record?.object_label === "string" && record.object_label.trim() ?
          record.object_label.trim() : "Previous mechanical item";
        const requirement = typeof record?.requirement_text === "string" && record.requirement_text.trim() ?
          record.requirement_text.trim().slice(0, 180) : "Previous written requirement";
        history.append(node("p", item + " · " + requirement));
      }
      history.append(node("p", decisionState(record) + " · " + actor + when + " · " + reason,
        ["stale", "orphaned"].includes(record?.resolution_state) ? "error" : ""));
      const issues = [...list(record?.issues), ...list(record?.resolution_issues)].map(decisionIssue).filter(Boolean);
      if (issues.length) history.append(node("p", [...new Set(issues)].join(" · "), "error"));
      for (const evidence of list(record?.citation_sources).map(sourceRef).filter(Boolean))
        history.append(button("Show cited source", () => showSource(evidence), true));
    }
    target.append(history);
  }
  function appendDecisionHistory(evaluation, target) {
    const records = decisionHistory(evaluation);
    if (!records.length) return;
    appendDecisionRecords(records, target, decisionState(records[0]) + " · " + records.length + " history record" +
      (records.length === 1 ? "" : "s"));
  }
  function appendOrphanedDecisionHistory(target) {
    const records = list(view.rule_applicability?.decision_records).filter((record) =>
      record?.resolution_state === "orphaned").sort((left, right) =>
      Number(right?.sequence || 0) - Number(left?.sequence || 0));
    if (!records.length) return;
    appendDecisionRecords(records, target, "Previous unlinked item decisions · " + records.length + " record" +
      (records.length === 1 ? "" : "s"), true);
  }
  function admissionStatus(value) {
    return ({
      candidate: "Candidate",
      validated: "Checks passed",
      validation_failed: "Checks did not pass",
      unsupported: "Wording not supported",
      review_passed: "Review passed",
      review_rejected: "Review did not pass",
      approved: "Approved for requirement applicability",
      superseded: "Superseded version",
      withdrawn: "Approval withdrawn",
      stale: "Stale · Needs new checks",
      outside_current_reading: "Outside current document reading",
    })[value] || "Needs review";
  }
  function admissionIssue(value) {
    if (typeof value === "string") return applicabilityReason(value);
    if (value && typeof value === "object" && typeof value.message === "string" && value.message.trim())
      return value.message.trim();
    return "This rule record has an unresolved issue.";
  }
  function admissionAction(value) {
    return ({
      review_pass: "Record review: passes",
      review_reject: "Record review: does not pass",
      approve: "Approve requirement applicability",
      withdraw: "Withdraw approval",
    })[value] || null;
  }
  async function readRuleCases(input) {
    const selected = input.files?.[0];
    if (!selected) throw new Error("Choose a JSON case file prepared during rule development.");
    if (selected.size > 256 * 1024) throw new Error("The rule case file must be 256 KB or smaller.");
    let parsed;
    try { parsed = JSON.parse(await selected.text()); }
    catch (_) { throw new Error("The rule case file is not valid JSON."); }
    const cases = Array.isArray(parsed) ? parsed :
      (parsed && typeof parsed === "object" && Array.isArray(parsed.cases) ? parsed.cases : null);
    if (!cases) throw new Error("The rule case file must be an array or an object containing a cases array.");
    return cases;
  }
  function appendAdmissionIssues(values, target) {
    const issues = [...new Set(list(values).map(admissionIssue).filter(Boolean))];
    if (issues.length) target.append(node("p", issues.join(" · "), "error"));
  }
  function appendRuleVersion(rule, version, target, readOnly = false) {
    if (!version || typeof version !== "object") return;
    const detail = node("details", "", "workflow-record");
    const sequence = Number.isInteger(version.sequence) ? "Version " + version.sequence : "Saved version";
    detail.append(node("summary", sequence + " · " + admissionStatus(version.status) +
      (version.current === true ? " · Current" : "")));
    const payload = version.payload || {};
    const validation = payload.validation || {};
    const caseCount = list(validation.cases).length || list(validation.results).length;
    const outcome = validation.passed === true ? "Checks passed" :
      (validation.passed === false ? "Checks did not pass" : "Check outcome unavailable");
    detail.append(node("p", outcome + " · " + caseCount + " independently specified case" +
      (caseCount === 1 ? "" : "s")));
    detail.append(node("p", "Scope: requirement applicability only · no quantity calculation."));
    const statement = typeof version.statement === "string" && version.statement.trim() ?
      version.statement.trim() : (typeof version.requirement_text === "string" && version.requirement_text.trim() ?
        version.requirement_text.trim() : null);
    if (statement) detail.append(node("p", statement));
    const source = sourceRef(version.source);
    if (source) detail.append(button("Show saved rule source", () => showSource(source), true));
    const author = typeof payload.author === "string" && payload.author.trim() ? payload.author.trim() : "Unknown author";
    const reason = typeof payload.reason === "string" && payload.reason.trim() ? payload.reason.trim() : "No preparation reason recorded";
    const when = typeof version.recorded_at === "string" && version.recorded_at.trim() ? " · " + version.recorded_at.trim() : "";
    detail.append(node("p", "Prepared by " + author + when + " · " + reason));
    appendAdmissionIssues(version.issues, detail);
    const events = list(rule.events).filter((event) => event?.payload?.version_id === version.id)
      .sort((left, right) => Number(right?.sequence || 0) - Number(left?.sequence || 0));
    if (events.length) {
      detail.append(node("h4", "Review and approval history"));
      for (const event of events) {
        const eventPayload = event?.payload || {};
        const action = admissionAction(eventPayload.action) || "Recorded rule action";
        const actor = typeof eventPayload.actor === "string" && eventPayload.actor.trim() ?
          eventPayload.actor.trim() : "Unknown operator";
        const eventReason = typeof eventPayload.reason === "string" && eventPayload.reason.trim() ?
          eventPayload.reason.trim() : "No reason recorded";
        const eventTime = typeof event?.recorded_at === "string" && event.recorded_at.trim() ?
          " · " + event.recorded_at.trim() : "";
        detail.append(node("p", action + " · " + actor + eventTime + " · " + eventReason));
        appendAdmissionIssues(event?.issues, detail);
      }
    } else detail.append(node("p", "No review or approval history."));
    let allowed = readOnly ? [] : [...new Set(list(version.allowed_actions).filter((action) => admissionAction(action)))];
    if (version.current !== true || version.status === "stale")
      allowed = allowed.filter((action) => action === "withdraw");
    if (allowed.length) {
        const lifecycle = node("div", "", "workflow-form");
        const action = field(lifecycle, "Rule action", "rule-action", "",
          [["", "Choose a rule action"], ...allowed.map((value) => [value, admissionAction(value)])]);
        const actionReason = field(lifecycle, "Rule action reason", "rule-action-reason", "");
        lifecycle.append(button("Save rule action", async () => {
          if (!action.value) throw new Error("Choose a rule action.");
          if (!actionReason.value.trim()) throw new Error("Enter a reason for this rule action.");
          if (await save("rule_lifecycle", {
            rule_id: rule.rule_id,
            binding_sha256: rule.binding_sha256,
            version_id: version.id,
            action: action.value,
            supersedes: rule.latest_event_id || null,
          }, actionReason.value.trim())) render();
        }));
        detail.append(lifecycle);
    }
    target.append(detail);
  }
  function appendOutsideReadingVersions(admission, target) {
    const linked = new Set(list(admission.rules).flatMap((rule) =>
      list(rule?.versions).map((version) => version?.id).filter((id) => typeof id === "string")));
    const versions = list(admission.versions).filter((version) =>
      version?.status === "outside_current_reading" && typeof version.id === "string" && !linked.has(version.id))
      .sort((left, right) => Number(right?.sequence || 0) - Number(left?.sequence || 0));
    if (!versions.length) return;
    const history = node("details", "", "workflow-record");
    history.append(node("summary", "Previous rule versions outside current reading · " + versions.length));
    const eventSource = { events: list(admission.events) };
    for (const version of versions) appendRuleVersion(eventSource, version, history, true);
    target.append(history);
  }
  function appendRuleAdmission(target) {
    const admission = view.rule_admission;
    if (!admission || typeof admission !== "object" || !Array.isArray(admission.rules)) return;
    const panel = node("details", "", "workflow-record");
    panel.append(node("summary", "Advanced: Rule checks and approval"));
    const summary = admission.summary || {};
    panel.append(node("p", Number(summary.candidate || 0) + " candidates · " +
      Number(summary.validated || 0) + " validated · " + Number(summary.approved || 0) +
      " approved for requirement applicability · " + Number(summary.stale || 0) + " stale"));
    panel.append(node("p", "Use independently specified JSON cases prepared during rule development. Project takeoff does not generate expected answers. A named reviewer must differ from the version author; the server verifies each action. Rule approval covers requirement applicability only and never calculates quantities.", "section-hint"));
    for (const rule of admission.rules) {
      if (!rule || typeof rule !== "object") continue;
      const statement = typeof rule.statement === "string" && rule.statement.trim() ?
        rule.statement.trim() : "Rule wording unavailable";
      const ruleDetail = node("details", "", "workflow-record");
      ruleDetail.append(node("summary", statement.slice(0, 180)));
      ruleDetail.append(node("p", statement), node("p", admissionStatus(rule.status)));
      const ruleSource = sourceRef(rule.source);
      if (ruleSource) ruleDetail.append(button("Show rule source", () => showSource(ruleSource), true));
      if (typeof rule.rule_id === "string" && typeof rule.binding_sha256 === "string") {
        const prepare = node("div", "", "workflow-form");
        const fileLabel = node("label", "Validation case file", "field-label");
        const caseFile = document.createElement("input");
        caseFile.type = "file";
        caseFile.accept = ".json";
        fileLabel.append(caseFile);
        const prepareReason = field(prepare, "Version preparation reason", "rule-version-reason", "");
        prepare.append(fileLabel, button("Run rule checks and save version", async () => {
          if (!prepareReason.value.trim()) throw new Error("Enter a reason for preparing this rule version.");
          const cases = await readRuleCases(caseFile);
          if (await save("rule_prepare", {
            rule_id: rule.rule_id,
            binding_sha256: rule.binding_sha256,
            cases,
          }, prepareReason.value.trim())) render();
        }));
        ruleDetail.append(prepare);
      }
      const versions = list(rule.versions).slice().sort((left, right) =>
        Number(right?.sequence || 0) - Number(left?.sequence || 0));
      if (versions.length) {
        ruleDetail.append(node("h4", "Saved versions"));
        for (const version of versions) appendRuleVersion(rule, version, ruleDetail);
      } else ruleDetail.append(node("p", "No saved rule-check version."));
      panel.append(ruleDetail);
    }
    appendOutsideReadingVersions(admission, panel);
    target.append(panel);
  }
  function appendModelFields(target, entries) {
    const fields = node("dl", "", "evidence-details");
    for (const [title, value] of entries) {
      fields.append(node("dt", title), node("dd", value == null ? "Unknown" : String(value)));
    }
    target.append(fields);
  }
  function modelMetric(value, percent = false) {
    if (typeof value !== "number" || !Number.isFinite(value))
      return percent ? "Unknown (zero or unavailable denominator)" : "Unknown";
    return percent ? (value * 100).toFixed(2) + "%" : String(value);
  }
  function ductEvaluationValue(value, unit = "") {
    if (value == null || (typeof value === "number" && !Number.isFinite(value))) return "Unavailable";
    return (typeof value === "object" ? JSON.stringify(value) : String(value)) + (unit ? " " + unit : "");
  }
  function ductEvaluationBounds(value) {
    return value && value.lower != null && value.upper != null ?
      "[" + ductEvaluationValue(value.lower) + ", " + ductEvaluationValue(value.upper) + "]" : "Unavailable";
  }
  function ductConnectionRelation(value) {
    return ({ connected: "Connected", not_connected: "Not connected", uncertain: "Uncertain" })[value] || "No explicit assertion";
  }
  function appendDuctConnectionMetrics(topology, target) {
    const complete = topology.state === "evaluated";
    appendModelFields(target, [["Independently annotated connection pairs", ductEvaluationValue(topology.truth_count)],
      ["Correct connection pairs", ductEvaluationValue(topology.correct)],
      ["Incorrect connection pairs", ductEvaluationValue(topology.incorrect)],
      ["Unresolved connection pairs", ductEvaluationValue(topology.unresolved)],
      ["Connection pairs without matched geometry", ductEvaluationValue(topology.unmatched_geometry)],
      ["Known-pair accuracy", ductEvaluationValue(complete ? topology.known_pair_accuracy : null)],
      ["Connection coverage", ductEvaluationValue(complete ? topology.coverage : null)]]);
  }
  function appendDuctConnectionEvidence(assertion, evidence, source, title, target) {
    for (const id of list(assertion?.evidence_ids)) {
      const region = list(evidence).find((entry) => entry?.id === id);
      if (!region) continue;
      target.append(node("p", title + " evidence · " + id + (region.text ? " · " + region.text : ""), "section-hint"));
      const box = region.bbox;
      if (source && Array.isArray(box) && box.length === 4 && box.every((v) => typeof v === "number" && Number.isFinite(v)) &&
          0 <= box[0] && box[0] < box[2] && box[2] <= 1 && 0 <= box[1] && box[1] < box[3] && box[3] <= 1)
        target.append(button("Show " + title.toLowerCase() + " connection evidence · " + id,
          () => showSource({ ...source, bbox: box }), true));
    }
  }
  function appendDuctSampleTopology(sample, target) {
    const topology = sample.topology;
    if (!topology || typeof topology !== "object") return;
    const detail = node("details", "", "workflow-record");
    detail.append(node("summary", "Explicit connection pairs"));
    const source = sourceRef(sample.source);
    if (topology.state === "unavailable") {
      detail.append(node("p", topology.reason === "producer_contract_has_no_topology" ?
        "Connection diagnostics unavailable: the retained producer version has no connection assertions." :
        "Connection diagnostics unavailable: no independent connection truth is recorded.", "section-hint"));
      appendModelFields(detail, [["Independently annotated connection pairs", ductEvaluationValue(topology.truth_count)]]);
    } else {
      appendModelFields(detail, [["Connection comparison", topology.state === "evaluated" ? "Evaluated explicit pairs" :
        "Indeterminate - geometry correspondence is uncertain"]]);
      appendDuctConnectionMetrics(topology, detail);
      if (topology.state !== "evaluated") detail.append(node("p",
        "Connection comparisons remain indeterminate until geometry has one definite assignment.", "error"));
      for (const pair of list(topology.rows)) {
        const row = node("details", "", "workflow-record");
        row.append(node("summary", "Connection pair · " + pair.truth_id));
        appendModelFields(row, [["Connection result", ({ correct: "Correct", incorrect: "Incorrect", unresolved: "Unresolved",
          unmatched_geometry: "Unmatched geometry", indeterminate: "Indeterminate" })[pair.state] || "Unavailable"],
          ["Independent pair ID", pair.truth_id], ["Independent path IDs", list(pair.truth_members).join(" / ") || "Unavailable"],
          ["Expected connection", ductConnectionRelation(pair.expected)],
          ["Producer assertion ID", ductEvaluationValue(pair.prediction_id)],
          ["Producer path IDs", list(pair.prediction_members).join(" / ") || "Unavailable"],
          ["Observed connection", ductConnectionRelation(pair.observed)]]);
        appendDuctConnectionEvidence(list(topology.truth_assertions).find((entry) => entry.id === pair.truth_id),
          topology.truth_evidence, source, "Independent", row);
        appendDuctConnectionEvidence(list(topology.prediction_assertions).find((entry) => entry.id === pair.prediction_id),
          topology.prediction_evidence, source, "Producer", row);
        detail.append(row);
      }
      appendModelFields(detail, [["Unscored producer connection IDs", list(topology.unscored_prediction_ids).join(", ") || "None"]]);
    }
    const retained = topology.state === "unavailable" ? list(topology.prediction_assertions) :
      list(topology.prediction_assertions).filter((assertion) => list(topology.unscored_prediction_ids).includes(assertion.id));
    for (const assertion of retained) {
      const claim = node("details", "", "workflow-record");
      claim.append(node("summary", "Unscored producer connection · " + assertion.id));
      appendModelFields(claim, [["Producer path IDs", list(assertion.members).join(" / ")],
        ["Observed connection", ductConnectionRelation(assertion.relation)]]);
      appendDuctConnectionEvidence(assertion, topology.prediction_evidence, source, "Producer", claim);
      detail.append(claim);
    }
    if (topology.state === "unavailable") for (const assertion of list(topology.truth_assertions)) {
      const truth = node("details", "", "workflow-record");
      truth.append(node("summary", "Independent connection · " + assertion.id));
      appendModelFields(truth, [["Independent path IDs", list(assertion.members).join(" / ")],
        ["Expected connection", ductConnectionRelation(assertion.relation)]]);
      appendDuctConnectionEvidence(assertion, topology.truth_evidence, source, "Independent", truth);
      detail.append(truth);
    }
    target.append(detail);
  }
  function appendDuctEvaluationReport(report, target) {
    const metrics = report.metrics || {};
    const curves = ["duct-path-scoring-2", "duct-path-scoring-3"].includes(report.version);
    const connections = report.version === "duct-path-scoring-3";
    const topology = connections && report.topology && typeof report.topology === "object" ? report.topology : null;
    const pathLabel = curves ? "Centerline" : "Planar";
    const matchingUncertain = metrics.matching_state !== "definite" || metrics.assignment_ambiguous !== false;
    target.append(node("h4", "Saved duct path evaluation"), node("p",
      "These declared " + (curves ? "planar and circular centerline" : "planar") + " diagnostics compare independent truth with retained, uncorrected producer results. They do not approve physical quantities, a model or complete takeoff accuracy.", "section-hint"));
    appendModelFields(target, [
      ["Evaluation task", "duct-evaluation-1"], ["Matching state", metrics.matching_state],
      ["Assignment ambiguity", metrics.assignment_ambiguous === true ? "Present" :
        metrics.assignment_ambiguous === false ? "Absent" : "Unavailable"],
      ["Assigned paths", ductEvaluationValue(metrics.true_positive)],
      ["Extra predictions", ductEvaluationValue(metrics.false_positive)],
      ["Missing truth paths", ductEvaluationValue(metrics.false_negative)],
      ["Definite matches", ductEvaluationValue(metrics.definite_matches)],
      ["Possible matches", ductEvaluationValue(metrics.possible_matches)],
      [pathLabel + " precision", ductEvaluationValue(metrics.precision)],
      [pathLabel + " recall", ductEvaluationValue(metrics.recall)],
      ["Precision bounds", ductEvaluationBounds(metrics.precision_bounds)],
      ["Recall bounds", ductEvaluationBounds(metrics.recall_bounds)],
      ["Size accuracy on scored pairs", ductEvaluationValue(metrics.size_accuracy)],
      ["Work-status accuracy on scored pairs", ductEvaluationValue(metrics.work_status_accuracy)],
      ["Supported length coverage", ductEvaluationValue(metrics.length_coverage)],
      ["Maximum absolute length error", ductEvaluationValue(metrics.max_absolute_length_error_ft, "ft")],
      [curves ? "Declared centerline criteria" : "Declared planar criteria", report.thresholds_met === true && matchingUncertain ? "Unavailable - matching remains uncertain" :
        report.thresholds_met === true ? "Met for these declared diagnostics" :
        report.thresholds_met === false ? "Not met for these declared diagnostics" : "Unavailable"],
      ["Topology", topology ? (topology.state === "evaluated" ? "Explicit pair diagnostics available" :
        "Incomplete - some samples are unavailable or indeterminate") : connections ?
        "Unavailable - no independent connection truth is recorded" : "Unavailable - producer has no connection-edge assertions"],
      ["Measured resource use", "Unavailable - retained duct results have no latency or memory measurements"],
      ["Quantity authority", report.quantity_authority], ["Execution evidence", report.execution_proof],
      ["Scorer version", report.version], ["Scorer SHA-256", report.scorer_sha256], ["Report SHA-256", report.sha256],
    ]);
    if (topology) {
      target.append(node("h4", "Explicit connection diagnostics"));
      appendDuctConnectionMetrics(topology, target);
      appendModelFields(target, [["Samples without connection diagnostics", ductEvaluationValue(topology.unavailable_samples)],
        ["Samples with indeterminate connections", ductEvaluationValue(topology.indeterminate_samples)]]);
      target.append(node("p", "Only independently annotated, same-sheet path pairs are scored. Known-pair accuracy is correct pairs divided by all annotated pairs; coverage is correct plus incorrect pairs divided by all annotated pairs. Unlisted pairs are unknown. These diagnostics have no acceptance threshold and do not change measured lengths or fitting counts.", "section-hint"));
      if (topology.state !== "evaluated") target.append(node("p",
        "Overall connection accuracy and coverage remain unavailable while any sample lacks diagnostics or has uncertain geometry correspondence.", "error"));
    }
    target.append(node("p", "Length coverage includes all truth paths with an independently known expected length, including unmatched truth. Unsupported predictions remain unscored and do not rescue a missing " + (curves ? "centerline" : "planar") + " path.", "section-hint"));
    if (curves) target.append(node("p", "Distance bounds cover the complete drawn path, including selected circular spans. A tolerance inside the bounds remains indeterminate. Crossing paths do not establish a physical connection.", "section-hint"));
    if (metrics.matching_state === "indeterminate") target.append(node("p",
      "Pairing is indeterminate. Definite and possible match bounds remain separate; assigned size, status and length diagnostics do not establish a definitive matching success.", "error"));
    if (metrics.assignment_ambiguous === true) target.append(node("p",
      "More than one maximum matching is possible. Geometric match counts remain valid, but assigned size, status and length comparisons are ambiguous and cannot satisfy the declared criteria.", "error"));
    for (const sample of list(report.samples)) {
      const detail = node("details", "", "workflow-record");
      detail.append(node("summary", "Duct sample · " + sample.sample_id + " · " + (sample.matching_state || "Unknown matching state")));
      appendModelFields(detail, [["Sample ID", sample.sample_id], ["Producer ID", sample.producer_id],
        ["Producer result SHA-256", sample.producer_result_sha256], ["Input SHA-256", sample.input_sha256],
        ["Response SHA-256", sample.response?.sha256], ["Response bytes", sample.response?.bytes],
        ["Revision ID", sample.source?.revision_id], ["Page (one-based)", Number.isInteger(sample.source?.index) ? sample.source.index + 1 : null],
        ["Recorded page index", sample.source?.index], ["Sheet ID", sample.source?.sheet_id],
        ["Geometry fingerprint", sample.source?.geometry_fingerprint],
        ["Truth paths", ductEvaluationValue(sample.truth_count)],
        ["All predictions", ductEvaluationValue(sample.prediction_count)],
        ["Supported planar predictions", ductEvaluationValue(sample.planar_prediction_count)],
        ["Definite matches", ductEvaluationValue(sample.definite_matches)],
        ["Possible matches", ductEvaluationValue(sample.possible_matches)]]);
      if (curves) appendModelFields(detail, [
        ["All supported centerline predictions", ductEvaluationValue(sample.supported_prediction_count)],
        ["Supported circular predictions", ductEvaluationValue(sample.circular_prediction_count)],
        ["Supported circular-span predictions", ductEvaluationValue(sample.span_prediction_count)]]);
      const source = sourceRef(sample.source);
      if (source) detail.append(button("Show evaluation source", () => showSource(source), true));
      if (sample.matching_state === "indeterminate") detail.append(node("p",
        "This sample has indeterminate pairs. Unassigned paths and all assigned classification/length diagnostics retain that qualification.", "error"));
      if (sample.assignment_ambiguous === true) detail.append(node("p",
        "This sample has alternate maximum assignments. Displayed pairs do not establish definitive classification or length results.", "error"));
      appendModelFields(detail, [["Missing / unassigned truth path IDs", list(sample.unmatched_truth_ids).join(", ") || "None"],
        ["Extra / unassigned prediction IDs", list(sample.unmatched_prediction_ids).join(", ") || "None"],
        ["Duplicate-like geometry IDs (still extra predictions)", list(sample.duplicate_like_prediction_ids).join(", ") || "None"]]);
      if (list(sample.duplicate_like_prediction_ids).length) detail.append(node("p",
        "Duplicate-like geometry is an evaluation diagnostic, not an authorized physical duplicate.", "section-hint"));
      for (const prediction of list(sample.unscored_predictions)) detail.append(node("p",
        "Unscored prediction " + prediction.id + " · " + prediction.kind + " · " + prediction.reason, "error"));
      const pairs = node("details", "", "workflow-record");
      pairs.append(node("summary", "Geometry pair bounds · " + list(sample.pairs).length));
      for (const pair of list(sample.pairs)) pairs.append(node("p", pair.truth_id + " / " + pair.prediction_id +
        " · " + pair.state + " · distance [" + ductEvaluationValue(pair.lower_pt) + ", " +
        ductEvaluationValue(pair.upper_pt) + "] pt", pair.state === "indeterminate" ? "error" : ""));
      detail.append(pairs);
      for (const pair of list(sample.matched_pairs)) {
        const matched = node("details", "", "workflow-record");
        matched.append(node("summary", "Assigned pair · " + pair.truth_id + " / " + pair.prediction_id));
        appendModelFields(matched, [["Matching qualification", pair.matching_state || sample.matching_state]]);
        for (const [key, title] of [["size", "Size"], ["work_status", "Work status"], ["system", "System"], ["material", "Material"]]) {
          const result = pair[key] || {};
          appendModelFields(matched, [[title + " comparison", result.state],
            [title + " expected", ductEvaluationValue(result.expected)], [title + " observed", ductEvaluationValue(result.observed)]]);
        }
        const length = pair.length || {};
        appendModelFields(matched, [["Independent expected length", ductEvaluationValue(length.expected_meters, "m")],
          ["Measured candidate length", ductEvaluationValue(length.measured_meters, "m")],
          ["Absolute length error", ductEvaluationValue(length.absolute_error_ft, "ft")],
          ["Exact error ratio (feet)", ductEvaluationValue(length.absolute_error_ft_ratio)]]);
        for (const issue of list(length.issues)) matched.append(node("p",
          "Length unavailable: " + (typeof issue === "string" ? issue : [issue.code, issue.message].filter(Boolean).join(" · ")), "error"));
        detail.append(matched);
      }
      if (connections) appendDuctSampleTopology(sample, detail);
      target.append(detail);
    }
    const criteria = node("details", "", "equipment-audit");
    criteria.append(node("summary", "Declared criteria and measurement dependencies"),
      node("pre", JSON.stringify({ criteria: report.criteria, measurement_dependencies: report.measurement_dependencies }, null, 2)));
    target.append(criteria);
  }
  function appendModelReport(report, target, task) {
    if (task === "duct-evaluation-1") {
      appendDuctEvaluationReport(report, target);
      return;
    }
    const metrics = report.metrics || {};
    target.append(node("h4", "Saved-output detection result"));
    appendModelFields(target, [
      ["True positives", modelMetric(metrics.true_positive)],
      ["False positives", modelMetric(metrics.false_positive)],
      ["False negatives", modelMetric(metrics.false_negative)],
      ["Precision", modelMetric(metrics.precision, true)],
      ["Recall", modelMetric(metrics.recall, true)],
      ["Supplied mean latency (ms)", modelMetric(metrics.mean_latency_ms)],
      ["Supplied peak memory (MB)", modelMetric(metrics.peak_memory_mb)],
      ["Declared experiment thresholds", report.thresholds_met === true ? "Met on supplied outputs" :
        report.thresholds_met === false ? "Not met on supplied outputs" : "Unknown"],
      ["Scorer version", report.version], ["Scorer SHA-256", report.scorer_sha256],
      ["Report SHA-256", report.sha256],
    ]);
    for (const category of list(report.categories)) {
      target.append(node("p", label(category.category) + " · " + modelMetric(category.samples) +
        " samples · TP " + modelMetric(category.true_positive) + " · FP " + modelMetric(category.false_positive) +
        " · FN " + modelMetric(category.false_negative) + " · precision " + modelMetric(category.precision, true) +
        " · recall " + modelMetric(category.recall, true), "section-hint"));
    }
  }
  function appendModelRecord(record, target) {
    const payload = record.payload || {};
    const detail = node("details", "", "workflow-record");
    detail.append(node("summary", label(record.kind) + " · " +
      (payload.name || payload.plan_id || payload.dataset_id || record.id)));
    appendModelFields(detail, [
      ["Record ID", record.id], ["Project ID", record.project_id],
      ["Recorded by", record.actor], ["Recorded at", record.recorded_at], ["Reason", record.reason],
    ]);
    if (record.kind !== "model") appendModelFields(detail, [["Evaluation task",
      payload.task === "duct-evaluation-1" ? "Duct path evaluation · duct-evaluation-1" :
        payload.task || "Bounding-box detection (legacy)"]]);
    if (payload.task === "duct-evaluation-1") appendModelFields(detail, [["Evidence state",
      list(record.issues).length ? "Needs resolution - retained historical evidence" : "Current recorded evidence"]]);
    if (record.kind === "dataset") {
      detail.append(node("p", "Version " + payload.version + " · " + list(payload.samples).length +
        " samples · " + list(payload.scope).map(label).join(", ")));
      detail.append(node("p", "Annotation basis: " + (payload.annotation_basis || "Unknown")));
    } else if (record.kind === "model") {
      appendModelFields(detail, [["Declared version", payload.version],
        ["Model manifest SHA-256 (for run linking)", record.model_manifest_sha256],
        ["Declared runtime", [payload.runtime?.name, payload.runtime?.version].filter(Boolean).join(" · ")]]);
      detail.append(node("p", "Artifact and runtime declarations do not verify installation or execution.", "section-hint"));
    } else if (record.kind === "plan") {
      appendModelFields(detail, [["Dataset ID", payload.dataset_id], ["Model ID", payload.model_id],
        ["Evaluation split", payload.split], ["Pinned scorer version", record.scorer_identity?.version],
        ["Pinned scorer SHA-256", record.scorer_identity?.sha256]]);
    } else if (record.kind === "run") {
      appendModelFields(detail, [["Plan ID", payload.plan_id],
        ["Model manifest SHA-256", payload.model_manifest_sha256]]);
      if (payload.report && typeof payload.report === "object") appendModelReport(payload.report, detail, payload.task);
    }
    for (const issue of list(record.issues))
      detail.append(node("p", "Evidence issue: " + (typeof issue === "string" ? issue : JSON.stringify(issue)), "error"));
    const raw = node("details", "", "equipment-audit");
    raw.append(node("summary", "Exact saved record and source pins"), node("pre", JSON.stringify(record, null, 2)));
    detail.append(raw);
    target.append(detail);
  }
  async function readModelManifest(input) {
    const selected = input.files?.[0];
    if (!selected) throw new Error("Choose an independently prepared JSON evidence file.");
    if (selected.size > 56 * 1024) throw new Error("The model evidence file must be 56 KiB (57,344 bytes) or smaller.");
    let parsed;
    try { parsed = JSON.parse(await selected.text()); }
    catch (_) { throw new Error("The model evidence file is not valid JSON."); }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
      throw new Error("The model evidence file must contain one payload object.");
    return parsed;
  }
  function modelInferenceActive() {
    return list(view?.model_inference?.jobs).some((job) => ["queued", "running"].includes(job.state));
  }
  function modelInferenceReason() {
    const reason = modelInferenceDraft.reason.trim();
    if (!reason) throw new Error("Enter a reason for this inference run or recovery action.");
    return reason;
  }
  function appendModelInference(target, baseline) {
    const inference = view.model_inference;
    if (!inference || typeof inference !== "object") return;
    const configured = inference.configured === true;
    const active = modelInferenceActive();
    const controls = node("div", "", "workflow-form");
    const pageAvailable = view.preview_mode === "image" && list(view.inventory).some((page) =>
      page.revision_id === selection?.revision && page.index === selection?.index);
    controls.append(node("p", sourceName(), "workflow-current-source"));
    const prepare = button("Prepare selected drawing as model input", async () => {
      if (!pageAvailable) throw new Error("Select an accepted drawing with a configured image renderer.");
      if (await save("model_input", { source: source() }, "Preserved the selected drawing as a frozen model input")) render();
    }, true);
    prepare.disabled = busy || !pageAvailable;
    const identity = button("Read configured local model identity", async () => {
      if (!configured) throw new Error("A local model must be configured first.");
      if (await save("model_identity", {}, "Read the configured local model and runtime identities")) render();
    }, true);
    identity.disabled = busy || !configured || active;
    controls.append(prepare, identity);
    if (view.preview_mode !== "image")
      controls.append(node("p", "Configure a local PDF renderer to prepare drawing images.", "section-hint"));
    if (!configured)
      controls.append(node("p", "Local inference is not configured. Saved inputs and jobs remain available.", "section-hint"));
    target.append(node("h4", "Frozen drawing inputs and local inference"), controls);
    const inputPanel = node("details", "", "workflow-record");
    inputPanel.append(node("summary", "Prepared page inputs · " + list(inference.inputs).length));
    for (const input of list(inference.inputs)) {
      const detail = node("details", "", "workflow-record");
      detail.append(node("summary", (input.sheet_id || input.revision_id || "Drawing") +
        " · page " + (Number.isInteger(input.index) ? input.index + 1 : "Unknown")));
      appendModelFields(detail, [["Input record ID", input.id], ["Dataset source_id", input.source_id],
        ["Dataset input_sha256", input.input_sha256], ["PNG SHA-256", input.image?.sha256],
        ["PNG bytes", input.image?.bytes], ["Image width", input.image?.width],
        ["Image height", input.image?.height], ["Revision ID", input.revision_id], ["Role", input.role]]);
      const source = sourceRef(input);
      if (source) detail.append(button("Show original drawing", () => showSource(source), true));
      const raw = node("details", "", "equipment-audit");
      raw.append(node("summary", "Exact input and renderer record"), node("pre", JSON.stringify(input, null, 2)));
      detail.append(raw);
      inputPanel.append(detail);
    }
    target.append(inputPanel);
    const observed = node("details", "", "workflow-record");
    observed.append(node("summary", "Last explicitly read local model identity"));
    if (view.model_inference_identity) {
      observed.append(node("p", "Copy this exact object into the model declaration's preprocessing.execution_identity. Match its model digest and runtime name, version and hash in the declaration.", "section-hint"),
        node("pre", JSON.stringify(view.model_inference_identity, null, 2)));
    } else observed.append(node("p", "No local model identity has been read in this session."));
    target.append(observed);
    const currentPlans = list(baseline.plans).filter((plan) => !list(plan.issues).length &&
      !Object.prototype.hasOwnProperty.call(plan.payload || {}, "task"));
    if (!currentPlans.some((plan) => plan.id === modelInferenceDraft.plan_id)) modelInferenceDraft.plan_id = "";
    const runForm = node("div", "", "workflow-form");
    const plan = field(runForm, "Current frozen evaluation plan", "model-inference-plan", modelInferenceDraft.plan_id,
      [["", "Choose a current plan"], ...currentPlans.map((entry) => [entry.id,
        (list(baseline.datasets).find((dataset) => dataset.id === entry.payload?.dataset_id)?.payload?.name || "Dataset") +
        " · " + (entry.payload?.split || "Unknown split") + " · " + entry.id])]);
    const reason = field(runForm, "Inference run / recovery reason", "model-inference-reason", modelInferenceDraft.reason);
    plan.addEventListener("change", () => { modelInferenceDraft.plan_id = plan.value; });
    reason.addEventListener("input", () => { modelInferenceDraft.reason = reason.value; });
    const start = button("Run local inference for selected plan", async () => {
      if (!configured) throw new Error("A local model must be configured first.");
      if (!currentPlans.some((entry) => entry.id === plan.value)) throw new Error("Choose a current frozen plan first.");
      modelInferenceDraft.plan_id = plan.value;
      modelInferenceDraft.reason = reason.value;
      if (await save("model_infer", { plan_id: plan.value }, modelInferenceReason())) render();
    });
    start.disabled = busy || !configured || active || !currentPlans.length;
    runForm.append(start);
    if (!currentPlans.length) runForm.append(node("p", "Register a current plan using the prepared PNG sources and observed model identity. Earlier plans with evidence issues remain in history below.", "section-hint"));
    target.append(runForm, node("p", "Inference sends the frozen page images to the configured local model. Expected labels stay with the scorer. Model detections are saved observations; memory remains unknown when it was not measured.", "section-hint"));
    if (list(baseline.plans).some((entry) => entry.payload?.task === "duct-evaluation-1")) target.append(node("p",
      "Duct path plans use saved duct producer results through the evidence importer. They are excluded from this bounding-box inference picker.", "section-hint"));
    appendModelFields(target, [["Active job ID", inference.running_job_id || "None"]]);
    const jobs = node("details", "", "workflow-record");
    jobs.open = active;
    jobs.append(node("summary", "Local inference jobs · " + list(inference.jobs).length));
    for (const job of list(inference.jobs).slice().reverse()) {
      const detail = node("details", "", "workflow-record");
      detail.open = ["queued", "running"].includes(job.state);
      const samples = list(job.samples);
      const objectCount = samples.reduce((count, sample) => count + list(sample.objects).length, 0);
      detail.append(node("summary", label(job.state || "unknown") + " · " + modelMetric(job.completed_samples) +
        "/" + modelMetric(job.total_samples) + " pages · " + objectCount + " model-generated objects"));
      appendModelFields(detail, [["Job ID", job.id], ["Plan ID", job.plan_id],
        ["Baseline run ID", job.baseline_run_id || "Not registered"]]);
      if (job.error) detail.append(node("p", (job.error.code || "Inference error") + ": " +
        (job.error.message || "No error detail available"), "error"));
      for (const sample of samples) {
        const page = node("details", "", "workflow-record");
        page.append(node("summary", sample.sample_id + " · " + list(sample.objects).length +
          " model-generated objects · " + (sample.unreadable === true ? "Unreadable" :
            sample.unreadable === false ? "Readable" : "Readability unknown")));
        appendModelFields(page, [["Input source ID", sample.input_source_id], ["PNG SHA-256", sample.image_sha256],
          ["Measured latency (ms)", modelMetric(sample.latency_ms)], ["Peak memory (MB)", modelMetric(sample.peak_memory_mb)],
          ["Raw response SHA-256", sample.response?.sha256], ["Raw response bytes", sample.response?.bytes]]);
        if (sample.unreadable === true) page.append(node("p", "Unreadable page: no detections; expected objects count as misses.", "error"));
        if (sample.error) page.append(node("p", (sample.error.code || "Page failed") + ": " +
          (sample.error.message || "No error detail available"), "error"));
        page.append(node("pre", JSON.stringify(sample.objects || [], null, 2)));
        detail.append(page);
      }
      if (["queued", "running"].includes(job.state)) {
        detail.append(button("Cancel local inference", async () => {
          if (await save("model_infer_cancel", { job_id: job.id }, modelInferenceReason())) render();
        }, true));
      } else if (["cancelled", "interrupted", "failed"].includes(job.state)) {
        const resume = button("Resume local inference", async () => {
          if (!configured) throw new Error("A local model must be configured first.");
          if (await save("model_infer_resume", { job_id: job.id }, modelInferenceReason())) render();
        }, true);
        resume.disabled = busy || !configured || active;
        detail.append(resume);
      }
      const raw = node("details", "", "equipment-audit");
      raw.append(node("summary", "Exact job, observed identity and response records"), node("pre", JSON.stringify(job, null, 2)));
      detail.append(raw);
      jobs.append(detail);
    }
    target.append(jobs);
  }
  function appendModelBaseline(target) {
    const baseline = view.model_baseline;
    if (!baseline || typeof baseline !== "object") return;
    const panel = node("details", "", "workflow-record");
    panel.open = modelPanelOpen;
    panel.addEventListener("toggle", () => { modelPanelOpen = panel.open; });
    panel.append(node("summary", "Advanced: Local model evidence"));
    panel.append(node("p", "This panel supplies local model experiments during development. It is separate from daily takeoff instructions and does not complete a takeoff stage or approve quantities.", "section-hint"));
    panel.append(node("p", list(baseline.datasets).length + " datasets · " + list(baseline.models).length +
      " model declarations · " + list(baseline.plans).length + " frozen plans · " + list(baseline.runs).length + " saved runs"));
    panel.append(node("p", "Detection scores compare saved predictions with independent labels. Legacy imported runs retain supplied performance measurements; local inference jobs separately record images, model responses and elapsed time. Duct path reports use retained producer results and source-supported lengths. Explicit connection pairs are scored separately when independent truth is available. These results do not establish final quantities, complete network topology or takeoff accuracy.", "section-hint"));
    appendModelFields(panel, [["Quantity authority", baseline.quantity_authority],
      ["Baseline evidence type", baseline.execution_proof], ["Evidence state fingerprint", baseline.state_fingerprint]]);
    appendModelInference(panel, baseline);
    const coverage = node("ul", "", "workflow-record-list");
    for (const entry of list(baseline.coverage)) {
      coverage.append(node("li", label(entry.category) + " · " + modelMetric(entry.samples) +
        " registered samples · " + modelMetric(entry.evaluated_samples) + " evaluated samples"));
    }
    panel.append(node("h4", "Category coverage"), coverage,
      node("p", "Zero coverage provides no performance evidence for that category. Precision or recall with a zero denominator remains unknown.", "section-hint"));
    if (Array.isArray(baseline.duct_coverage)) {
      const ductCoverage = node("ul", "", "workflow-record-list");
      for (const entry of baseline.duct_coverage) ductCoverage.append(node("li", label(entry.category) + " · " +
        modelMetric(entry.samples) + " registered samples · " + modelMetric(entry.evaluated_samples) + " evaluated samples"));
      panel.append(node("h4", "Duct path evaluation coverage"), ductCoverage, node("p",
        "Duct samples are counted separately from bounding-box category coverage. Unscored geometry and unavailable topology are retained in each report.", "section-hint"));
    }
    const sources = new Map();
    for (const entry of [...list(view.project_knowledge?.sources), ...list(view.model_sources)])
      if (entry && typeof entry.id === "string") sources.set(entry.id, entry);
    const sourcePanel = node("details", "", "workflow-record");
    sourcePanel.append(node("summary", "Reusable source evidence · " + sources.size));
    sourcePanel.append(node("p", "Use the source ID and exact snapshot SHA-256 in dataset samples. Source rights are recorded provenance statements and do not grant permission to train, upload or redistribute.", "section-hint"));
    for (const sourceEntry of sources.values()) {
      const detail = node("details", "", "workflow-record");
      detail.append(node("summary", (sourceEntry.metadata?.title || "Source") + " · " + (sourceEntry.state || "Unknown state")));
      appendModelFields(detail, [["Source ID", sourceEntry.id], ["Snapshot SHA-256", sourceEntry.snapshot_sha256],
        ["Rights basis", sourceEntry.metadata?.rights_basis], ["Locator", sourceEntry.metadata?.locator]]);
      if (sourceEntry.state !== "current")
        detail.append(node("p", "Source is not current. Dependent evidence needs resolution before a new plan or run can be saved.", "error"));
      const raw = node("details", "", "equipment-audit");
      raw.append(node("summary", "Source metadata"), node("pre", JSON.stringify(sourceEntry, null, 2)));
      detail.append(raw);
      sourcePanel.append(detail);
    }
    panel.append(sourcePanel);
    const importer = node("div", "", "workflow-form");
    const kind = field(importer, "Evidence kind", "model-evidence-kind", "", [["", "Choose evidence kind"],
      ["source", "Source snapshot"], ["dataset", "Dataset"], ["model", "Model declaration"],
      ["plan", "Evaluation plan"], ["run", "Saved prediction run"]]);
    const fileLabel = node("label", "JSON payload file (maximum 56 KiB)", "field-label");
    const manifest = document.createElement("input");
    manifest.type = "file";
    manifest.accept = ".json,application/json";
    fileLabel.append(manifest);
    const reason = field(importer, "Evidence registration reason", "model-evidence-reason", "");
    importer.append(fileLabel, button("Import local model evidence", async () => {
      const selectedKind = kind.value;
      const registrationReason = reason.value.trim();
      if (!selectedKind) throw new Error("Choose the evidence kind matching your JSON payload.");
      if (!registrationReason) throw new Error("Enter a reason for registering this evidence.");
      const payload = await readModelManifest(manifest);
      if (selectedKind === "run" && Object.prototype.hasOwnProperty.call(payload, "report"))
        throw new Error("Supply predictions only. The server computes the report and threshold outcome.");
      const action = selectedKind === "source" ? "model_source" : "model_baseline";
      const data = selectedKind === "source" ? payload : { kind: selectedKind, payload };
      if (await save(action, data, registrationReason)) render();
    }));
    panel.append(node("h4", "Register experiment evidence"),
      node("p", "Prepare dataset annotations and model declarations independently; freeze a plan before running inference or importing saved predictions. Choose one raw payload object without a kind/payload wrapper. A text source payload contains metadata and snapshot_text, limited to 48,000 UTF-8 text bytes. The importer reads local files and does not generate labels or expected outcomes.", "section-hint"), importer);
    panel.append(node("p", 'For duct path evaluation, dataset, plan and run payloads explicitly use task: "duct-evaluation-1". Saved run predictions name sample_id and producer_id; the server verifies the retained original producer result and computes diagnostics. Model declarations remain shared.', "section-hint"));
    panel.append(node("p", 'A duct dataset sample may include independent topology: {schema: "duct-topology-truth-1", evidence: [{id, bbox, text}], assertions: [{id, members: [truth_id, truth_id], relation: "connected" or "not_connected", evidence_ids: [...]}]}. Each pair names two distinct truth paths on that source sheet and cites drawing evidence; bbox uses normalized [left, top, right, bottom] coordinates. Omitted pairs remain unknown. Keep these annotations independent of model predictions.', "section-hint"));
    for (const key of ["datasets", "models", "plans", "runs"])
      for (const record of list(baseline[key])) appendModelRecord(record, panel);
    const link = node("a", "Download evidence in draft project package", "button button-outline");
    link.href = "api/workflow/export.zip";
    link.download = "heleos-draft-takeoff.zip";
    panel.append(link, node("p", "The package retains baseline records and scores, plus model-inference.json and raw response files when inference evidence exists. Saving evidence never selects or promotes a model.", "section-hint"));
    target.append(panel);
  }
  function appendDecisionControl(evaluation, target) {
    const applicability = view.rule_applicability;
    const decisionTarget = list(applicability?.targets).find((entry) => entry?.evaluation_id === evaluation.id);
    if (!decisionTarget || typeof decisionTarget.binding_sha256 !== "string") return;
    const options = [["", "Choose an item decision"]];
    if (decisionTarget.can_decide === true) options.push(["applies", "Applies"], ["excludes", "Does not apply"]);
    options.push(["defer", "Keep unresolved"]);
    if (decisionTarget.latest_decision_id) options.push(["withdraw", "Withdraw prior decision"]);
    const decisionForm = node("div", "", "workflow-form");
    const disposition = field(decisionForm, "Item decision", "rule-decision", "", options);
    const evidenceOptions = list(view.rule_decision_evidence).filter((entry) => entry?.available === true &&
      typeof entry.id === "string" && typeof entry.label === "string");
    const evidence = field(decisionForm, "Supporting evidence", "rule-evidence", "",
      [["", "Choose supporting evidence"], ...evidenceOptions.map((entry) => [entry.id, entry.label])]);
    const reason = field(decisionForm, "Decision reason", "rule-decision-reason", "");
    decisionForm.append(button("Show selected evidence", () => {
      const selected = evidenceOptions.find((entry) => entry.id === evidence.value);
      const source = sourceRef(selected?.source);
      if (!source) throw new Error("Choose available supporting evidence first.");
      showSource(source);
    }, true));
    decisionForm.append(button("Save item decision", async () => {
      if (!disposition.value) throw new Error("Choose an item decision.");
      if (!reason.value.trim()) throw new Error("Enter a reason for this item decision.");
      if (["applies", "excludes"].includes(disposition.value) && !evidence.value)
        throw new Error("Choose supporting evidence for this item decision.");
      if (await save("rule_decision", {
        evaluation_id: decisionTarget.evaluation_id,
        binding_sha256: decisionTarget.binding_sha256,
        disposition: disposition.value,
        evidence_ids: evidence.value ? [evidence.value] : [],
        supersedes: decisionTarget.latest_decision_id || null,
      }, reason.value.trim())) render();
    }));
    if (decisionTarget.can_decide !== true)
      decisionForm.append(node("p", "Resolve the source or wording issue before recording applies or does not apply.", "section-hint"));
    target.append(decisionForm);
  }
  function candidateApplicabilityDescription(requirement) {
    const applicability = view.rule_applicability;
    if (!applicability || typeof applicability !== "object")
      return "Candidate requirement · applicability and stated conditions await resolution · no quantity calculated.";
    const evaluations = list(applicability.evaluations).filter((entry) => entry?.requirement_id === requirement.id);
    if (evaluations.some((entry) => ["confirmed_applies", "confirmed_excluded"].includes(entry?.status)))
      return "Candidate requirement · a scope match has a recorded item decision below; rule approval remains separate · no quantity calculated.";
    if (evaluations.some((entry) => entry?.status === "matched"))
      return "Candidate requirement · scope matches found below; rule approval remains separate · no quantity calculated.";
    if (evaluations.length)
      return "Candidate requirement · item scope needs resolution below; rule approval remains separate · no quantity calculated.";
    if (list(applicability.unassigned_rules).some((entry) => entry?.requirement_id === requirement.id))
      return "Candidate requirement · matching item scope needs resolution below; rule approval remains separate · no quantity calculated.";
    return "Candidate requirement · no applicable item scope result is available · no quantity calculated.";
  }
  function applicabilityObjectSources(entry) {
    const object = list(view.rule_applicability?.objects).find((candidate) => candidate?.id === entry?.object_id);
    const requirementSource = sourceRef(entry?.source);
    const candidates = [...list(entry?.evidence_sources), ...list(object?.source_refs)];
    const seen = new Set();
    return candidates.map(sourceRef).filter((candidate) => {
      if (!candidate) return false;
      const key = candidate.revision_id + ":" + candidate.index + ":" + JSON.stringify(candidate.bbox || null);
      if (requirementSource && key === requirementSource.revision_id + ":" + requirementSource.index + ":" +
          JSON.stringify(requirementSource.bbox || null)) return false;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }
  function appendApplicability(requirement, target) {
    const applicability = view.rule_applicability;
    if (!applicability || typeof applicability !== "object") return;
    const evaluations = list(applicability.evaluations).filter((entry) => entry?.requirement_id === requirement.id);
    const unassigned = list(applicability.unassigned_rules).filter((entry) => entry?.requirement_id === requirement.id);
    for (const evaluation of evaluations) {
      const itemLabel = typeof evaluation.object_label === "string" && evaluation.object_label.trim() ?
        evaluation.object_label.trim() : "Unlabeled mechanical item";
      const line = node("p", itemLabel + " · " + applicabilityLabel(evaluation.status),
        ["matched", "confirmed_applies", "confirmed_excluded"].includes(evaluation.status) ? "" : "error");
      const reasons = applicabilityReasons(evaluation.reasons);
      if (reasons.length) line.append(document.createTextNode(" · " + reasons.join(" · ")));
      target.append(line);
      const requirementSource = sourceRef(evaluation?.source);
      if (requirementSource) target.append(button("Show requirement source", () => showSource(requirementSource), true));
      for (const evidence of applicabilityObjectSources(evaluation))
        target.append(button("Show item source", () => showSource(evidence), true));
      appendDecisionHistory(evaluation, target);
      appendDecisionControl(evaluation, target);
    }
    for (const entry of unassigned) {
      const reasons = applicabilityReasons(entry?.reasons);
      const line = node("p", "No matching mechanical item · " + applicabilityLabel(entry?.status), "error");
      if (reasons.length) line.append(document.createTextNode(" · " + reasons.join(" · ")));
      target.append(line);
      const evidence = sourceRef(entry?.source);
      if (evidence) target.append(button("Show requirement source", () => showSource(evidence), true));
    }
  }
  function source() {
    if (!selection?.revision || !Number.isInteger(selection.index)) throw new Error("Open a drawing page first.");
    return { revision_id: selection.revision, index: selection.index };
  }
  function sourceName() {
    if (!selection?.revision) return "No drawing page selected";
    const entry = view?.inventory.find((v) => v.revision_id === selection.revision && v.index === selection.index);
    return entry ? entry.document_name + " · page " + (entry.index + 1) : "No accepted page selected";
  }
  function showSource(value, extra = {}) {
    window.dispatchEvent(new CustomEvent("heleos:show-source", {
      detail: { ...value, bbox: value.bbox || [0, 0, 1, 1], ...extra },
    }));
  }
  async function api(path, body) {
    const response = await fetch(path, {
      credentials: "same-origin", cache: "no-store",
      ...(body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
    });
    let result;
    try { result = await response.json(); } catch (_) { throw new Error("The workflow response could not be read."); }
    if (!response.ok) throw new Error(result?.error?.message || "The workflow operation failed.");
    return result;
  }
  async function load(first = false) {
    const next = await api("api/workflow");
    if (!Array.isArray(next.stages) || !Number.isInteger(next.version)) throw new Error("The saved workflow is incomplete.");
    view = next;
    if (first) stage = view.selected_stage;
    render();
  }
  async function save(action, data, reason = "") {
    if (busy || !view) return false;
    const actor = byId("workflow-actor").value.trim();
    if (!actor && action !== "navigate") throw new Error("Enter your name above to save project records.");
    const body = { version: view.version, actor: actor || "local-operator",
      reason: reason || "Saved " + label(action).toLowerCase(), values: data };
    if (["model_baseline", "model_source"].includes(action) &&
        new TextEncoder().encode(JSON.stringify(body)).byteLength > 64 * 1024)
      throw new Error("This evidence and audit text exceed the 64 KiB request limit. Use a smaller payload.");
    busy = true;
    let conflictRefresh = false;
    error();
    content.setAttribute("aria-busy", "true");
    try {
      view = await api("api/workflow/" + action, body);
      try { sessionStorage.setItem(actorKey, actor); } catch (_) {}
      info("Saved on this computer.");
      return true;
    } catch (e) {
      const message = e.message.toLowerCase();
      const changed = message.includes("changed") || message.includes("stale") ||
        ["newer decision", "newer event", "latest event", "no longer current",
          "rule, source or implementation changed", "newer rule decision"].some((value) => message.includes(value));
      if (changed) { await load(); conflictRefresh = true; }
      throw e;
    } finally {
      busy = false;
      content.setAttribute("aria-busy", "false");
      if (conflictRefresh) render();
    }
  }
  async function navigate(next) {
    if (busy || !view) return;
    window.dispatchEvent(new CustomEvent("heleos:pick-region", { detail: false }));
    window.dispatchEvent(new CustomEvent("heleos:pick-line", { detail: false }));
    pickingView = false;
    ductPanel.cancelPicking();
    equipmentCountPanel.cancelRegion();
    if (await save("navigate", { stage: next }, "Opened " + next)) {
      stage = next;
      window.dispatchEvent(new CustomEvent("heleos:workflow-stage", { detail: stage }));
      itemEditing = null;
      pickedView = null;
      pickedLine = null;
      measurementDraft = { calibration: {}, measurement: {}, declared: {} };
      render();
    }
  }
  function render() {
    if (!view) return;
    const current = view.stages.find((v) => v.id === stage) || view.stages[0];
    stage = current.id;
    byId("workflow-project-title").textContent = view.project.name || "Set up your mechanical project";
    if (view.project.name) byId("project-name").textContent = view.project.name;
    byId("workflow-progress").textContent = view.stages.filter((v) => v.status === "reviewed").length +
      " of 6 stages reviewed · " + view.issues_view.filter((v) => v.state === "open").length + " unresolved";
    byId("workflow-stage-number").textContent = "Stage " + (view.stages.indexOf(current) + 1) + " of 7";
    byId("workflow-stage-title").textContent = current.title;
    const navigation = byId("workflow-navigation");
    navigation.replaceChildren();
    for (const [index, step] of view.stages.entries()) {
      const item = node("li");
      const control = button("", () => navigate(step.id), true);
      control.classList.add("workflow-step");
      control.setAttribute("aria-current", step.id === stage ? "step" : "false");
      control.append(node("span", String(index + 1), "workflow-step-number"),
        node("strong", step.title), node("small", label(step.status)));
      item.append(control);
      navigation.append(item);
    }
    content.replaceChildren();
    byId("workflow-equipment-tools").hidden = stage !== "equipment";
    ({ setup: setupForm, documents: documentForm, equipment: equipmentForm,
       measurements: measurementForm, takeoff: takeoffForm, exceptions: exceptionForm, export: exportForm })[stage]();
    renderReview(current);
    const capabilities = byId("workflow-capabilities");
    capabilities.replaceChildren(...view.capability_notes.map((v) => node("li", v)));
    byId("workflow-history").replaceChildren(...view.history.slice(-12).reverse().map((v) =>
      node("li", v.actor + " · " + label(v.action) + " · " + v.reason)));
    scheduleWorkflowPoll();
    Promise.resolve().then(prepareCompletedDuctReadings);
    Promise.resolve().then(prepareCompletedAirReadings);
  }
  function scheduleWorkflowPoll() {
    if (workflowPoll) clearTimeout(workflowPoll);
    workflowPoll = null;
    if ((stage === "equipment" && ["queued", "running"].includes(view.equipment?.state)) ||
        ["queued", "running"].includes(view.document_reading?.state) || modelInferenceActive() || ductPanel.controller.active() || airPanel.active()) {
      workflowPoll = setTimeout(() => {
        workflowPoll = null;
        if (busy) scheduleWorkflowPoll();
        else load().catch((e) => error(e.message));
      }, 1600);
    }
  }
  function setupForm() {
    hint("Set the job's identity and mechanical scope. These records carry through the rest of the workflow.");
    const f = form();
    field(f, "Project name", "name", view.project.name);
    field(f, "Location / jurisdiction", "location", view.project.location);
    field(f, "Working length units", "units", view.project.units, [["ft", "Feet"], ["m", "Meters"], ["in", "Inches"], ["mm", "Millimeters"]]);
    const group = node("fieldset", "", "workflow-scope-picker");
    group.append(node("legend", "Included scope"));
    for (const scope of scopes) {
      const wrapper = node("label");
      const box = document.createElement("input");
      box.type = "checkbox"; box.name = "scope"; box.value = scope;
      box.checked = view.project.scopes.includes(scope);
      wrapper.append(box, document.createTextNode(label(scope)));
      group.append(wrapper);
    }
    f.append(group, button("Save project", async () => {
      const data = values(f);
      if (await save("project", { name: data.name, location: data.location, units: data.units,
        scopes: Array.from(f.querySelectorAll('input[name="scope"]:checked'), (v) => v.value) })) render();
    }));
  }
  function documentForm() {
    hint("Read the job's pages into equipment schedule fields and written requirements. Page roles help match plans to schedules; unreadable pages remain listed.");
    content.append(button("Read project documents", async () => {
      if (await save("read_documents", {}, "Read project content into schedule and requirement records")) render();
    }));
    readingSummary();
    const groupedRequirements = mechanicalScopeSummary();
    knowledgeSummary();
    appendModelBaseline(content);
    const current = sourceName();
    content.append(node("p", current, "workflow-current-source"));
    if (selection?.revision && view.inventory.some((v) => v.revision_id === selection.revision && v.index === selection.index)) {
      const existing = view.pages[selection.revision + ":" + selection.index] || {};
      const f = form();
      field(f, "Page role", "role", existing.role || "plan", roles);
      field(f, "Drawing / sheet label", "label", existing.label || "");
      field(f, "Building / area", "building", existing.building || "");
      field(f, "Floor / level", "level", existing.level || "");
      field(f, "Notes or exclusion reason", "note", existing.note || "");
      f.append(button("Save page role", async () => {
        const data = values(f);
        if (await save("page", { ...data, source: source() }, data.note || "Classified drawing page")) render();
      }));
    }
    const pageList = node("ul", "", "workflow-record-list");
    for (const page of view.inventory) {
      const row = node("li");
      row.append(button((page.assignment?.label || "Page " + (page.index + 1)) + " · " +
        (page.assignment ? label(page.assignment.role) : "Unassigned"), () => showSource(page), true));
      row.append(node("small", page.document_name));
      pageList.append(row);
    }
    content.append(pageList);
    const reading = view.document_reading;
    const remaining = list(reading?.requirements).filter((requirement) => !groupedRequirements.has(requirement.id));
    if (remaining.length) {
      content.append(node("h3", "Requirements read from the job"));
      for (const requirement of remaining) appendWrittenRequirement(requirement, content);
    }
  }
  function mechanicalScopeSummary() {
    const scope = view.mechanical_scope, rendered = new Set();
    if (!scope || typeof scope !== "object") return rendered;
    const panel = node("section", "", "workflow-scope-summary");
    panel.setAttribute("aria-label", "Mechanical specification scope");
    panel.append(node("h3", "Mechanical specification scope"), node("p",
      "This list reflects extracted project requirements; it does not establish full Division 23 coverage or a completed takeoff.", "section-hint"));
    if (scope.state === "stale") panel.append(node("p",
      "The document set has changed. Read project documents again before reviewing these requirements.", "error"));
    else if (scope.state === "reading") panel.append(node("p",
      "Document reading is in progress. Section scope will update when it finishes."));
    else if (["failed", "interrupted"].includes(scope.state)) panel.append(node("p",
      "Document reading " + scope.state + ". Read project documents again to refresh section scope.", "error"));
    else if (!scope.available) panel.append(node("p", "Read project documents to identify written section requirements."));
    if (scope.available && !scope.section_index_available) panel.append(node("p",
      "Read project documents again to identify section headings. Earlier requirements remain available below.", "section-hint"));
    const originals = new Map(list(view.document_reading?.requirements).map((requirement) => [requirement.id, requirement]));
    function appendRows(rows, target) {
      for (const row of list(rows)) {
        if (rendered.has(row.id)) continue;
        appendWrittenRequirement({ ...originals.get(row.id), ...row }, target, row);
        rendered.add(row.id);
      }
    }
    for (const section of list(scope.sections)) {
      const detail = node("details", "", "workflow-record");
      const counts = section.counts || {};
      detail.append(node("summary", "Section " + section.section + " · " + Number(counts.requirements || 0) + " requirements"));
      const status = ({
        requirements_reviewed: "Extracted requirements reviewed",
        needs_review: "Requirements need review",
        no_requirements_identified: "Section scope unverified · no requirements identified",
        stale: "Earlier reading · refresh required",
      })[section.review_state] || "Section scope unverified";
      detail.append(node("p", status), node("p", Number(counts.applicable || 0) + " applies · " +
        Number(counts.excluded || 0) + " excluded · " + Number(counts.pending || 0) + " pending · " + Number(counts.stale || 0) + " stale"));
      for (const heading of list(section.headings)) {
        const line = node("p", heading.text);
        if (sourceRef(heading.source)) line.append(button("Show section heading", () => showSource(heading.source), true));
        detail.append(line);
      }
      appendRows(section.requirements, detail);
      panel.append(detail);
    }
    if (scope.section_index_available && !list(scope.sections).length) panel.append(node("p",
      "No Division 23 section headings were identified in this reading. Section scope remains unverified."));
    if (list(scope.unassigned_requirements).length) {
      panel.append(node("h4", "Requirements without an identified section"));
      appendRows(scope.unassigned_requirements, panel);
    }
    if (scope.other_division_requirement_count) panel.append(node("p", scope.other_division_requirement_count +
      " requirement(s) from other divisions remain in document review."));
    if (list(scope.unread_pages).length) {
      panel.append(node("h4", "Pages needing further reading"));
      for (const page of scope.unread_pages) {
        const status = ({ needs_ocr: "Needs image reading", failed: "Reading failed", unassigned: "Page role unassigned" })[page.state] || label(page.state);
        const line = node("p", status);
        if (sourceRef(page.source)) line.append(button("Show unread page", () => showSource(page.source), true));
        panel.append(line);
      }
    }
    for (const issue of list(scope.issues)) {
      const line = node("p", issue.message, "error");
      if (sourceRef(issue.source)) line.append(button("Show scope issue source", () => showSource(issue.source), true));
      panel.append(line);
    }
    content.append(panel);
    return rendered;
  }
  function appendWrittenRequirement(requirement, target, projection = null) {
    const detail = node("details", "", "workflow-record");
    detail.append(node("summary", label(list(requirement.categories).join(", ")) + " · " + requirement.text.slice(0, 110)));
    detail.append(node("p", requirement.text));
    if (requirement.section) detail.append(node("small", "Section " + requirement.section));
    if (list(requirement.qualifiers).length) detail.append(node("p", requirement.qualifiers.join(" · ")));
    if (requirement.knowledge) {
      detail.append(node("p", knowledgeDescription(requirement.knowledge)));
      detail.append(node("p", candidateApplicabilityDescription(requirement)));
      for (const sourceEntry of sourceIssuesFor(requirement.knowledge)) {
        detail.append(node("p", "Source issue: " + (sourceEntry.metadata?.title || "Linked source") +
          " is " + label(sourceEntry.state) + ".", "error"));
      }
      const linkedRule = list(view.project_knowledge?.rules).find((rule) => rule?.id === requirement.knowledge.rule_id);
      if (list(linkedRule?.issues).length && !sourceIssuesFor(requirement.knowledge).length) {
        detail.append(node("p", "Source issue: a cited source requires resolution before this candidate can be used.", "error"));
      }
    }
    appendApplicability(requirement, detail);
    detail.append(button("Show written source", () => showSource(requirement.source), true));
    const canReview = view.document_reading?.state === "completed" && !view.document_reading.stale &&
      (!projection || view.mechanical_scope.state === "current");
    const saved = view.requirement_reviews?.[requirement.id];
    let dispositionValue = saved?.disposition || "pending";
    if (projection) {
      const reviewState = projection.review_state === "current" && !canReview ? "stale" : projection.review_state;
      dispositionValue = reviewState === "current" ? projection.disposition : "pending";
      const dispositionName = (value) => ({ applicable: "Applies", excluded: "Does not apply" })[value] || "Pending";
      const reason = projection.reason ? ": " + projection.reason : "";
      if (reviewState === "current") detail.append(node("p", "Current review · " + dispositionName(projection.disposition) + reason));
      else if (reviewState === "stale") detail.append(node("p", "Stale review · needs review. Previously " +
        dispositionName(projection.stored_disposition || projection.disposition) + reason, "error"));
      else detail.append(node("p", "Awaiting applicability review"));
    } else detail.append(node("p", saved ? label(saved.disposition) + ": " + saved.reason : "Awaiting applicability review"));
    const f = node("div", "", "workflow-form");
    const disposition = field(f, "Applies to this job", "disposition", dispositionValue,
      [["pending", "Pending"], ["applicable", "Applies"], ["excluded", "Does not apply"]]);
    const reason = field(f, "Review reason", "reason", "");
    const saveReview = button("Save requirement review", async () => {
      if (!canReview) throw new Error("Read the current project documents before reviewing this requirement.");
      if (!reason.value.trim()) throw new Error("Enter a reason for this requirement review.");
      if (await save("requirement_review", { requirement_id: requirement.id, disposition: disposition.value }, reason.value)) render();
    }, true);
    disposition.disabled = reason.disabled = saveReview.disabled = busy || !canReview;
    f.append(saveReview);
    detail.append(f);
    target.append(detail);
  }
  function readingSummary() {
    const reading = view.document_reading;
    if (!reading) return;
    content.append(node("p", "Document reading: " + label(reading.state) + " · " + reading.progress + "/" +
      reading.total_pages + " pages · " + reading.schedule_rows.length + " schedule rows · " +
      reading.requirements.length + " written requirements"));
    if (reading.stale) content.append(node("p", "The document set or its roles changed. Read it again to update these records.", "error"));
    if (reading.error) content.append(node("p", reading.error, "error"));
    for (const issue of reading.issues) {
      const item = node("p", issue.message);
      if (issue.source) item.append(button("Show page", () => showSource(issue.source), true));
      content.append(item);
    }
  }
  function knowledgeSummary() {
    const reading = view.document_reading;
    const knowledge = view.project_knowledge;
    if (knowledge && typeof knowledge === "object") {
      const summary = knowledge.summary || {};
      content.append(node("h3", "Mechanical requirements"));
      content.append(node("p", Number(summary.sources || 0) + " source-linked pages · " +
        Number(summary.candidate_rules || 0) + " candidate requirements · " +
        Number(summary.rules_needing_source_resolution || 0) + " with source issues · " +
        Number(summary.unclassified_requirements || 0) + " unclassified requirements"));
      const applicability = view.rule_applicability;
      if (applicability && typeof applicability === "object") {
        const counts = applicability.summary || {};
        const unresolved = Number(counts.needs_context || 0) + Number(counts.source_blocked || 0) +
          Number(counts.unsupported || 0) + Number(counts.conflicts || 0) + Number(counts.unassigned_rules || 0);
        content.append(node("p", Number(counts.matched || 0) + " matched to items · " + unresolved + " unresolved"));
        content.append(node("p", Number(counts.confirmed_applies || 0) + " recorded as applies · " +
          Number(counts.confirmed_excluded || 0) + " recorded as does not apply · " +
          Number(counts.stale_decisions || 0) + " stale decisions · " +
          Number(counts.deferred_decisions || 0) + " kept unresolved"));
        for (const conflict of list(applicability.conflicts)) {
          const itemLabel = typeof conflict?.object_label === "string" && conflict.object_label.trim() ?
            conflict.object_label.trim() : "Unlabeled mechanical item";
          const message = typeof conflict?.message === "string" && conflict.message.trim() ?
            conflict.message.trim() : "Conflicting requirements apply to this item.";
          const line = node("p", itemLabel + " · " + message, "error");
          content.append(line);
          for (const evidence of list(conflict?.sources).map(sourceRef).filter(Boolean)) {
            content.append(button("Show conflicting source", () => showSource(evidence), true));
          }
        }
        appendOrphanedDecisionHistory(content);
      }
      content.append(node("p", "These source-linked candidate requirements will feed takeoff after applicability and source conditions are resolved. They do not provide numeric quantities.", "section-hint"));
      for (const sourceEntry of list(knowledge.sources).filter((entry) => entry?.state && entry.state !== "current")) {
        const metadata = sourceEntry.metadata || {};
        const identity = [metadata.title, metadata.locator, metadata.revision_id].filter(Boolean).join(" · ") || "Linked source";
        content.append(node("p", "Source issue: " + identity + " · " + label(sourceEntry.state), "error"));
      }
      appendRuleAdmission(content);
      return;
    }
    if (reading?.state === "completed" && reading.stale !== true && knowledge == null) {
      content.append(button("Build mechanical knowledge", async () => {
        if (await save("build_knowledge", {}, "Build mechanical knowledge from saved document reading")) render();
      }, true));
    }
  }
  function correspondenceProblem(issue) {
    if (issue && typeof issue === "object") return issue.message || "Source evidence needs resolution.";
    return typeof issue === "string" ? label(issue) : "Source evidence needs resolution.";
  }
  function correspondenceNodeName(entry) {
    const source = entry.source || {};
    const page = list(view.inventory).find((item) => item.revision_id === source.revision_id && item.index === source.index);
    return (entry.kind === "schedule" ? "Schedule row" : "Plan reference") + " · " +
      (entry.original_tag || entry.tag || "Unknown tag") + " · " + (page?.assignment?.label || page?.document_name || "Drawing") +
      " · page " + (Number.isInteger(source.index) ? source.index + 1 : "Unknown");
  }
  function appendCorrespondenceNode(entry, target) {
    const detail = node("details", "", "workflow-record");
    detail.append(node("summary", correspondenceNodeName(entry) + (entry.validity === "blocked" ? " · Source blocked" : "")));
    if (entry.text) detail.append(node("p", entry.text));
    if (entry.kind === "schedule") {
      if (entry.original?.equipment_type) detail.append(node("p", entry.original.equipment_type));
      detail.append(node("p", "Schedule-declared quantity: " + (entry.original?.quantity ?? "Unknown")));
      for (const field of list(entry.fields)) {
        const title = field.header || label(field.name || "field");
        const values = node("dl");
        values.append(node("dt", title), node("dd", field.value ?? "Unknown"));
        detail.append(values);
        if (field.source) detail.append(button("Show " + title + " source", () => showSource(field.source), true));
      }
    }
    if (entry.source) detail.append(button(entry.kind === "schedule" ? "Show schedule row" : "Show plan reference",
      () => showSource(entry.source), true));
    for (const issue of list(entry.issues)) detail.append(node("p", correspondenceProblem(issue), "error"));
    target.append(detail);
  }
  function appendCorrespondenceHistory(events, states, target, nodesById, groupFingerprint) {
    if (!events.length) return;
    const history = node("details", "", "workflow-record");
    history.append(node("summary", "Correspondence decision history · " + events.length));
    const stateById = new Map(list(states).map((entry) => [entry.event_id, entry.state]));
    for (const event of events.slice().reverse()) {
      const action = ({ resolve: "Correspondence resolved", exclude_group: "Group excluded", withdraw: "Decision withdrawn" })[event.action] || "Recorded decision";
      history.append(node("p", action + " · " + label(stateById.get(event.id) || "historical") +
        " · " + event.actor + " · " + event.reason));
      if (event.at) history.append(node("small", event.at));
      for (const omitted of list(event.exclusions)) {
        const entry = event.group_fingerprint === groupFingerprint ? nodesById.get(omitted.node_id) : null;
        history.append(node("p", "Omitted " + (entry ? correspondenceNodeName(entry) : "earlier source " + omitted.node_id) +
          ": " + label(omitted.disposition) + " · " + omitted.reason));
        if (entry?.source) history.append(button("Show omitted source", () => showSource(entry.source), true));
      }
    }
    target.append(history);
  }
  function appendCorrespondenceGroup(group, result, target, decide = null) {
    const nodesById = new Map(list(result.nodes).map((entry) => [entry.id, entry]));
    const entries = list(group.node_ids).map((id) => nodesById.get(id)).filter(Boolean);
    const schedules = entries.filter((entry) => entry.kind === "schedule");
    const plans = entries.filter((entry) => entry.kind === "plan");
    const status = ({ matched: "Matched automatically", resolved: "Correspondence resolved", excluded: "Group excluded",
      blocked: "Needs resolution" })[group.status] || "Needs resolution";
    const detail = node("details", "", "workflow-record");
    detail.append(node("summary", group.tag + " · " + status + (group.validity === "stale" ? " · Earlier evidence" : "")));
    detail.append(node("p", schedules.length + " schedule row(s) · " + plans.length + " plan reference(s) · Physical quantity: unknown"));
    const issues = [...new Set([...list(group.issues), ...(group.block_reason ? [group.block_reason] : [])])];
    for (const issue of issues) detail.append(node("p", correspondenceProblem(issue), group.status === "blocked" ? "error" : "section-hint"));
    if (group.selected_schedule_id || list(group.selected_plan_ids).length) {
      const chosen = [group.selected_schedule_id, ...list(group.selected_plan_ids)].map((id) => nodesById.get(id)).filter(Boolean);
      detail.append(node("p", "Selected correspondence: " + chosen.map(correspondenceNodeName).join("; ")));
    }
    for (const entry of entries) appendCorrespondenceNode(entry, detail);
    const events = list(result.decisions).filter((event) => event.group_id === group.id);
    appendCorrespondenceHistory(events, result.decision_states, detail, nodesById, group.fingerprint);
    if (decide && group.validity === "current") {
      const key = group.id + ":" + group.fingerprint;
      const draft = reconciliationDrafts[key] ||= { action: "", schedule_id: "", plan_ids: [], exclusions: {}, reason: "", withdrawal: "" };
      const controls = node("div", "", "workflow-form");
      const action = field(controls, "Exception disposition", "correspondence-action-" + group.id, draft.action,
        [["", "Choose a disposition"], ["resolve", "Match selected schedule and plan references"], ["exclude_group", "Exclude this whole group"]]);
      const choices = node("div", "", "workflow-form");
      const schedule = field(choices, "Corresponding schedule row", "correspondence-schedule-" + group.id, draft.schedule_id,
        [["", "Choose one schedule row"], ...schedules.filter((entry) => entry.validity === "current").map((entry) => [entry.id, correspondenceNodeName(entry)])]);
      const planChecks = plans.map((entry) => {
        const wrapper = node("label", "", "workflow-check");
        const checkbox = document.createElement("input"); checkbox.type = "checkbox";
        checkbox.checked = entry.validity === "current" && draft.plan_ids.includes(entry.id);
        checkbox.disabled = entry.validity !== "current";
        wrapper.append(checkbox, document.createTextNode(correspondenceNodeName(entry)));
        choices.append(wrapper);
        return { entry, checkbox };
      });
      choices.append(node("p", "Select one or more plan references that correspond to this schedule entry. References are not counted as physical units.", "section-hint"));
      const omissions = entries.map((entry) => {
        const omission = node("div", "", "workflow-form");
        const saved = draft.exclusions[entry.id] ||= { disposition: "", reason: "" };
        const disposition = field(omission, "Omitted " + correspondenceNodeName(entry), "correspondence-omit-" + entry.id, saved.disposition,
          [["", "Choose why this source is omitted"], ["excluded", "Excluded from this correspondence"],
            ["superseded", "Superseded evidence"], ["reading_error", "Reading error"]]);
        const reason = field(omission, "Reason for this omitted source", "correspondence-omit-reason-" + entry.id, saved.reason);
        disposition.addEventListener("change", () => { saved.disposition = disposition.value; });
        reason.addEventListener("input", () => { saved.reason = reason.value; });
        choices.append(omission);
        return { entry, omission, disposition, reason };
      });
      const selectedIds = () => new Set([schedule.value, ...planChecks.filter(({ checkbox }) => checkbox.checked).map(({ entry }) => entry.id)]);
      const updateSelection = () => {
        draft.action = action.value; draft.schedule_id = schedule.value;
        draft.plan_ids = planChecks.filter(({ checkbox }) => checkbox.checked).map(({ entry }) => entry.id);
        choices.hidden = action.value !== "resolve";
        const selected = selectedIds();
        for (const item of omissions) item.omission.hidden = selected.has(item.entry.id);
      };
      action.addEventListener("change", updateSelection);
      schedule.addEventListener("change", updateSelection);
      for (const { checkbox } of planChecks) checkbox.addEventListener("change", updateSelection);
      updateSelection();
      controls.append(choices);
      const reason = field(controls, "Correspondence resolution reason", "correspondence-reason-" + group.id, draft.reason);
      reason.addEventListener("input", () => { draft.reason = reason.value; });
      controls.append(button("Save exception resolution", async () => {
        if (!action.value) throw new Error("Choose how to resolve this exception.");
        if (!reason.value.trim()) throw new Error("Enter a reason for this correspondence decision.");
        const values = { action: action.value, group_fingerprint: group.fingerprint, schedule_id: null,
          plan_ids: [], exclusions: [], supersedes: group.decision_id || null };
        if (action.value === "resolve") {
          if (!schedules.some((entry) => entry.id === schedule.value && entry.validity === "current"))
            throw new Error("Choose one current schedule row.");
          values.schedule_id = schedule.value;
          values.plan_ids = planChecks.filter(({ entry, checkbox }) => checkbox.checked && entry.validity === "current").map(({ entry }) => entry.id);
          if (!values.plan_ids.length) throw new Error("Choose at least one corresponding current plan reference.");
          const included = new Set([values.schedule_id, ...values.plan_ids]);
          for (const item of omissions.filter((item) => !included.has(item.entry.id))) {
            if (!item.disposition.value || !item.reason.value.trim())
              throw new Error("Choose a disposition and reason for every omitted schedule row or plan reference.");
            values.exclusions.push({ node_id: item.entry.id, disposition: item.disposition.value, reason: item.reason.value.trim() });
          }
        }
        if (await decide(group, values, reason.value.trim())) render();
      }));
      if (group.status === "matched") {
        const correction = node("details", "", "workflow-record");
        correction.append(node("summary", "Change this correspondence"), controls);
        detail.append(correction);
      } else detail.append(controls);
    }
    if (decide && group.validity === "current" && group.decision_id && group.decision_state === "current") {
      const key = group.id + ":" + group.fingerprint;
      const draft = reconciliationDrafts[key] ||= { action: "", schedule_id: "", plan_ids: [], exclusions: {}, reason: "", withdrawal: "" };
      const reason = field(detail, "Decision withdrawal reason", "correspondence-withdraw-" + group.id, draft.withdrawal);
      reason.addEventListener("input", () => { draft.withdrawal = reason.value; });
      detail.append(button("Withdraw correspondence decision", async () => {
        if (!reason.value.trim()) throw new Error("Enter a reason for withdrawing this decision.");
        if (await decide(group, { action: "withdraw", group_fingerprint: group.fingerprint, schedule_id: null,
          plan_ids: [], exclusions: [], supersedes: group.decision_id }, reason.value.trim())) render();
      }, true));
    }
    target.append(detail);
  }
  function appendScheduleReconciliation() {
    const result = view.schedule_reconciliation;
    if (!result || typeof result !== "object") return false;
    const section = node("section");
    section.append(node("h3", "Schedule and plan matches"), node("p",
      "Unique exact schedule and plan pairs match automatically. Resolve the exceptions below; correspondence does not establish a physical quantity.", "section-hint"));
    const readingActive = ["queued", "running"].includes(view.document_reading?.state);
    if (!result.can_build || result.reading_stale) {
      const read = button(result.reading_stale ? "Refresh project document reading" : "Read project documents", async () => {
        if (await save("read_documents", {}, "Read project sources for schedule and plan correspondence")) render();
      }, true);
      read.disabled = busy || readingActive;
      section.append(read);
    }
    if (result.reading_stale) section.append(node("p", "The project reading has changed source selections. Refresh it to include the current documents; earlier evidence stays visible.", "error"));
    else if (result.needs_refresh) section.append(node("p", "Source evidence changed. Refresh schedule and plan matches to incorporate the changed evidence.", "section-hint"));
    const build = button(result.available ? "Refresh schedule and plan matches" : "Match schedules and plans", async () => {
      if (!result.can_build) throw new Error("Complete the project document reading before matching schedules and plans.");
      if (await save("reconcile", {}, "Matched saved schedule rows and plan references with their source evidence")) render();
    });
    build.disabled = busy || !result.can_build || readingActive;
    section.append(build);
    if (result.available) {
      const summary = result.summary || {};
      section.append(node("p", "Tag groups: " + modelMetric(summary.matched) + " matched automatically · " +
        modelMetric(summary.resolved) + " resolved · " + modelMetric(summary.excluded) + " excluded · " +
        modelMetric(summary.blocked) + " need resolution · " + modelMetric(summary.stale) + " stale"));
    } else section.append(node("p", "No saved schedule and plan correspondence yet."));
    for (const issue of list(result.issues)) {
      const resolved = issue?.state === "resolved";
      section.append(node("p", (resolved ? "Resolved source warning: " : "") + correspondenceProblem(issue),
        resolved ? "section-hint" : "error"));
      if (issue?.source) section.append(button(resolved ? "Show resolved warning source" : "Show source with reading gap",
        () => showSource(issue.source), true));
    }
    const decide = result.can_build && !readingActive ? (group, values, reason) =>
      save("reconciliation_decision", { group_id: group.id, ...values }, reason) : null;
    for (const group of list(result.groups)) appendCorrespondenceGroup(group, result, section, decide);
    const earlier = list(result.generations).filter((generation) => generation.id !== result.generation_id);
    if (earlier.length) {
      const history = node("details", "", "workflow-record");
      history.append(node("summary", "Earlier schedule and plan correspondence · " + earlier.length));
      for (const [index, generation] of earlier.entries()) {
        const saved = node("details", "", "workflow-record");
        saved.append(node("summary", "Earlier saved correspondence " + (index + 1)),
          node("p", "Historical snapshot. Current correspondence is shown above.", "section-hint"));
        for (const issue of list(generation.issues)) saved.append(node("p", correspondenceProblem(issue), "section-hint"));
        for (const group of list(generation.groups)) appendCorrespondenceGroup({ ...group, validity: "stale" },
          { nodes: generation.nodes, decisions: list(result.decisions).filter((event) => event.group_fingerprint === group.fingerprint),
            decision_states: result.decision_states }, saved);
        history.append(saved);
      }
      section.append(history);
    }
    content.append(section);
    return result.available === true;
  }
  function equipmentForm() {
    if (!byId("equipment-reviewer").value) byId("equipment-reviewer").value = byId("workflow-actor").value;
    equipmentCountPanel.render(content);
    readingSummary();
    knowledgeSummary();
    const hasCorrespondence = appendScheduleReconciliation();
    const reading = view.document_reading;
    if (reading?.equipment_register.length) {
      const register = hasCorrespondence ? node("details", "", "workflow-record") : content;
      if (hasCorrespondence) register.append(node("summary", "Original document equipment register"));
      register.append(node("h3", "Equipment read from schedules"));
      for (const record of reading.equipment_register) {
        const detail = node("details", "", "workflow-record");
        detail.append(node("summary", record.tag + " · " + record.plan_occurrences.length + " plan references"));
        if (Array.isArray(record.rule_ids)) {
          detail.append(node("p", record.rule_ids.length + " source-linked candidate requirement" +
            (record.rule_ids.length === 1 ? "" : "s") + " associated with this equipment."));
        }
        for (const row of reading.schedule_rows.filter((v) => record.schedule_row_ids.includes(v.id))) {
          if (row.equipment_type) detail.append(node("h4", row.equipment_type));
          if (row.knowledge) detail.append(node("p", knowledgeDescription(row.knowledge)));
          const fields = node("dl");
          for (const field of row.fields) {
            fields.append(node("dt", field.header), node("dd", field.value ?? "Unknown"));
            if (field.source) fields.append(button("Show " + field.header, () => showSource(field.source), true));
          }
          detail.append(fields);
          if (row.issues.length) detail.append(node("p", row.issues.join(" · ")));
        }
        for (const occurrence of record.plan_occurrences) {
          detail.append(button("Show " + record.tag + " on page " + (occurrence.source.index + 1), () => showSource(occurrence.source), true));
        }
        for (const requirement of reading.requirements.filter((v) => record.requirement_ids.includes(v.id))) {
          detail.append(node("p", requirement.text));
          detail.append(button("Show linked requirement", () => showSource(requirement.source), true));
        }
        detail.append(node("p", "Physical quantity: unknown. Schedule quantities and repeated references require reconciliation."));
        if (record.issues.length) detail.append(node("p", record.issues.map(label).join(" · ")));
        register.append(detail);
      }
      if (hasCorrespondence) content.append(register);
    }
    if (view.equipment?.stale) hint("Equipment results use an earlier document reading. Current reviewed quantities are unknown; build current review records to continue. Earlier reviewed values remain in history.");
    hint("Draft tag review links equipment references in plans and schedules. Physical assembly quantities come from the source-region review above.");
    const f = form();
    const mode = field(f, "Equipment source", "mode", "text", [["text", "Saved document reading"], ["vision", "Configured local AI"]]);
    f.append(button("Build draft tag review", async () => {
      if (await save("extract_equipment", { mode: mode.value }, "Built equipment review records from the project sources")) {
        render();
        window.dispatchEvent(new CustomEvent("heleos:open-equipment-run", { detail: view.equipment_run }));
      }
    }));
    if (view.equipment_run) {
      content.append(button("Open draft tag results", () => {
        window.dispatchEvent(new CustomEvent("heleos:open-equipment-run", { detail: view.equipment_run }));
      }, true));
    }
    content.append(button("Refresh project equipment status", async () => {
      const run = document.getElementById("equipment-history").value;
      if (run && run !== view.equipment_run) await save("equipment", { run_id: run }, "Selected equipment extraction for this project");
      await load();
    }, true));
  }
  function ensureImage() {
    source();
    if (selection?.state.preview_mode !== "image") throw new Error("An image preview is required to select drawing points.");
  }
  function currentCalibrations() {
    return verifiedCalibrations({ revision_id: selection?.revision, index: selection?.index });
  }
  function verifiedCalibrations(source) {
    return list(view.calibrations).filter((fact) => fact.state === "verified" &&
      fact.source.revision_id === source?.revision_id && fact.source.index === source?.index);
  }
  function rememberMeasurementForm(element, key) {
    element.dataset.measurementForm = key;
    const remember = () => {
      measurementDraft[key] = { ...values(element),
        confirmed: element.querySelector('input[type="checkbox"]')?.checked === true };
    };
    element.addEventListener("input", remember);
    element.addEventListener("change", remember);
  }
  function appendScaleCandidates() {
    const readingCurrent = view.document_reading?.state === "completed" && view.document_reading.stale !== true;
    const detect = button("Find scale labels in saved reading", async () => {
      if (!readingCurrent) throw new Error("Read the current project documents before finding scale labels.");
      if (await save("detect_scales", {}, "Read explicit scale labels from the saved document reading")) render();
    }, true);
    detect.disabled = busy || !readingCurrent;
    content.append(detect);
    if (!readingCurrent) hint("A current document reading is needed to find explicit scale labels.");
    const gaps = list(view.scale_reading_issues);
    if (gaps.length) {
      const issues = node("details", "", "workflow-record");
      issues.append(node("summary", "Scale reading gaps · " + gaps.length));
      for (const gap of gaps) {
        issues.append(node("p", gap.message || "Scale evidence could not be read on this page.", "error"));
        if (gap.source) issues.append(button("Show page with scale reading gap", () => showSource(gap.source), true));
      }
      content.append(issues);
    }
    const candidates = list(view.scale_candidates).filter((candidate) =>
      candidate.source?.revision_id === selection?.revision && candidate.source?.index === selection?.index);
    const panel = node("details", "", "workflow-record");
    panel.append(node("summary", "Scale labels on this page · " + candidates.length));
    for (const candidate of candidates) {
      const detail = node("details", "", "workflow-record");
      const notation = candidate.notation === "nts" ? "Not to scale" :
        candidate.meters_per_point == null ? "Unknown scale" : "Unverified declared scale";
      detail.append(node("summary", (candidate.label || "Scale label") + " · " + notation +
        (candidate.validity === "current" ? "" : " · Blocked")), node("pre", candidate.evidence?.text || "No source text saved"));
      if (candidate.source) detail.append(button("Show scale label source", () => showSource({
        ...candidate.source, bbox: candidate.evidence?.bbox,
      }), true));
      for (const issue of list(candidate.issues)) detail.append(node("p", String(issue), "error"));
      const raw = node("details", "", "equipment-audit");
      raw.append(node("summary", "Exact candidate and reading evidence"), node("pre", JSON.stringify(candidate, null, 2)));
      detail.append(raw);
      panel.append(detail);
    }
    content.append(panel);
    const available = candidates.filter((candidate) => candidate.validity === "current");
    const f = form();
    rememberMeasurementForm(f, "declared");
    const draft = measurementDraft.declared;
    const selected = available.some((candidate) => candidate.id === draft.candidate_id) ? draft.candidate_id : "";
    const candidate = field(f, "Label to assign to the selected drawing view", "candidate_id", selected,
      [["", "Choose a current scale label"], ...available.map((entry) => [entry.id,
        entry.label + (entry.notation === "nts" ? " · NTS" : entry.meters_per_point == null ? " · Unknown" : "")])]);
    const name = field(f, "Declared view name", "label", draft.label || "");
    const reason = field(f, "Scale evidence reason", "reason", draft.reason || "");
    const uniform = node("label", "", "workflow-check");
    const checkbox = document.createElement("input"); checkbox.type = "checkbox";
    checkbox.checked = draft.confirmed === true;
    uniform.append(checkbox, document.createTextNode("I checked that the numeric scale applies uniformly within this selected view."));
    const assign = button("Save unverified declared scale", async () => {
      if (!pickedView) throw new Error("Select the drawing view that this label applies to first.");
      const selected = available.find((entry) => entry.id === candidate.value);
      if (!selected) throw new Error("Choose a current scale label for this page.");
      if (selected.meters_per_point != null && !checkbox.checked)
        throw new Error("Confirm that the numeric scale applies uniformly within the selected view.");
      if (!reason.value.trim()) throw new Error("Enter a reason for assigning this scale evidence to the view.");
      if (await save("scale_declared", { candidate_id: candidate.value, view: pickedView, label: name.value,
        uniform_scale_confirmed: checkbox.checked }, reason.value.trim())) {
        pickedView = null; measurementDraft.declared = {}; render();
      }
    });
    assign.disabled = busy || !available.length || !view.sheet_geometries?.[selection?.revision + ":" + selection?.index];
    f.append(uniform, assign);
    hint("Assign the label only to its actual view. Numeric labels still need an explicit scale decision. NTS and unknown labels record views where scale-dependent measurements are blocked.");
  }
  function appendScaleFacts() {
    const panel = node("details", "", "workflow-record");
    const facts = list(view.calibrations).filter((fact) =>
      fact.source?.revision_id === selection?.revision && fact.source?.index === selection?.index);
    panel.append(node("summary", "Saved scale facts on this page · " + facts.length));
    for (const fact of facts) {
      const state = fact.state || "unverified";
      const detail = node("details", "", "workflow-record");
      detail.append(node("summary", fact.label + " · " + label(state)),
        node("p", fact.meters_per_point == null ? "Scale: unknown / NTS" : "Scale factor: " + fact.meters_per_point + " meters per paper point"));
      detail.append(button("Show scale view", () => showSource({ ...fact.source, bbox: fact.view },
        fact.points ? { points: fact.points } : {}), true));
      for (const issue of list(fact.issues)) detail.append(node("p", String(issue), "error"));
      const draft = scaleDrafts.decisions[fact.id] ||= { state: "", reason: "" };
      const options = [["", "Choose a scale decision"]];
      if (state !== "stale" && fact.geometry_fingerprint && Number.isFinite(Number(fact.meters_per_point)) &&
          Number(fact.meters_per_point) > 0) options.push(["verified", "Verify scale for this view"]);
      options.push(["rejected", "Reject this scale fact"], ["withdrawn", "Withdraw this scale fact"],
        ["superseded", "Supersede this scale fact"]);
      const choice = field(detail, "Scale decision", "scale-state-" + fact.id,
        options.some(([value]) => value === draft.state) ? draft.state : "", options);
      const reason = field(detail, "Scale decision reason", "scale-reason-" + fact.id, draft.reason);
      choice.addEventListener("change", () => { draft.state = choice.value; });
      reason.addEventListener("input", () => { draft.reason = reason.value; });
      detail.append(button("Record scale decision", async () => {
        if (!choice.value) throw new Error("Choose an explicit scale decision.");
        if (!reason.value.trim()) throw new Error("Enter a reason for this scale decision.");
        if (await save("scale_decision", { fact_id: fact.id, state: choice.value }, reason.value.trim())) render();
      }, true));
      for (const decision of list(view.scale_decisions).filter((entry) => entry.fact_id === fact.id))
        detail.append(node("p", label(decision.state) + " · " + decision.actor + " · " + decision.reason));
      const raw = node("details", "", "equipment-audit");
      raw.append(node("summary", "Exact saved scale fact"), node("pre", JSON.stringify(fact, null, 2)));
      detail.append(raw);
      panel.append(detail);
    }
    content.append(panel);
  }
  function appendSavedMeasurements() {
    const measurementList = node("ul", "", "workflow-record-list");
    for (const entry of list(view.measurements)) {
      const row = node("li");
      const current = entry.validity === "current" && entry.meters != null;
      const superseded = entry.validity === "superseded";
      row.append(button(entry.label + " · " + (current ? entry.meters + " m · Current" :
        superseded ? "Superseded" : "UNKNOWN · Blocked"), () => showSource(entry.source, { points: entry.points }), true));
      if (!current) {
        row.append(node("p", "Stored historical length: " + (entry.stored_meters ?? entry.meters ?? "Unknown") + " m"));
        const message = entry.block_message || entry.block_reason;
        if (message) row.append(node("p", (superseded ? "" : "Blocked: ") + message, "error"));
      }
      if (superseded) row.append(node("p", "Replacement measurement: " + (entry.replacement_id || "Unknown")));
      else {
        const available = verifiedCalibrations(entry.source);
        const draft = scaleDrafts.recalculations[entry.id] ||= { calibration_id: "", reason: "" };
        const calibration = field(row, "Verified scale for recalculation", "recalculate-scale-" + entry.id,
          available.some((fact) => fact.id === draft.calibration_id) ? draft.calibration_id : "",
          [["", "Choose a verified scale for this page"], ...available.map((fact) => [fact.id, fact.label])]);
        const reason = field(row, "Recalculation reason", "recalculate-reason-" + entry.id, draft.reason);
        calibration.addEventListener("change", () => { draft.calibration_id = calibration.value; });
        reason.addEventListener("input", () => { draft.reason = reason.value; });
        const recalculate = button("Recalculate saved measurement", async () => {
          if (!available.some((fact) => fact.id === calibration.value)) throw new Error("Choose a verified scale for this measurement's page.");
          if (!reason.value.trim()) throw new Error("Enter a reason for recalculating this measurement.");
          if (await save("measure_recalculate", { measurement_id: entry.id, calibration_id: calibration.value }, reason.value.trim())) render();
        }, true);
        recalculate.disabled = busy || !available.length;
        row.append(recalculate);
      }
      const raw = node("details", "", "equipment-audit");
      raw.append(node("summary", "Saved measurement and calculation binding"), node("pre", JSON.stringify(entry, null, 2)));
      row.append(raw);
      measurementList.append(row);
    }
    content.append(measurementList);
  }
  function measurementForm() {
    hint("Read the page's scale evidence and define its drawing view. Measurements require an explicit verified scale and remain blocked if scale views conflict. This is the measurement foundation for automatic mechanical takeoff.");
    content.append(node("p", sourceName(), "workflow-current-source"));
    const geometryAvailable = Boolean(view.sheet_geometries?.[selection?.revision + ":" + selection?.index]);
    if (!geometryAvailable) hint("The selected page has no available coordinate identity. Scale-dependent calculations are unavailable.");
    const f = form();
    f.append(button("Select drawing view", () => {
      ensureImage(); pickingView = true;
      window.dispatchEvent(new CustomEvent("heleos:pick-region", { detail: true }));
      info("Drag a rectangle around the view that uses one uniform scale.");
    }, true));
    f.append(node("p", pickedView ? "Drawing view selected." : "Drawing view not selected.", "section-hint"));
    f.append(button("2. Mark known distance", () => {
      ensureImage();
      window.dispatchEvent(new CustomEvent("heleos:pick-line", { detail: "calibration" }));
      info("Drag from one end of the known dimension to the other.");
    }, true));
    f.append(node("p", pickedLine?.purpose === "calibration" ? "Known-distance endpoints selected." : "Known-distance endpoints not selected.", "section-hint"));
    rememberMeasurementForm(f, "calibration");
    field(f, "View / calibration name", "label", measurementDraft.calibration.label ?? "Main drawing view");
    field(f, "Known distance", "known_length", measurementDraft.calibration.known_length || "");
    field(f, "Known distance units", "unit", measurementDraft.calibration.unit || view.project.units, ["ft", "m", "in", "mm"]);
    const confirmed = node("label", "", "workflow-check");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = measurementDraft.calibration.confirmed === true;
    confirmed.append(checkbox, document.createTextNode("I checked that this view has uniform scale and is not marked NTS."));
    const saveCalibration = button("Save unverified calibration", async () => {
      if (!pickedView || pickedLine?.purpose !== "calibration") throw new Error("Select the drawing view and known-distance endpoints first.");
      if (await save("calibrate", { ...values(f), source: source(), view: pickedView, points: pickedLine.points,
        uniform_scale_confirmed: checkbox.checked }, "Recorded unverified drawing-view calibration from a known distance")) {
        pickedLine = null; pickedView = null; measurementDraft.calibration = {}; render();
      }
    });
    saveCalibration.disabled = busy || !geometryAvailable;
    f.append(confirmed, saveCalibration);
    appendScaleCandidates();
    appendScaleFacts();
    content.append(node("h3", "Measure a length"));
    const m = form();
    rememberMeasurementForm(m, "measurement");
    const verified = currentCalibrations();
    const calibration = field(m, "Verified scale for this page", "calibration_id",
      verified.some((fact) => fact.id === measurementDraft.measurement.calibration_id) ? measurementDraft.measurement.calibration_id : "",
      [["", "Choose a verified scale"], ...verified.map((v) => [v.id, v.label])]);
    field(m, "Length description", "label", measurementDraft.measurement.label || "");
    field(m, "Mechanical scope", "scope", measurementDraft.measurement.scope || view.project.scopes[0], view.project.scopes);
    m.append(button("Mark length endpoints", () => {
      ensureImage();
      if (!calibration.value) throw new Error("Choose a calibration for this page.");
      window.dispatchEvent(new CustomEvent("heleos:pick-line", { detail: "measurement" }));
      info("Drag along the segment to measure. Save each segment separately.");
    }, true));
    m.append(node("p", pickedLine?.purpose === "measurement" ? "Length endpoints selected." : "Length endpoints not selected.", "section-hint"));
    const measure = button("Calculate and save length", async () => {
      if (pickedLine?.purpose !== "measurement") throw new Error("Mark the length endpoints first.");
      if (!verified.some((fact) => fact.id === calibration.value)) throw new Error("Choose a verified scale for this page.");
      if (await save("measure", { ...values(m), points: pickedLine.points }, "Measured a segment within a calibrated view")) {
        pickedLine = null; measurementDraft.measurement = {}; render();
      }
    });
    measure.disabled = busy || !geometryAvailable || !verified.length;
    m.append(measure);
    hint("The server checks the whole measured segment against scale views. Resolve conflicting, NTS or unverified overlapping views before recalculating; a new decision does not silently update old quantities.");
    appendSavedMeasurements();
  }
  function takeoffForm() {
    airPanel.render(content);
    ductPanel.render(content);
    content.append(node("h3", "Additional mechanical items"));
    hint("Add other mechanical items or explicit allowances with their source and evidence. A linked measurement supplies its calculated length.");
    content.append(node("p", sourceName(), "workflow-current-source"));
    const f = form();
    const v = itemEditing || {};
    field(f, "Scope", "scope", v.scope || view.project.scopes[0], view.project.scopes);
    field(f, "Item description", "description", v.description || "");
    field(f, "System / service", "system", v.system || "");
    field(f, "Size", "size", v.size || "");
    field(f, "Material", "material", v.material || "");
    field(f, "Quantity — leave blank if unknown", "quantity", v.validity === "blocked" ? "" : v.quantity ?? "");
    field(f, "Unit", "unit", v.unit || "each", ["each", "m", "ft", "m2", "ft2", "kg", "lb", "lot"]);
    field(f, "Evidence", "evidence_class", v.evidence_class || "drawn",
      [["drawn", "Read / counted on drawing"], ["document_required", "Required by document"],
        ["derived", "Calculated from saved measurement"], ["allowance", "Explicit allowance"], ["unmeasurable", "Cannot measure yet"]]);
    const currentMeasurements = view.measurements.filter((entry) => entry.validity === "current" && entry.meters != null);
    const blockedLink = Boolean(v.measurement_id && !currentMeasurements.some((entry) => entry.id === v.measurement_id));
    field(f, "Current measurement, if used", "measurement_id", blockedLink ? "" : v.measurement_id || "",
      [["", "No linked measurement"], ...currentMeasurements.map((entry) => [entry.id, entry.label + " · " + entry.meters + " m"])]);
    if (blockedLink) f.append(node("p", "The linked measurement is blocked or superseded. Recalculate it in Measurements or select a current measurement before saving a correction.", "error"));
    field(f, "Source note / correction reason", "note", v.note || "");
    f.append(button(itemEditing ? "Save item correction" : "Save takeoff item", async () => {
      const data = values(f);
      if (blockedLink && !data.measurement_id)
        throw new Error("Recalculate the linked measurement or select a current measurement before saving this correction.");
      if (await save("item", { ...data, id: itemEditing?.id || null,
        quantity: data.quantity.trim() === "" ? null : data.quantity,
        source: itemEditing ? { revision_id: itemEditing.source.revision_id, index: itemEditing.source.index } : source(),
        measurement_id: data.measurement_id || null }, data.note)) {
        itemEditing = null; render();
      }
    }));
    if (itemEditing) f.append(button("Cancel correction", () => { itemEditing = null; render(); }, true));
    const list = node("ul", "", "workflow-record-list");
    for (const entry of view.items) {
      const row = node("li");
      row.append(node("strong", entry.description), node("span", label(entry.scope) + " · " +
        (entry.validity === "blocked" ? "UNKNOWN" : entry.quantity ?? "UNKNOWN") + " " + entry.unit),
        button("Source", () => showSource(entry.source), true),
        button("Correct", () => { itemEditing = entry; render(); }, true));
      if (entry.validity === "blocked") {
        row.append(node("p", "Quantity blocked: " +
          (entry.block_message || entry.block_reason || "Linked measurement needs resolution"), "error"));
        row.append(node("p", "Stored historical quantity: " + (entry.stored_quantity ?? "Unknown") + " " + entry.unit));
      }
      list.append(row);
    }
    content.append(list, node("h3", "Review each included scope"));
    const review = form();
    field(review, "Scope to review", "scope", view.project.scopes[0], view.project.scopes);
    field(review, "Disposition", "disposition", "reviewed", [["reviewed", "Saved records reviewed"], ["not_applicable", "This scope does not apply"]]);
    const reason = field(review, "Review note / why not applicable", "reason", "");
    review.append(button("Save scope review", async () => {
      const data = values(review);
      if (!reason.value.trim()) throw new Error("Explain the scope review or why it does not apply.");
      if (await save("scope_review", { scope: data.scope, disposition: data.disposition }, reason.value)) render();
    }));
    for (const [key, status] of Object.entries(view.scopes)) hint(label(key) + ": " + status.count + " items · " + (status.reviewed ? "Reviewed" : "Review pending"));
  }
  function exceptionForm() {
    hint("Automatic findings remain open until the underlying records are corrected. Save project questions and their resolutions here.");
    const f = form();
    field(f, "Question / missing information", "message", "");
    f.append(button("Save unresolved item", async () => {
      const data = values(f);
      if (await save("issue", { id: null, message: data.message, state: "open", resolution: "",
        source: selection?.revision ? source() : null }, "Recorded unresolved project information")) render();
    }));
    const list = node("ul", "", "workflow-record-list");
    for (const issue of view.issues_view) {
      const row = node("li");
      row.append(node("strong", issue.message), node("span", label(issue.state)));
      if (issue.source) row.append(button("Source", () => showSource(issue.source), true));
      if (issue.allowance_id && issue.state === "open") {
        const reason = field(row, "Allowance review note", "allowance-" + issue.allowance_id);
        row.append(button("Acknowledge explicit allowance", async () => {
          if (!reason.value.trim()) throw new Error("Record why this explicit allowance is included.");
          if (await save("allowance_review", { item_id: issue.allowance_id }, reason.value)) render();
        }, true));
      }
      if (!issue.automatic) {
        const resolution = field(row, "Resolution / reason to reopen", "resolution-" + issue.id, issue.resolution);
        row.append(button(issue.state === "open" ? "Save resolution" : "Reopen", async () => {
          if (!resolution.value.trim()) throw new Error("Record the resolution or reason to reopen.");
          if (await save("issue", { id: issue.id, message: issue.message, state: issue.state === "open" ? "resolved" : "open",
            resolution: resolution.value, source: issue.source ? { revision_id: issue.source.revision_id, index: issue.source.index } : null }, resolution.value)) render();
        }, true));
      }
      list.append(row);
    }
    content.append(list);
  }
  function exportForm() {
    hint("Download the Excel workbook with supported duct lengths and air-device counts, source references, saved records and change history. Unknown quantities and incomplete work stay visible. Correct in Heleos and regenerate; workbook edits do not update the project.");
    content.append(node("p", view.items.length + " mechanical items · " + view.measurements.length + " measurements · " + view.equipment_rows.length + " equipment tags", "workflow-export-summary"));
    const link = node("a", "Download draft takeoff package", "button button-primary");
    link.href = "api/workflow/export.zip"; link.download = "heleos-draft-takeoff.zip";
    content.append(link);
    hint(view.review_complete ? "All recorded stage reviews are current. The package remains a draft and is not an approved estimate." :
      "The project is incomplete. The export includes those limitations.");
  }
  function renderReview(current) {
    const target = byId("workflow-review-controls");
    target.replaceChildren();
    if (stage === "export") return;
    if (current.status === "reviewed") target.append(node("p", "This stage's saved inputs have been reviewed.", "equipment-message"));
    else if (current.can_review) {
      const note = field(target, "Stage review note", "stage-review-note");
      target.append(button("Record stage review", async () => {
        if (!note.value.trim()) throw new Error("Add a review note.");
        if (await save("review", { stage }, note.value)) render();
      }));
    } else target.append(node("p", "Required records are still incomplete. You can continue other stages; this stage stays open.", "section-hint"));
    const next = view.stages[view.stages.findIndex((v) => v.id === stage) + 1];
    if (next) target.append(button("Continue to " + next.title.toLowerCase(), () => navigate(next.id), true));
  }

  window.addEventListener("heleos:selection", (event) => {
    const previous = selection;
    selection = event.detail;
    if (!view) return;
    if (view.project.name) byId("project-name").textContent = view.project.name;
    const changed = previous?.revision !== selection.revision || previous?.index !== selection.index;
    if (changed) { equipmentCountPanel.cancelRegion(); pickedLine = null; pickedView = null; measurementDraft = { calibration: {}, measurement: {}, declared: {} }; }
    for (const entry of document.querySelectorAll(".workflow-current-source")) entry.textContent = sourceName();
    if (changed && ["documents", "measurements", "equipment"].includes(stage) && !busy) render();
    if (selection.state.documents.reduce((n, v) => n + v.page_count, 0) !== view.inventory.length && !busy) {
      load().catch((e) => error(e.message));
    }
  });
  window.addEventListener("heleos:region-picked", async (event) => {
    if (stage === "equipment") {
      try { await equipmentCountPanel.regionPicked(event.detail); } catch (e) { error(e.message); }
      return;
    }
    if (stage === "takeoff") {
      try { ductPanel.pickRegion(event.detail); } catch (e) { error(e.message); }
      return;
    }
    if (!pickingView || stage !== "measurements") return;
    rememberMeasurementForms();
    pickedView = event.detail.bbox;
    pickingView = false;
    window.dispatchEvent(new CustomEvent("heleos:pick-region", { detail: false }));
    render();
    info("Drawing view selected. Mark the known distance next.");
  });
  window.addEventListener("heleos:line-picked", (event) => {
    if (event.detail?.purpose === "duct-correction") {
      if (stage === "takeoff") {
        try { ductPanel.pickLine(event.detail); } catch (e) { error(e.message); }
      }
      return;
    }
    if (stage !== "measurements") return;
    rememberMeasurementForms();
    pickedLine = event.detail;
    render();
    info("Endpoints selected. Enter the remaining fields and save.");
  });
  window.addEventListener("heleos:path-draft", (event) => {
    if (stage !== "takeoff") return;
    try { ductPanel.pathDraft(event.detail); } catch (e) { error(e.message); }
  });
  window.addEventListener("heleos:equipment-updated", () => {
    if (view && stage === "equipment" && !busy) load().catch((e) => error(e.message));
  });
  function rememberMeasurementForms() {
    for (const f of content.querySelectorAll("form[data-measurement-form]")) {
      measurementDraft[f.dataset.measurementForm] = {
        ...values(f), confirmed: f.querySelector('input[type="checkbox"]')?.checked === true,
      };
    }
  }
  window.dispatchEvent(new CustomEvent("heleos:request-selection"));
  load(true).catch((e) => error(e.message));
})();
