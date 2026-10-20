"use strict";
// Source-bound review drafts only; exact geometry and quantities belong to the server.
((root, factory) => {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.HeleosRevisionChecksEditor = api;
})(typeof globalThis === "object" ? globalThis : this, () => {
  const copy = value => JSON.parse(JSON.stringify(value));
  const list = value => Array.isArray(value) ? value : [];
  const key = source => JSON.stringify([source?.revision_id, source?.index, source?.sheet_id, source?.geometry_fingerprint]);
  const same = (a, b) => key(a) === key(b);
  const record = entry => entry.record || entry;
  function evidence(records, captures = []) {
    // Producer evidence has no semantic kind. Keep capture metadata separate from
    // frozen source bytes, and never let a later untyped copy erase that metadata.
    const byId = new Map();
    for (const item of records) {
      const prior = byId.get(item.id);
      byId.set(item.id, prior?.kind && !Object.hasOwn(item, "kind") ? { ...item, kind: prior.kind } : item);
    }
    for (const capture of captures) {
      const item = byId.get(capture.evidence?.id);
      if (item && Object.hasOwn(capture, "kind")) byId.set(item.id, { ...item, kind: capture.kind });
    }
    return [...byId.values()];
  }
  function create(entry) {
    const saved = record(entry), origin = saved.origin;
    const reading = (saved.observation || saved.retained_observation || origin.observation).readings.find(value => value.id === origin.reading_id);
    return { id: entry.id, fingerprint: entry.fingerprint, state: "", target_ids: [], scope: null,
      reading: copy(reading), evidence_ids: [], relationships: [], reason: "" };
  }
  function affected(stage, group) {
    return list(stage.arc_check_index).filter(entry => list(entry.current_ids).some(id => group.current_ids.includes(id)));
  }
  function priorSources(entry, targets) {
    const saved = record(entry);
    const sources = [entry.current_source || saved.current_source,
      ...targets.filter(target => list(entry.current_ids).includes(target.id)).map(target => target.observation.source)].filter(Boolean);
    return [...new Map(sources.map(source => [key(source), source])).values()];
  }
  function validate(entry, draft, targets, evidence, prior, later = false, completeIds = targets.map(target => target.id)) {
    if (draft.id !== entry.id || draft.fingerprint !== entry.fingerprint) throw new Error("The saved check pin changed. Reopen its review.");
    if (!["active", "unresolved", "not_applicable"].includes(draft.state) || !draft.reason.trim()) throw new Error("Explicitly decide each retained check and explain its source review.");
    const ids = draft.target_ids || (later ? list(draft.targets).map(target => target.id) : draft.incoming_ids);
    if (!ids?.length || new Set(ids).size !== ids.length || ids.some(id => !targets.some(target => target.id === id))) throw new Error("Explicitly select the new dependent targets for each retained check.");
    const selected = targets.filter(target => ids.includes(target.id));
    if (later && draft.targets && draft.targets.some(pin => !targets.some(target => target.id === pin.id && target.fingerprint === pin.fingerprint))) throw new Error("A retained check target changed. Reopen its review.");
    if (draft.state === "not_applicable" && (ids.length !== completeIds.length || completeIds.some(id => !ids.includes(id)))) throw new Error("Retirement must explicitly include the complete dependent scope.");
    const sources = [...prior, ...selected.map(target => target.observation.source)].filter(Boolean);
    const chosen = evidence.filter(item => draft.evidence_ids.includes(item.id));
    if (!sources.length || sources.some(source => !chosen.some(item => same(item.source, source))) || draft.evidence_ids.some(id => !evidence.some(item => item.id === id && sources.some(source => same(source, item.source))))) throw new Error("Choose check decision evidence on every old current and selected new source page.");
    const result = { state: draft.state, scope: null, reading: null, evidence_ids: copy(draft.evidence_ids), relationships: [], reason: draft.reason.trim() };
    if (later) result.targets = selected.map(target => ({ id: target.id, fingerprint: target.fingerprint }));
    else Object.assign(result, { id: entry.id, fingerprint: entry.fingerprint, incoming_ids: copy(ids) });
    if (draft.state !== "active") {
      if (!draft.target_ids && (draft.scope !== null || draft.reading !== null || list(draft.relationships).length)) throw new Error("Inactive checks cannot carry an active scope, reading or relationship.");
      return result;
    }
    const target = selected[0], scope = draft.scope, saved = record(entry), reading = draft.reading;
    if (selected.length !== 1 || target.observation.geometry.kind !== "circular_arc" || !["root", "interval"].includes(scope?.kind)) throw new Error("Choose one circular root and an explicit root or interval scope for the active check.");
    const scopeId = later ? "observation_id" : "incoming_id";
    if (scope[scopeId] !== target.id) throw new Error("The active check scope must match its selected target.");
    if (!reading || reading.id !== saved.origin.reading_id || reading.kind !== "length" || !String(reading.value).trim() || !reading.unit || !reading.evidence_ids?.length) throw new Error("Keep the original check reading identity and review its value, unit and source evidence.");
    const points = scope.kind === "root" ? target.observation.geometry.points : [scope.start_point, scope.end_point];
    if (points.some(point => !Array.isArray(point) || point.length !== 2 || point.some(value => !Number.isFinite(Number(value)) || Number(value) < 0 || Number(value) > 1)) || (scope.kind === "interval" && JSON.stringify(points[0]) === JSON.stringify(points[1]))) throw new Error("Pick two distinct interval endpoints on the selected source drawing.");
    const graphics = chosen.filter(item => item.text === null && (!Object.hasOwn(item, "kind") || item.kind === "graphic") && same(item.source, target.observation.source));
    if (points.some(point => !graphics.some(item => item.bbox && Number(point[0]) >= item.bbox[0] && Number(point[0]) <= item.bbox[2] && Number(point[1]) >= item.bbox[1] && Number(point[1]) <= item.bbox[3]))) throw new Error("Select new-page graphic evidence supporting the circular root or both interval endpoints.");
    const links = list(draft.relationships);
    for (const id of reading.evidence_ids) {
      const item = evidence.find(value => value.id === id);
      if (!item || typeof item.text !== "string" || !item.text.trim() || (Object.hasOwn(item, "kind") && !["dimension", "elevation"].includes(item.kind))) throw new Error("Choose available written source evidence for the reviewed reading; captured evidence must have a dimension or elevation role.");
      if (!same(item.source, target.observation.source) && !links.some(link => link.reading_id === reading.id && same(link.reading_source, item.source) &&
        list(link.observation_reference_evidence_ids).length && list(link.reading_reference_evidence_ids).length &&
        link.observation_reference_evidence_ids.every(marker => evidence.some(value => value.id === marker && same(value.source, target.observation.source))) &&
        link.reading_reference_evidence_ids.every(marker => evidence.some(value => value.id === marker && same(value.source, item.source))))) throw new Error("Review foreign readings using reference evidence on both pages.");
    }
    result.scope = copy(scope); result.reading = copy(reading); delete result.reading.supplemental; result.relationships = copy(links);
    return result;
  }
  function group(stage, proposal, evidence = stage.evidence) {
    const result = copy(proposal); delete result.checkDrafts;
    const checks = affected(stage, proposal), drafts = proposal.checkDrafts || proposal.check_dispositions || [];
    if (proposal.disposition !== "replace_current") {
      if (list(proposal.check_dispositions).length) throw new Error("Only replacement groups can change retained checks.");
      delete result.check_dispositions; return result;
    }
    if (checks.length !== drafts.length || new Set(drafts.map(draft => draft.id)).size !== drafts.length) throw new Error("Explicitly decide every affected retained check once.");
    const targets = stage.incoming.filter(target => proposal.incoming_ids.includes(target.id));
    const dispositions = checks.map(entry => {
      if (list(entry.required_current_ids).some(id => !proposal.current_ids.includes(id))) throw new Error("Select the complete dependent scope for the retained check.");
      const draft = drafts.find(value => value.id === entry.id);
      if (!draft) throw new Error("Explicitly decide every affected retained check once.");
      return validate(entry, draft, targets, evidence, priorSources(entry, stage.current_targets));
    });
    if (dispositions.length || Object.hasOwn(proposal, "check_dispositions")) result.check_dispositions = dispositions;
    return result;
  }
  return { create, affected, priorSources, validate, group, evidence };
});
