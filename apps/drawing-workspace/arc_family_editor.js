"use strict";
// Draft state only. The server proves circular geometry and owns all quantities.
((root, factory) => {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.HeleosArcFamilyEditor = api;
})(typeof globalThis === "object" ? globalThis : this, () => {
  const copy = value => JSON.parse(JSON.stringify(value));
  function create(family, snapshot) {
    const observation = copy(family.root);
    observation.schema = "duct-observation-2";
    observation.readings = [];
    observation.geometry.dimension_check_ids = [];
    observation.geometry.radius_check_ids = [];
    return { family: copy(family), snapshot: copy(snapshot), observation,
      cuts: copy(family.cuts), decisions: family.checks.map(check => ({
        id: check.id, fingerprint: check.fingerprint, state: "", scope: null,
        reading: copy((check.observation || check.retained_observation || check.origin.observation).readings.find(reading => reading.id === check.origin.reading_id)),
        evidence_ids: copy(check.evidence_ids || []), reason: "", relationships: [], interval: { start_point: [], end_point: [] }
      })), evidence_ids: [], reason: "", acknowledgment: null, undo: [] };
  }
  function invalidate(draft, geometry = false) {
    draft.acknowledgment = null;
    if (geometry) {
      draft.groupReviews = [];
      for (const decision of draft.decisions) { decision.state = ""; decision.scope = null; decision.relationships = []; }
    }
  }
  function checkpoint(draft) {
    draft.undo.push(copy({ observation: draft.observation, cuts: draft.cuts, children: draft.children }));
    if (draft.undo.length > 32) draft.undo.shift();
  }
  function undo(draft) {
    const prior = draft.undo.pop();
    if (!prior) return false;
    Object.assign(draft, prior); invalidate(draft, true); return true;
  }
  function payload(draft, groups) {
    if (!draft.reason.trim() || !draft.evidence_ids.length) throw new Error("Explain this correction and select its source evidence.");
    if (draft.cuts.length > 127 || groups.length !== draft.cuts.length + 1 ||
        draft.cuts.some(cut => !cut.evidence_ids.length)) throw new Error("Review every boundary and resulting portion with source support.");
    if (draft.groupReviews?.length !== groups.length || draft.groupReviews.some(value => !value))
      throw new Error("Review the grouping of every resulting portion after changing the boundaries.");
    const dispositions = draft.decisions.map(decision => {
      if (!["active", "unresolved", "not_applicable"].includes(decision.state) || !decision.reason.trim() || !decision.evidence_ids.length)
        throw new Error("Explicitly decide every retained check, including its reason and evidence.");
      const result = { id: decision.id, fingerprint: decision.fingerprint, state: decision.state,
        scope: null, reading: null, evidence_ids: copy(decision.evidence_ids), relationships: [], reason: decision.reason.trim() };
      if (decision.state === "active") {
        if (!decision.scope || !decision.reading?.value || !decision.reading.evidence_ids?.length)
          throw new Error("Choose the active check scope and review its reading and evidence.");
        result.scope = copy(decision.scope); result.reading = copy(decision.reading);
        delete result.reading.supplemental;
        result.relationships = copy(decision.relationships);
      }
      return result;
    });
    return { generation_id: draft.snapshot.generation_id, root_id: draft.family.root_id,
      family_fingerprint: draft.family.family_fingerprint, root: copy(draft.observation),
      cuts: draft.cuts.map(cut => ({ source_point: copy(cut.source_point), evidence_ids: copy(cut.evidence_ids) })),
      groups: copy(groups), dispositions, evidence_ids: copy(draft.evidence_ids) };
  }
  function signature(draft, groups, context) {
    // Includes unfinished decisions and supplemental review state, not just valid payloads.
    return JSON.stringify([draft.snapshot, draft.observation, draft.cuts, groups, draft.groupReviews,
      draft.decisions, draft.evidence_ids, draft.reason, context]);
  }
  return { create, invalidate, checkpoint, undo, payload, signature };
});
