"use strict";

// The workflow owns quantities, source admission and stale-state validation.
// This module presents those results and collects corrections to observations.
((root, factory) => {
  const api = factory(typeof module === "object" && module.exports ? require("./path_editor.js") : root.HeleosPathEditor,
    typeof module === "object" && module.exports ? require("./arc_family_editor.js") : root.HeleosArcFamilyEditor,
    typeof module === "object" && module.exports ? require("./revision_checks_editor.js") : root.HeleosRevisionChecksEditor);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.HeleosDucts = api;
})(typeof globalThis === "object" ? globalThis : this, (pathTools, familyTools, revisionCheckTools) => {
  const isArc = (geometry) => ["circular_arc", "circular_arc_span"].includes(geometry?.kind);
  const list = (value) => Array.isArray(value) ? value : [];
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const workNames = { new_install: "New installation", existing_to_remain: "Existing to remain", demolition: "Demolition" };
  const label = (value) => String(value || "Unknown").replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());
  function issueText(issue) {
    const value = typeof issue === "string" ? issue : issue?.message || "Source evidence needs review.";
    return ({ source_changed: "The drawing source changed; read the current drawing again.",
      geometry_changed: "The drawing coordinates changed; read the current drawing again.",
      admission_missing: "The drawing role needs review.", admission_changed: "The source decision needs renewed review.",
      evidence_missing: "Supporting source evidence is missing.", role_unresolved: "Confirm whether this portion is ductwork.",
      graphic_support_missing: "Select or capture a drawing region showing this duct path. A label alone cannot support its length.",
      graphic_support_incomplete: "The cited drawing regions do not cover the full duct path. Capture the missing part or correct the path.",
      reading_relationship_missing: "Review how the supplemental reading applies to this duct portion using markers on both pages.",
      reading_relationship_stale: "The portion, reading or referenced source changed. Review the supplemental reading again.",
      reading_relationship_withdrawn: "This supplemental reading relationship was withdrawn; the dependent length remains unresolved.",
      reading_relationship_conflict: "Conflicting supplemental reading relationships need review.",
      reading_evidence_role: "Supplemental reading evidence cannot replace target-page graphics, labels or physical support.",
      correspondence_unresolved: "Review whether these depictions refer to the same physical portion.",
      coverage_incomplete: "Some drawing coverage still needs review.", coverage_empty: "No drawing portions have been accounted for.",
      coverage_observations_changed: "Portions changed since the drawing coverage review.",
      coverage_sources_changed: "Drawing sources changed since the coverage review.",
      coverage_evidence_missing: "Coverage evidence is missing.",
      dimension_conflict: "The source dimension and scaled route disagree; resolve the source reading or route.",
      split_partition_changed: "The corrected child routes no longer form the original continuous portion. Review the route and whole-run dimensions.",
      split_member_changed: "A split portion changed; review the connected child routes and retained whole-run evidence." })[value] || (/^[a-z][a-z0-9_]+$/.test(value) ? label(value) : value);
  }
  function groupLabel(group = {}) {
    const size = group.size;
    const dimensions = list(size?.dimensions).join(" × ");
    return [size && dimensions ? dimensions + " " + (size.unit || "in") + " · " + label(size.shape) : "Unknown size",
      group.system || "Unknown system", group.material || "Unknown material", workNames[group.work_status] || "Unknown work status"].join(" · ");
  }
  function sourceHighlight(segment) {
    const observation = segment.observation || {};
    const geometry = observation.geometry || segment.geometry || segment.source?.geometry || {};
    const points = segment.points || geometry.points || geometry.projection?.points;
    const source = clone(segment.source || observation.source);
    if (isArc(geometry)) return { source, extra: { geometry: clone(geometry) } };
    if (Array.isArray(points) && points.length > 1) return { source, extra: { points: clone(points) } };
    const bbox = segment.bbox || list(segment.evidence)[0]?.bbox;
    if (bbox) source.bbox = clone(bbox);
    return { source, extra: {} };
  }
  function createController({ getView, save, getActor = () => null }) {
    const attempted = new Set();
    const pending = new Set();
    const failures = new Map();
    const takeoff = () => getView().duct_takeoff || {};
    function snapshot(segment = null) {
      const current = takeoff();
      return { generation_id: current.current_generation_id, state_fingerprint: current.state_fingerprint,
        segment_id: segment?.id, segment_fingerprint: segment?.fingerprint,
        supersedes: segment?.latest_decision_id || null };
    }
    function current(draft) {
      const now = takeoff();
      if (draft.generation_id !== now.current_generation_id || draft.state_fingerprint !== now.state_fingerprint ||
          (draft.root_id && !list(now.arc_families).some(entry => entry.root_id === draft.root_id && entry.family_fingerprint === draft.family_fingerprint)) ||
          (draft.segment_id && !list(now.segments).some((entry) => entry.id === draft.segment_id && entry.fingerprint === draft.segment_fingerprint)) ||
          (draft.target_id && !list(now.reading_targets).some((entry) => entry.id === draft.target_id && entry.kind === draft.target_kind && entry.fingerprint === draft.target_fingerprint)))
        throw new Error("The duct results changed. Reopen this review using the current drawing results.");
    }
    async function prepare(id, retry = false) {
      const job = list(getView().duct_producer?.jobs).find((entry) => entry.id === id);
      if (job?.state !== "completed" || pending.has(id)) return false;
      if (list(takeoff().generations).some((entry) => list(entry.source_records).some((record) => record.id === id))) return false;
      if (list(takeoff().revisions).some((entry) => entry.producer_record_id === id)) return false;
      if (!retry && list(takeoff().refreshes).some((entry) => entry.producer_record_id === id)) return false;
      if (!retry && attempted.has(id)) return false;
      attempted.add(id); pending.add(id); failures.delete(id);
      try {
        const saved = await save("duct_prepare", { producer_record_id: id }, "Calculated duct lengths from the completed drawing reading");
        if (!saved) { attempted.delete(id); return false; }
        return true;
      } catch (error) { failures.set(id, error.message); throw error; }
      finally { pending.delete(id); }
    }
    function refreshStage(snapshot, applying = false) {
      const entry = list(takeoff().refreshes).find((value) => value.id === snapshot.id);
      if (!entry || entry.fingerprint !== snapshot.fingerprint || entry.state !== "pending" ||
          (applying && (entry.validity !== "current" || entry.base_generation_id !== takeoff().current_generation_id)))
        throw new Error("The staged drawing reading changed. Reopen its current review before continuing.");
      return entry;
    }
    function revisionNeedsCheckPins(stage) {
      const ids = new Set(list(stage.current_targets).map(target => target.id));
      const expected = [...list(takeoff().arc_families).filter(family => list(family.leaf_ids).some(id => ids.has(id))).flatMap(family => list(family.checks)),
        ...list(takeoff().revision_checks).filter(check => list(check.current_ids).some(id => ids.has(id)))];
      return expected.some(check => !list(stage.arc_check_index).some(pin => pin.id === check.id && pin.fingerprint === check.fingerprint));
    }
    function revisionStage(snapshot, applying = false) {
      const entry = list(takeoff().revisions).find((value) => value.id === snapshot.id);
      if (!entry || entry.fingerprint !== snapshot.fingerprint || entry.state !== "pending" ||
          (applying && (entry.validity !== "current" || entry.base_generation_id !== takeoff().current_generation_id)))
        throw new Error("The source revision or saved results changed. Restage it against the current results before applying decisions.");
      if (applying && revisionNeedsCheckPins(entry)) throw new Error("Restage this source revision to pin every current retained check before reviewing replacements.");
      return entry;
    }
    function familySnapshot(memberId) {
      const family = list(takeoff().arc_families).find(entry => entry.root_id === memberId ||
        list(entry.leaf_ids).includes(memberId) || list(entry.parents).some(parent => parent.id === memberId));
      if (!family) throw new Error("This saved arc family is no longer current. Reopen the current results.");
      return { ...snapshot(), root_id: family.root_id, family_fingerprint: family.family_fingerprint };
    }
    function currentFamily(draft) {
      current(draft);
      const family = list(takeoff().arc_families).find(entry => entry.root_id === draft.root_id && entry.family_fingerprint === draft.family_fingerprint);
      if (!family) throw new Error("The saved arc family changed. Reopen its current review.");
      return family;
    }
    return {
      snapshot, current, failures, familySnapshot, currentFamily,
      async repartition(draft, body, reason) {
        const family = currentFamily(draft);
        if (body.generation_id !== draft.generation_id || body.root_id !== family.root_id || body.family_fingerprint !== family.family_fingerprint ||
            JSON.stringify(body.root.source) !== JSON.stringify(family.root.source)) throw new Error("Keep this correction bound to its saved family and source.");
        if (!reason.trim()) throw new Error("Explain this saved family correction.");
        return save("duct_arc_repartition", clone(body), reason);
      },
      revisionStage, revisionNeedsCheckPins,
      revisionCheckSnapshot(entry) { return { ...snapshot(), check_id: entry.id, check_fingerprint: entry.fingerprint }; },
      currentRevisionCheck(draft) {
        current(draft);
        const entry = list(takeoff().revision_checks).find(value => value.id === draft.check_id && value.fingerprint === draft.check_fingerprint);
        if (!entry || entry.validity !== "current") throw new Error("The retained revision check changed. Reopen its current review.");
        return entry;
      },
      async reviewRevisionCheck(draft, disposition) {
        const entry = this.currentRevisionCheck(draft);
        const verified = revisionCheckTools.validate(entry, { ...disposition, id: entry.id, fingerprint: entry.fingerprint }, entry.targets,
          revisionCheckTools.evidence([...entry.evidence, ...list(takeoff().available_evidence).filter(value => value.validity === "current").map(value => value.evidence)], takeoff().available_evidence),
          revisionCheckTools.priorSources(entry, entry.targets), true, entry.current_ids);
        return save("duct_revision_check_review", { generation_id: draft.generation_id, check_id: entry.id,
          check_fingerprint: entry.fingerprint, disposition: verified }, disposition.reason);
      },
      async stageRevision(draft, producerId) {
        current(draft);
        if (!draft.generation_id || !producerId) throw new Error("Choose a saved drawing reading to stage for review.");
        return save("duct_revision_stage", { generation_id: draft.generation_id, producer_record_id: producerId },
          "Staged the saved drawing reading for explicit source and revision review");
      },
      async rejectRevision(stage, reason) {
        revisionStage(stage);
        if (!reason.trim()) throw new Error("Explain why this source revision should be rejected.");
        return save("duct_revision_reject", { revision_id: stage.id, revision_fingerprint: stage.fingerprint }, reason);
      },
      async reviewRevision(stage, groups) {
        const entry = revisionStage(stage, true);
        const incoming = groups.flatMap((group) => group.incoming_ids), selected = groups.flatMap((group) => group.current_ids);
        if (!groups.length || incoming.length !== entry.incoming.length || new Set(incoming).size !== incoming.length ||
            incoming.some((id) => !entry.incoming.some((candidate) => candidate.id === id)) || new Set(selected).size !== selected.length ||
            selected.some((id) => !entry.current_targets.some((candidate) => candidate.id === id)))
          throw new Error("Account for every new portion once and use each saved portion in at most one revision decision group.");
        return save("duct_revision_review", { revision_id: stage.id, revision_fingerprint: stage.fingerprint, groups: groups.map(group => revisionCheckTools.group(entry, group, revisionCheckTools.evidence([...list(entry.evidence), ...Object.values(takeoff().current_sources || {}).flatMap(source => list(source.evidence)), ...list(takeoff().available_evidence).filter(value => value.validity === "current").map(value => value.evidence)], takeoff().available_evidence))) },
          "Reviewed the old and new drawing sources and explicitly decided their duct scope");
      },
      readingSnapshot: (target) => ({ ...snapshot(), target_id: target.id, target_kind: target.kind, target_fingerprint: target.fingerprint }),
      async reviewReadings(draft, observation, relationships, reason) {
        current(draft);
        if (!draft.target_id || !reason.trim()) throw new Error("Choose the current reading target and explain the source relationship.");
        return save("duct_reading_review", { generation_id: draft.generation_id, target_kind: draft.target_kind,
          target_id: draft.target_id, target_fingerprint: draft.target_fingerprint, observation: clone(observation), relationships: clone(relationships) }, reason);
      },
      async correctParentReadings(draft, readingUpdates, relationships, reason) {
        current(draft);
        if (draft.target_kind !== "split_parent" || !readingUpdates.length || !reason.trim())
          throw new Error("Change an existing parent dimension and explain its source evidence.");
        return save("duct_parent_readings_correct", { generation_id: draft.generation_id, parent_id: draft.target_id,
          parent_fingerprint: draft.target_fingerprint, reading_updates: clone(readingUpdates), relationships: clone(relationships) }, reason);
      },
      async correctParentGraphics(draft, addIds, removeIds, relationships, reason) {
        current(draft);
        if (draft.target_kind !== "split_parent" || (!addIds.length && !removeIds.length) || !reason.trim())
          throw new Error("Choose an explicit parent graphic change and explain the source evidence.");
        return save("duct_parent_graphics_correct", { generation_id: draft.generation_id, parent_id: draft.target_id,
          parent_fingerprint: draft.target_fingerprint, add_evidence_ids: clone(addIds), remove_evidence_ids: clone(removeIds), relationships: clone(relationships) }, reason);
      },
      async withdrawReading(draft, relationship, reason) {
        current(draft);
        const now = list(takeoff().reading_relationships).find((entry) => entry.id === relationship.id);
        if (!now || now.fingerprint !== relationship.fingerprint || now.latest_event_id !== relationship.latest_event_id || now.validity === "withdrawn")
          throw new Error("The reading relationship changed. Reopen its current review.");
        if (!reason.trim()) throw new Error("Explain why the supplemental reading relationship is being withdrawn.");
        return save("duct_reading_withdraw", { generation_id: draft.generation_id, relationship_id: now.id,
          relationship_fingerprint: now.fingerprint, supersedes: now.latest_event_id }, reason);
      },
      active: () => list(getView().duct_producer?.jobs).some((job) => ["queued", "running"].includes(job.state)),
      retry: (id) => prepare(id, true),
      refreshStage,
      async restage(snapshot) {
        const entry = refreshStage(snapshot);
        if (entry.validity !== "stale") throw new Error("This drawing reading already uses the current results.");
        return save("duct_prepare", { producer_record_id: entry.producer_record_id }, "Restaged the retained drawing reading against current duct results");
      },
      async rejectRefresh(snapshot, reason) {
        refreshStage(snapshot);
        if (!reason.trim()) throw new Error("Explain why this drawing reading should be rejected.");
        return save("duct_refresh_reject", { refresh_id: snapshot.id, refresh_fingerprint: snapshot.fingerprint }, reason);
      },
      async reviewRefresh(snapshot, groups) {
        const entry = refreshStage(snapshot, true);
        const incoming = groups.flatMap((group) => group.incoming_ids), current = groups.flatMap((group) => group.current_ids);
        if (incoming.length !== entry.incoming.length || new Set(incoming).size !== incoming.length ||
            incoming.some((id) => !entry.incoming.some((candidate) => candidate.id === id)) ||
            new Set(current).size !== current.length || current.some((id) => !entry.current_ids.includes(id)))
          throw new Error("Account for every new portion once and use each saved portion in at most one decision group.");
        return save("duct_refresh_review", { refresh_id: snapshot.id, refresh_fingerprint: snapshot.fingerprint, groups: clone(groups) },
          "Reviewed the new drawing reading against saved duct portions with source-backed decisions");
      },
      async reconcile() {
        let changed = false;
        let failure = null;
        for (const job of list(getView().duct_producer?.jobs)) {
          if (job.state !== "completed") continue;
          try { changed = (await prepare(job.id)) || changed; }
          catch (error) { failure ||= error; }
        }
        if (failure) throw failure;
        return changed;
      },
      start: (source) => save("duct_find", { source }, "Find ductwork on the selected drawing with the configured local model"),
      cancel: (id) => save("duct_find_cancel", { job_id: id }, "Stopped the duct drawing reading"),
      async correct(draft, changes, reason) {
        current(draft);
        return save("duct_correct", { segment_id: draft.segment_id, segment_fingerprint: draft.segment_fingerprint,
          changes: clone(changes), supersedes: draft.supersedes }, reason);
      },
      async capture(draft, input, reason) {
        current(draft);
        const version = getView().version, actor = getActor();
        if (!await save("duct_capture_evidence", { generation_id: draft.generation_id, ...clone(input) }, reason)) return null;
        current(draft);
        const result = takeoff(), acknowledgment = result.capture_ack;
        const captured = list(result.available_evidence).find((entry) => entry.id === result.last_capture_id && entry.validity === "current");
        if (!acknowledgment || !captured || acknowledgment.evidence_id !== captured.id ||
            acknowledgment.generation_id !== draft.generation_id || acknowledgment.base_workflow_version !== version ||
            acknowledgment.saved_workflow_version !== getView().version || (actor !== null && acknowledgment.actor !== actor))
          throw new Error("The source capture changed. Refresh the current drawing evidence before using it.");
        return clone(captured);
      },
      async add(draft, observation, reason) {
        current(draft);
        return save("duct_add", { generation_id: draft.generation_id, observation: clone(observation) }, reason);
      },
      async split(draft, cuts, groups, reason) {
        current(draft);
        return save("duct_split", { generation_id: draft.generation_id, segment_id: draft.segment_id,
          segment_fingerprint: draft.segment_fingerprint, cuts: clone(cuts), groups: clone(groups) }, reason);
      },
      async review(draft, coverage, correspondences, reason) {
        current(draft);
        return save("duct_review_context", { generation_id: draft.generation_id,
          coverage: clone(coverage), correspondences: clone(correspondences) }, reason);
      },
      async recalculate(draft, calibrationIds = {}) {
        current(draft);
        return save("duct_recalculate", { generation_id: draft.generation_id, calibration_ids: calibrationIds },
          "Recalculated duct quantities using current source evidence and verified scales");
      },
    };
  }

  function createPanel(env) {
    const controller = createController(env);
    const doc = env.document;
    let editing = null;
    let coverageDraft = null;
    let selectedPage = "";
    let pathPicking = false;
    let pathRequest = null;
    let pathDraftTarget = null;
    let splitting = null;
    let familyEditing = null;
    let refreshEditing = null;
    const refreshNotes = new Map();
    let revisionEditing = null;
    let revisionCheckEditing = null;
    let historicalProducer = "";
    const revisionNotes = new Map();
    let supplementalCapture = null;
    const withdrawalNotes = new Map();
    function node(tag, text = "", className = "") {
      const element = doc.createElement(tag); element.textContent = text; element.className = className; return element;
    }
    function button(title, action, disabled = false) {
      const element = node("button", title, "button button-outline"); element.type = "button";
      element.disabled = env.isBusy() || disabled;
      element.addEventListener("click", () => Promise.resolve().then(action).catch((e) => env.error(e.message)));
      return element;
    }
    function field(target, title, value, update, choices = null) {
      const wrapper = node("label", title, "field-label");
      const input = node(choices ? "select" : "input");
      if (choices) for (const [key, name] of choices) { const option = node("option", name); option.value = key; input.append(option); }
      else input.maxLength = 500;
      input.value = value ?? ""; input.disabled = env.isBusy();
      input.addEventListener(choices ? "change" : "input", () => update(input.value));
      wrapper.append(input); target.append(wrapper); return input;
    }
    function checkbox(target, title, checked, change) {
      const wrapper = node("label", "", "field-label"); const input = node("input");
      input.type = "checkbox"; input.checked = checked; input.disabled = env.isBusy();
      input.addEventListener("change", () => change(input.checked));
      wrapper.append(input, node("span", title)); target.append(wrapper); return input;
    }
    function details(target, title, opened = false) {
      const element = node("details", "", "workflow-record"); element.open = opened;
      element.append(node("summary", title)); target.append(element); return element;
    }
    function pageGeometry(source) {
      const view = env.getView();
      const page = [view.duct_takeoff?.current_sources?.[source.sheet_id]?.geometry,
        view.sheet_geometries?.[source.revision_id + ":" + source.index],
        ...list(view.duct_takeoff?.generations).map((generation) => generation.current_sources?.[source.sheet_id]?.geometry)]
        .find((entry) => entry?.fingerprint === source.geometry_fingerprint && entry.sheet_id === source.sheet_id);
      return page && page.fingerprint === source.geometry_fingerprint && page.sheet_id === source.sheet_id ? clone(page) : null;
    }
    function show(segment) {
      const value = sourceHighlight(segment);
      if (isArc(value.extra.geometry)) value.extra.page_geometry = pageGeometry(value.source);
      env.showSource(value.source, value.extra);
    }
    const refresh = async (operation) => { if (await operation) env.render(); };
    function sources(target, evidence) {
      for (const entry of list(evidence)) target.append(button(entry.text ? (entry.text_origin === "operator_transcription" ? "Show transcribed “" : "Show “") + entry.text.slice(0, 100) + "”" : "Show supporting drawing region",
        () => env.showSource({ ...entry.source, bbox: entry.bbox })));
    }
    function evidenceChoices(target, evidence, chosen) {
      if (!list(evidence).length) target.append(node("p", "No retained source regions are available for this review.", "section-hint"));
      for (const entry of list(evidence)) {
        checkbox(target, entry.text || "Drawing evidence · page " + (entry.source.index + 1), chosen.includes(entry.id), (checked) => {
          const index = chosen.indexOf(entry.id);
          if (checked && index < 0) chosen.push(entry.id);
          if (!checked && index >= 0) chosen.splice(index, 1);
        });
        sources(target, [entry]);
      }
    }
    const sameSource = (a, b) => ["revision_id", "index", "sheet_id", "geometry_fingerprint"].every((key) => a?.[key] === b?.[key]);
    function sourceEvidence(source, fallback = []) {
      const data = env.getView().duct_takeoff || {};
      const context = Object.values(data.current_sources || {}).find((entry) =>
        entry.geometry.revision_id === source.revision_id && entry.geometry.index === source.index);
      const entries = [...list(context?.evidence), ...fallback,
        ...list(data.available_evidence).filter((entry) => entry.validity === "current").map((entry) => ({ ...entry.evidence, kind: entry.kind, text_origin: entry.text_origin }))];
      return [...new Map(entries.filter((entry) => sameSource(entry.source, source))
        .map((entry) => [entry.id, entry])).values()];
    }
    function geometryDraft(observation) {
      const geometry = observation.geometry;
      const slot = (id) => {
        const reading = list(observation.readings).find((entry) => entry.id === id);
        return reading ? clone(reading) : { id: null, value: "", unit: "ft", datum_id: null, evidence_ids: [] };
      };
      const lower = slot(list(geometry.elevation_ids)[0]);
      return { kind: geometry.kind, points: clone(geometry.points || geometry.projection?.points || []),
        scale: geometry.scale_fact_id || geometry.projection?.scale_fact_id || "",
        dimensionChecks: list(geometry.dimension_check_ids).map(slot),
        radiusChecks: list(geometry.radius_check_ids).map(slot),
        verticalMethod: geometry.rise_reading_id ? "rise" : list(geometry.elevation_ids).length ? "elevations" : "unknown",
        rise: slot(geometry.rise_reading_id), projectionMethod: geometry.projection?.kind || "scaled_path",
        projection: slot(geometry.projection?.reading_id), lower, upper: slot(list(geometry.elevation_ids)[1]),
        datum: lower.datum_id || "", supportIds: clone(geometry.support_ids || []), reason: geometry.reason || "" };
    }
    function composeObservation(draft) {
      const result = clone(draft.observation), shape = draft.geometry;
      const readings = [];
      function reading(slot, kind = "length", datum = null) {
        if (!String(slot.value).trim() || !slot.evidence_ids.length) throw new Error("Enter the reading and choose its source evidence.");
        if (kind === "elevation" && !datum) throw new Error("Choose the shared source datum for both elevations.");
        const evidenceIds = slot.evidence_ids.filter((id) => id !== slot.datum_id || id === datum);
        if (datum) evidenceIds.push(datum);
        slot.id ||= env.newId();
        readings.push({ id: slot.id, kind, value: String(slot.value).trim(), unit: slot.unit,
          datum_id: datum, evidence_ids: [...new Set(evidenceIds)] });
        return slot.id;
      }
      const elevations = () => [reading(shape.lower, "elevation", shape.datum), reading(shape.upper, "elevation", shape.datum)];
      result.schema = shape.kind === "circular_arc_span" ? "duct-observation-3" : shape.kind === "circular_arc" ? "duct-observation-2" : "duct-observation-1";
      if (shape.kind === "planar") result.geometry = { kind: "planar", points: clone(shape.points), scale_fact_id: shape.scale,
        dimension_check_ids: shape.dimensionChecks.map((entry) => reading(entry)) };
      else if (shape.kind === "circular_arc_span") {
        if (draft.observation.geometry.kind !== "circular_arc_span") throw new Error("Reopen the saved circular interval.");
        result.geometry = { ...clone(draft.observation.geometry), scale_fact_id: shape.scale,
          dimension_check_ids: shape.dimensionChecks.map(entry => reading(entry)),
          radius_check_ids: shape.radiusChecks.map(entry => reading(entry)) };
      }
      else if (shape.kind === "circular_arc") {
        if (shape.points.length !== 3) throw new Error("Select the arc start, a point on the sweep, and the end.");
        result.geometry = { kind: "circular_arc", points: clone(shape.points), scale_fact_id: shape.scale,
          dimension_check_ids: shape.dimensionChecks.map((entry) => reading(entry)),
          radius_check_ids: shape.radiusChecks.map((entry) => reading(entry)) };
      }
      else if (shape.kind === "vertical") result.geometry = { kind: "vertical", support_ids: clone(shape.supportIds),
        rise_reading_id: shape.verticalMethod === "rise" || (shape.verticalMethod === "unknown" && shape.rise.value.trim()) ? reading(shape.rise) : null,
        elevation_ids: shape.verticalMethod === "elevations" ? elevations() : [] };
      else if (shape.kind === "slope") result.geometry = { kind: "slope", support_ids: clone(shape.supportIds),
        projection: shape.projectionMethod === "dimension" ? { kind: "dimension", reading_id: reading(shape.projection) } :
          { kind: "scaled_path", points: clone(shape.points), scale_fact_id: shape.scale }, elevation_ids: elevations() };
      else result.geometry = { kind: "unsupported", reason: shape.reason.trim(), points: clone(shape.points), support_ids: clone(shape.supportIds) };
      result.readings = readings;
      if (draft.sizeChanged) result.group.size = !draft.shape ? null : { shape: draft.shape,
        dimensions: draft.sizeText.split(/\s*[x×,]\s*/i).map((v) => v.trim()), unit: draft.sizeUnit, original_text: draft.sizeText.trim() };
      result.group.evidence_ids = clone(draft.groupEvidenceIds);
      const priorReadingEvidence = new Set(list(draft.observation.readings).flatMap((entry) => list(entry.evidence_ids)));
      const retainedEvidence = result.evidence_ids.filter((id) => !priorReadingEvidence.has(id) || draft.sourceEvidence.some((entry) => entry.id === id));
      result.evidence_ids = [...new Set([...retainedEvidence, ...draft.evidence_ids, ...draft.groupEvidenceIds, ...shape.supportIds,
        ...readings.flatMap((entry) => entry.evidence_ids)])];
      if (!result.evidence_ids.length) throw new Error("Choose or capture source evidence for this duct portion.");
      if ((result.group.size || result.group.system || result.group.material || result.group.work_status) && !result.group.evidence_ids.length)
        throw new Error("Choose the source labels supporting the known duct properties.");
      return result;
    }
    function stopPicking() {
      const prior = pathRequest; pathRequest = null; pathDraftTarget = null;
      if (prior) env.pathCommand({ request_id: prior.request_id, source: prior.source, action: "cancel" });
      pathPicking = false;
      if (familyEditing) familyTools.invalidate(familyEditing);
      if (revisionEditing) invalidateRevision(revisionEditing);
      if (revisionCheckEditing) revisionCheckEditing.acknowledgment = null;
      if (familyEditing?.capture) familyEditing.capture.active = false;
      if (editing?.capture) editing.capture.active = false;
      if (splitting?.capture) splitting.capture.active = false;
      if (refreshEditing?.captureDraft) refreshEditing.captureDraft.capture.active = false;
      if (supplementalCapture) supplementalCapture.capture.active = false;
      env.pickRegion(false); env.pickLine(false);
    }
    function startPath(draft, mode) {
      controller.current(draft.snapshot);
      stopPicking();
      const points = mode === "arc_split" || (draft.familyEdit && mode === "circular_arc" && draft.geometry.points.some(point => point.some(value => typeof value !== "number"))) ? [] : (mode === "straight" && draft.geometry.points.length !== 2) ||
        (mode === "circular_arc" && draft.geometry.points.length !== 3) ? [] : clone(draft.geometry.points);
      pathRequest = { request_id: env.newId(), source: clone(draft.observation.source), mode, points };
      if (mode === "arc_split") pathRequest.geometry = clone(draft.observation.geometry);
      if (["circular_arc", "arc_split"].includes(mode)) {
        pathRequest.page_geometry = pageGeometry(pathRequest.source);
        if (!pathRequest.page_geometry) throw new Error("Refresh the source coordinates before editing this arc.");
      }
      pathDraftTarget = draft;
      env.showSource({ ...draft.observation.source, bbox: [0, 0, 1, 1] }, ["circular_arc", "arc_split"].includes(mode) ?
        { geometry: mode === "arc_split" ? clone(draft.observation.geometry) : { kind: "circular_arc", points }, page_geometry: pathRequest.page_geometry } :
        draft.geometry.points.length > 1 ? { points: draft.geometry.points } : {});
      env.editPath(pathRequest);
    }
    function pathControls(target, draft, straight = false) {
      const arc = draft.geometry.kind === "circular_arc";
      target.append(node("p", arc ? "Three anchors: start, a point along the intended sweep, end. Source evidence must establish a circular centerline." :
        draft.geometry.points.length + " centerline points", "section-hint"));
      target.append(button(arc ? "Edit circular arc anchors" : straight ? "Edit projection endpoints" : "Edit centerline vertices",
        () => startPath(draft, arc ? "circular_arc" : straight ? "straight" : "polyline")));
      if (pathRequest && pathRequest.mode !== "split") for (const [action, title] of [["undo", "Undo path edit"], ["finish", "Finish path"], ["cancel", "Cancel path edit"]])
        target.append(button(title, () => env.pathCommand({ request_id: pathRequest.request_id, source: pathRequest.source, action })));
    }
    function scaleField(target, draft) {
      const source = draft.observation.source;
      const facts = list(env.getView().calibrations).filter((fact) => fact.state === "verified" && fact.source.revision_id === source.revision_id && fact.source.index === source.index);
      const options = [["", "Choose a verified scale"], ...facts.map((fact) => [fact.id, fact.label || "Verified drawing scale"])];
      if (draft.geometry.scale && !facts.some((fact) => fact.id === draft.geometry.scale)) options.push([draft.geometry.scale, "Saved scale · verify before use"]);
      field(target, "Drawing scale", draft.geometry.scale, (value) => { draft.geometry.scale = value; if (draft.familyEdit) { draft.observation.geometry.scale_fact_id = value; familyTools.invalidate(draft, true); env.render(); } }, options);
    }
    function readingFields(target, title, reading, evidence, sourceTitle = title + " source") {
      field(target, title, reading.value, (value) => { reading.value = value; if (reading.supplemental) reading.supplemental.reviewed = false; });
      field(target, title + " unit", reading.unit, (value) => { reading.unit = value; if (reading.supplemental) reading.supplemental.reviewed = false; }, [["ft", "Feet"], ["in", "Inches"], ["m", "Meters"], ["mm", "Millimeters"]]);
      const chosen = reading.evidence_ids.find((id) => id !== reading.datum_id) || "";
      const options = [["", "Choose retained source evidence"], ...evidence.map((entry) => [entry.id, entry.text || "Drawing region"])];
      if (chosen && !evidence.some((entry) => entry.id === chosen)) options.push([chosen, "Supplemental reading · review below"]);
      field(target, sourceTitle, chosen, (value) => {
        reading.evidence_ids = value ? [value] : [];
        reading.datum_id = null;
        if (reading.supplemental) { reading.supplemental = null; env.render(); }
      }, options);
    }
    function consumedSlots(draft) {
      const shape = draft.geometry, slots = [];
      if (["planar", "circular_arc", "circular_arc_span"].includes(shape.kind)) shape.dimensionChecks.forEach((reading, index) =>
        slots.push({ title: (isArc(shape) ? (shape.kind === "circular_arc_span" ? "Retained arc portion length check " : "Whole arc length check ") : "Dimension check ") + (index + 1), reading }));
      if (isArc(shape)) shape.radiusChecks.forEach((reading, index) =>
        slots.push({ title: "Centerline radius check " + (index + 1), reading }));
      if (shape.kind === "vertical" && shape.verticalMethod !== "elevations") slots.push({ title: "Supported rise", reading: shape.rise });
      if (shape.kind === "slope" && shape.projectionMethod === "dimension") slots.push({ title: "Projection", reading: shape.projection });
      if (shape.kind === "slope" || (shape.kind === "vertical" && shape.verticalMethod === "elevations"))
        slots.push({ title: "Lower elevation", reading: shape.lower }, { title: "Upper elevation", reading: shape.upper });
      return slots;
    }
    function supplementalPages(source) {
      const view = env.getView();
      return list(view.inventory).filter((page) => ["plan", "detail", "riser"].includes(page.assignment?.role)).map((page) => {
        const geometry = view.sheet_geometries?.[page.revision_id + ":" + page.index];
        if (!geometry?.sheet_id || !geometry.fingerprint) return null;
        return { ...page, source: { revision_id: page.revision_id, index: page.index,
          sheet_id: geometry.sheet_id, geometry_fingerprint: geometry.fingerprint } };
      }).filter((page) => page && !sameSource(page.source, source));
    }
    function generationEvidence(generation) {
      return [...Object.values(generation.current_sources || {}).flatMap((entry) => list(entry.evidence)),
        ...list(generation.user_evidence_refs).map((entry) => entry.record?.evidence).filter(Boolean)];
    }
    function allSourceEvidence(retained = false) {
      const data = env.getView().duct_takeoff;
      const evidence = [...Object.values(data.current_sources || {}).flatMap((entry) => list(entry.evidence)),
        ...list(data.available_evidence).filter((entry) => retained || entry.validity === "current").map((entry) => entry.evidence)];
      if (!retained) return evidence;
      return [...new Map([...evidence, ...list(data.generations).flatMap(generationEvidence)]
        .map((entry) => [JSON.stringify([entry.id, sourceKey(entry.source)]), entry])).values()];
    }
    function initializeSupplemental(draft, targetId) {
      const evidence = allSourceEvidence(), relationships = list(env.getView().duct_takeoff.reading_relationships);
      for (const { reading } of consumedSlots(draft)) {
        const prior = relationships.find((entry) => entry.target_id === targetId && entry.reading_id === reading.id);
        const foreign = evidence.filter((entry) => reading.evidence_ids.includes(entry.id) && !sameSource(entry.source, draft.observation.source));
        const fullyLocal = reading.evidence_ids.length > 0 && reading.evidence_ids.every((id) =>
          evidence.some((entry) => entry.id === id && sameSource(entry.source, draft.observation.source)));
        if (!foreign.length && fullyLocal) continue;
        const sources = [...new Map(foreign.map((entry) => [JSON.stringify(entry.source), entry.source])).values()];
        const source = sources.length === 1 ? sources[0] : prior?.record.reading_source;
        if (source) reading.supplemental = { source: clone(source), reviewed: false, supersedes: prior?.latest_event_id || null,
          observation_reference_evidence_ids: clone(prior?.record.observation_reference_evidence_ids || []),
          reading_reference_evidence_ids: clone(prior?.record.reading_reference_evidence_ids || []) };
      }
    }
    function invalidateGraphicReview(draft) {
      if (draft?.mode === "parent-graphic") for (const { reading } of consumedSlots(draft)) if (reading.supplemental) reading.supplemental.reviewed = false;
    }
    function beginSupplementalCapture(draft, source, kind, graphicOnly = false) {
      stopPicking();
      invalidateGraphicReview(draft);
      supplementalCapture = { owner: draft, readingCapture: true, graphicCapture: graphicOnly, snapshot: draft.snapshot, observation: { source: clone(source) },
        sourceEvidence: sourceEvidence(source), evidence_ids: [], groupEvidenceIds: [], geometry: { supportIds: [] },
        capture: { kind, text: "", bbox: null, active: false } };
      env.render();
    }
    function parentReadingProposal(draft) {
      const observation = clone(draft.observation), ids = [...list(observation.geometry.dimension_check_ids), ...list(observation.geometry.radius_check_ids)], updates = [];
      const slots = consumedSlots(draft).map(entry => entry.reading);
      if (!["planar", "circular_arc", "circular_arc_span"].includes(observation.geometry.kind) || slots.length !== ids.length)
        throw new Error("Reopen the current retained parent dimension checks.");
      for (const [index, reading] of slots.entries()) {
        const original = observation.readings.find((entry) => entry.id === ids[index]);
        if (!original || original.kind !== "length" || reading.id !== ids[index]) throw new Error("Keep every existing parent dimension check and reading identity.");
        const value = String(reading.value).trim(), evidence = clone(reading.evidence_ids);
        if (!value || !evidence.length) throw new Error("Enter each retained parent dimension and choose its source evidence.");
        if (value !== String(original.value) || reading.unit !== original.unit || JSON.stringify(evidence) !== JSON.stringify(original.evidence_ids)) {
          updates.push({ reading_id: original.id, value, unit: reading.unit, evidence_ids: evidence });
          original.value = value; original.unit = reading.unit; original.evidence_ids = evidence;
        }
      }
      return { observation, updates };
    }
    function readingReviewObservation(draft) {
      if (draft.mode === "revision-check") {
        const decision = clone(draft.decision); delete decision.reading.supplemental; delete decision.relationships;
        return { decision, observation: draft.observation, context: revisionCheckContext(), owner: draft.ownerKey };
      }
      if (draft.mode === "family-check") {
        const reading = clone(draft.decision.reading); delete reading.supplemental;
        return { root: draft.ownerFamily.observation, cuts: draft.ownerFamily.cuts, groups: childGroups(draft.ownerFamily),
          scope: draft.decision.scope, reading, evidence_ids: draft.ownerFamily.evidence_ids,
          decision_evidence: draft.decision.evidence_ids, context: familyContext(draft.ownerFamily) };
      }
      if (draft.mode === "parent") return draft.observation;
      if (draft.mode === "parent-edit") return parentReadingProposal(draft).observation;
      if (draft.mode === "parent-graphic") {
        const observation = clone(draft.observation);
        observation.evidence_ids = observation.evidence_ids.filter((id) => !draft.removeGraphicIds.includes(id)).concat(draft.addGraphicIds.slice().sort());
        return observation;
      }
      return composeObservation(draft);
    }
    function supplementalFields(target, draft) {
      const slots = consumedSlots(draft), pages = supplementalPages(draft.observation.source);
      if (!slots.length || draft.mode === "add") return;
      const panel = details(target, "Readings from another drawing page", slots.some(({ reading }) => reading.supplemental));
      panel.append(node("p", "Link each supplemental reading to this portion using reference markers on both drawings. Each view keeps its own scale; links do not resolve conflicting dimensions or equate different elevation datums.", "section-hint"));
      for (const { title, reading } of slots) {
        const entry = details(panel, title, !!reading.supplemental);
        const link = reading.supplemental;
        if (!["parent", "parent-graphic"].includes(draft.mode)) field(entry, "Supplemental page for " + title, link ? link.source.revision_id + ":" + link.source.index : "", (value) => {
          stopPicking();
          const page = pages.find((candidate) => candidate.revision_id + ":" + candidate.index === value);
          const prior = list(env.getView().duct_takeoff.reading_relationships).find((value) => value.target_id === (draft.segment?.id || draft.target?.id) && value.reading_id === reading.id);
          reading.supplemental = page ? { source: clone(page.source), observation_reference_evidence_ids: [], reading_reference_evidence_ids: [], reviewed: false,
            supersedes: prior?.latest_event_id || null } : null;
          reading.evidence_ids = reading.evidence_ids.filter((id) => draft.sourceEvidence.some((entry) => entry.id === id)); env.render();
        }, [["", "Use this portion’s own page"], ...pages.map((page) => [page.revision_id + ":" + page.index,
          (page.assignment?.label || page.document_name) + " · " + label(page.assignment?.role) + " · page " + (page.index + 1)])]);
        if (!link) continue;
        const foreign = sourceEvidence(link.source);
        const readingEvidence = [...draft.sourceEvidence, ...foreign];
        if (!["parent", "parent-graphic"].includes(draft.mode)) field(entry, "Supplemental reading source for " + title, reading.evidence_ids.find((id) => id !== reading.datum_id) || "", (value) => {
          reading.evidence_ids = value ? [value] : []; reading.datum_id = null; link.reviewed = false; env.render();
        }, [["", "Choose the written dimension or elevation"], ...readingEvidence.map((evidence) => [evidence.id,
          (sameSource(evidence.source, draft.observation.source) ? "Target page · " : "Supplemental page · ") + (evidence.text || "Drawing region")])]);
        const selected = readingEvidence.find((evidence) => reading.evidence_ids.includes(evidence.id));
        if (selected) entry.append(button("Show supplemental reading for " + title, () => env.showSource({ ...selected.source, bbox: selected.bbox })));
        function markers(container, prefix, evidence, key) {
          for (const value of evidence) checkbox(container, prefix + " · " + (value.text || "Drawing region"), link[key].includes(value.id), (checked) => {
            link[key] = checked ? [...new Set([...link[key], value.id])] : link[key].filter((id) => id !== value.id);
            link.reviewed = false; env.render();
          });
          const markerSource = key === "observation_reference_evidence_ids" ? draft.observation.source : link.source;
          for (const id of link[key].filter((value) => !evidence.some((entry) => entry.id === value))) {
            const retained = allSourceEvidence(true).find((entry) => entry.id === id && sameSource(entry.source, markerSource));
            container.append(node("p", "Unavailable saved " + prefix.toLowerCase() + (retained?.text ? " · " + retained.text : ""), "error"));
            if (retained) sources(container, [retained]);
            container.append(button("Remove unavailable " + prefix.toLowerCase(), () => {
              link[key] = link[key].filter((value) => value !== id); link.reviewed = false; env.render();
            }));
          }
          sources(container, evidence);
        }
        const targetMarkers = details(entry, "Target reference markers for " + title, true);
        markers(targetMarkers, "Target marker", draft.sourceEvidence, "observation_reference_evidence_ids");
        const sourceMarkers = details(entry, "Supplemental reference markers for " + title, true);
        markers(sourceMarkers, "Supplemental marker", foreign, "reading_reference_evidence_ids");
        if (draft.mode !== "parent-graphic") entry.append(button("Capture supplemental evidence for " + title, () => beginSupplementalCapture(draft, link.source, reading.kind === "elevation" ? "elevation" : "dimension")),
          button("Capture target marker for " + title, () => beginSupplementalCapture(draft, draft.observation.source, "label")));
        let signature = null;
        try { signature = JSON.stringify(readingReviewObservation(draft)); } catch (_) { /* Incomplete drafts cannot be acknowledged. */ }
        checkbox(entry, "Reviewed applicability of " + title + " to this duct portion", link.reviewed && signature !== null && link.reviewedObservation === signature, (checked) => {
          link.reviewed = false; link.reviewedObservation = null;
          if (checked) {
            try {
              link.reviewedObservation = JSON.stringify(readingReviewObservation(draft));
              link.reviewed = true;
            } catch (error) { env.error(error.message); }
          }
          env.render();
        });
      }
      if (supplementalCapture?.owner === draft && draft.mode !== "parent-graphic") captureFields(panel, supplementalCapture);
    }
    function reviewedSupplemental(draft) {
      const signature = JSON.stringify(readingReviewObservation(draft));
      return consumedSlots(draft).filter(({ reading }) => reading.supplemental).map(({ reading }) => {
        const link = reading.supplemental;
        if (!link.reviewed || link.reviewedObservation !== signature || !link.observation_reference_evidence_ids.length || !link.reading_reference_evidence_ids.length)
          throw new Error("Review each supplemental reading and choose reference markers on both source pages.");
        return { reading_id: reading.id, reading_source: clone(link.source),
          observation_reference_evidence_ids: clone(link.observation_reference_evidence_ids),
          reading_reference_evidence_ids: clone(link.reading_reference_evidence_ids), supersedes: link.supersedes || null };
      });
    }
    function geometryFields(target, draft) {
      const shape = draft.geometry;
      if (shape.kind === "circular_arc_span") target.append(node("p", "This derived circular portion retains its original circle and saved boundaries. Split it again to add size changes. Use Correct saved arc family to move or remove saved boundaries and review retained checks.", "section-hint"));
      else field(target, "Geometry basis", shape.kind, (value) => { stopPicking(); shape.kind = value; env.render(); },
        [["planar", "Drawn planar centerline"], ["circular_arc", "Supported circular centerline arc"], ["vertical", "Vertical rise"], ["slope", "Straight sloped portion"], ["unsupported", "Unsupported or uncertain"]]);
      if (["planar", "circular_arc", "circular_arc_span"].includes(shape.kind)) {
        if (shape.kind !== "circular_arc_span") pathControls(target, draft);
        scaleField(target, draft);
        const checks = details(target, shape.kind === "circular_arc" ? "Explicit whole arc lengths to check against scale" : "Explicit dimensions to check against scale");
        for (const [index, reading] of shape.dimensionChecks.entries()) {
          readingFields(checks, "Dimension check " + (index + 1), reading, draft.sourceEvidence);
          checks.append(button("Remove dimension check " + (index + 1), () => { shape.dimensionChecks.splice(index, 1); env.render(); }));
        }
        checks.append(button("Add dimension check", () => { shape.dimensionChecks.push({ id: null, value: "", unit: "ft", datum_id: null, evidence_ids: [] }); env.render(); }));
      }
      if (isArc(shape)) {
        const checks = details(target, "Explicit centerline radius checks");
        checks.append(node("p", "Choose evidence explicitly identifying the centerline radius. Inside radius, outside radius, diameter and chord dimensions do not supply this check.", "section-hint"));
        for (const [index, reading] of shape.radiusChecks.entries()) {
          readingFields(checks, "Centerline radius check " + (index + 1), reading, draft.sourceEvidence);
          checks.append(button("Remove radius check " + (index + 1), () => { shape.radiusChecks.splice(index, 1); env.render(); }));
        }
        checks.append(button("Add centerline radius check", () => { shape.radiusChecks.push({ id: null, value: "", unit: "ft", datum_id: null, evidence_ids: [] }); env.render(); }));
      }
      if (shape.kind === "vertical") {
        field(target, "Rise basis", shape.verticalMethod, (value) => { shape.verticalMethod = value; if (value === "unknown") shape.rise.value = ""; env.render(); },
          [["unknown", "Height unknown"], ["rise", "Direct rise dimension"], ["elevations", "Difference of two elevations"]]);
        if (shape.verticalMethod !== "elevations") readingFields(target, "Supported rise", shape.rise, draft.sourceEvidence, "Rise source dimension");
      }
      if (shape.kind === "slope") {
        field(target, "Projection method", shape.projectionMethod, (value) => { shape.projectionMethod = value; env.render(); },
          [["scaled_path", "Scaled straight projection"], ["dimension", "Dimensioned projection"]]);
        if (shape.projectionMethod === "scaled_path") { pathControls(target, draft, true); scaleField(target, draft); }
        else readingFields(target, "Projection", shape.projection, draft.sourceEvidence);
      }
      if (shape.kind === "slope" || (shape.kind === "vertical" && shape.verticalMethod === "elevations")) {
        readingFields(target, "Lower elevation", shape.lower, draft.sourceEvidence);
        readingFields(target, "Upper elevation", shape.upper, draft.sourceEvidence);
        const datumEvidence = [...new Map([...draft.sourceEvidence, ...[shape.lower, shape.upper].flatMap((reading) =>
          reading.supplemental ? sourceEvidence(reading.supplemental.source) : [])].map((entry) => [entry.id, entry])).values()];
        field(target, "Shared elevation datum", shape.datum, (value) => {
          shape.datum = value;
          for (const reading of [shape.lower, shape.upper]) if (reading.supplemental) reading.supplemental.reviewed = false;
          env.render();
        }, [["", "Choose the same datum for both elevations"], ...datumEvidence.map((entry) => [entry.id, entry.text || "Datum source region"])]);
      }
      if (shape.kind === "unsupported") field(target, "Unresolved geometry reason", shape.reason, (value) => { shape.reason = value; });
      if (["vertical", "slope", "unsupported"].includes(shape.kind)) {
        const support = details(target, "Source evidence showing this physical portion");
        evidenceChoices(support, draft.sourceEvidence, shape.supportIds);
      }
    }
    function beginCorrection(segment) {
      stopPicking(); splitting = null; supplementalCapture = null;
      const context = Object.values(env.getView().duct_takeoff.current_sources || {}).find((entry) =>
        entry.geometry.revision_id === segment.observation.source.revision_id && entry.geometry.index === segment.observation.source.index);
      const sourceEvidence = context ? list(context.evidence) : list(segment.evidence);
      editing = { snapshot: controller.snapshot(segment), segment, observation: clone(segment.observation), mode: "correct",
        sourceEvidence,
        state: segment.state || "unresolved", role: segment.role || "unknown", evidence_ids: clone(segment.evidence_ids || segment.observation.evidence_ids),
        sizeText: list(segment.observation.group.size?.dimensions).join(" × "),
        shape: segment.observation.group.size?.shape || "", sizeUnit: segment.observation.group.size?.unit || "in", reason: "",
        groupEvidenceIds: clone(segment.observation.group.evidence_ids), sizeChanged: false,
        geometry: geometryDraft(segment.observation), capture: { kind: "graphic", text: "", bbox: null, active: false } };
      const readingTarget = list(env.getView().duct_takeoff.reading_targets).find((entry) => entry.id === segment.id && entry.kind === "observation");
      if (readingTarget) editing.readingSnapshot = controller.readingSnapshot(readingTarget);
      editing.sourceEvidence = editing.sourceEvidence.filter((entry) => sameSource(entry.source, editing.observation.source));
      editing.evidence_ids = editing.evidence_ids.filter((id) => editing.sourceEvidence.some((entry) => entry.id === id));
      initializeSupplemental(editing, segment.id);
      pathPicking = false; show(segment); env.render();
    }
    function beginParent(target, editReadings = false, editGraphics = false) {
      stopPicking(); splitting = null; supplementalCapture = null;
      editing = { mode: editGraphics ? "parent-graphic" : editReadings ? "parent-edit" : "parent", snapshot: controller.readingSnapshot(target), readingSnapshot: controller.readingSnapshot(target),
        observation: clone(target.observation), geometry: geometryDraft(target.observation), sourceEvidence: sourceEvidence(target.source),
        target, reason: "", addGraphicIds: [], removeGraphicIds: [] };
      initializeSupplemental(editing, target.id); show(target); env.render();
    }
    function retainedGraphicEvidence() {
      const data = env.getView().duct_takeoff;
      return [...allSourceEvidence(true), ...list(data.generations).flatMap((generation) =>
        Object.values(generation.current_sources || {}).flatMap((source) => list(source.evidence)))];
    }
    function parentGraphicOptions(draft) {
      const wrappers = new Map(list(env.getView().duct_takeoff.available_evidence).map((entry) => [entry.id, entry]));
      const added = sourceEvidence(draft.observation.source).filter((entry) => {
        const captured = wrappers.get(entry.id);
        return entry.text === null && !draft.observation.evidence_ids.includes(entry.id) &&
          (!captured || (captured.validity === "current" && captured.kind === "graphic" && captured.evidence.text === null && sameSource(captured.evidence.source, draft.observation.source)));
      });
      const removed = [...new Map(retainedGraphicEvidence().filter((entry) => draft.observation.evidence_ids.includes(entry.id) &&
        entry.text === null && sameSource(entry.source, draft.observation.source)).map((entry) => [entry.id, entry])).values()];
      return { added, removed };
    }
    function parentGraphicFields(target, draft) {
      const panel = details(target, "Correct retained parent graphic evidence", true);
      try { controller.current(draft.snapshot); } catch (error) {
        panel.append(node("p", error.message, "error"), button("Close parent graphic correction", () => { stopPicking(); editing = null; env.render(); })); return;
      }
      panel.append(node("p", "Select graphic regions on the parent's original drawing. Partial or withdrawn support can be saved; the affected length remains unknown until the saved route has sufficient current support. Whole-run dimension conflicts remain visible.", "section-hint"),
        button("Show retained parent route", () => show(draft.target)));
      const choices = parentGraphicOptions(draft);
      const additions = details(panel, "Current graphic regions to add", true);
      for (const [index, evidence] of choices.added.entries()) {
        checkbox(additions, "Add graphic region " + (index + 1), draft.addGraphicIds.includes(evidence.id), (checked) => {
          draft.addGraphicIds = checked ? [...draft.addGraphicIds, evidence.id] : draft.addGraphicIds.filter((id) => id !== evidence.id); env.render();
        });
        additions.append(button("Show addition region " + (index + 1), () => env.showSource({ ...evidence.source, bbox: evidence.bbox })));
      }
      if (!choices.added.length) additions.append(node("p", "Capture a current graphic region on this drawing to add support.", "section-hint"));
      const removals = details(panel, "Cited graphic regions to remove", true);
      for (const [index, evidence] of choices.removed.entries()) {
        checkbox(removals, "Remove cited graphic region " + (index + 1), draft.removeGraphicIds.includes(evidence.id), (checked) => {
          draft.removeGraphicIds = checked ? [...draft.removeGraphicIds, evidence.id] : draft.removeGraphicIds.filter((id) => id !== evidence.id); env.render();
        });
        removals.append(button("Show cited region " + (index + 1), () => env.showSource({ ...evidence.source, bbox: evidence.bbox })));
      }
      for (const { title, reading } of consumedSlots(draft)) panel.append(node("p", "Retained " + title.toLowerCase() + ": " + reading.value + " " + reading.unit, "section-hint"));
      panel.append(button("Capture parent graphic region", () => beginSupplementalCapture(draft, draft.observation.source, "graphic", true)));
      if (supplementalCapture?.owner === draft) captureFields(panel, supplementalCapture);
      supplementalFields(panel, draft);
      field(panel, "Graphic correction reason", draft.reason, (value) => { draft.reason = value; });
      panel.append(button("Save parent graphic correction", async () => {
        const currentChoices = parentGraphicOptions(draft);
        if (draft.addGraphicIds.some((id) => !currentChoices.added.some((entry) => entry.id === id)) ||
            draft.removeGraphicIds.some((id) => !currentChoices.removed.some((entry) => entry.id === id)))
          throw new Error("The selected graphic evidence changed. Review the current source regions again.");
        if (await controller.correctParentGraphics(draft.snapshot, draft.addGraphicIds, draft.removeGraphicIds, reviewedSupplemental(draft), draft.reason)) {
          stopPicking(); editing = null; env.render();
        }
      }), button("Close parent graphic correction", () => { stopPicking(); editing = null; env.render(); }));
    }
    function beginAdd(page) {
      const geometry = env.getView().sheet_geometries?.[page.revision_id + ":" + page.index];
      if (!geometry?.sheet_id || !geometry.fingerprint) throw new Error("Refresh the accepted drawing coordinates before adding a portion.");
      stopPicking();
      const source = { revision_id: page.revision_id, index: page.index, sheet_id: geometry.sheet_id, geometry_fingerprint: geometry.fingerprint };
      const observation = { schema: "duct-observation-1", source, group: { scope: "ductwork", system: null, material: null,
        work_status: null, size: null, evidence_ids: [] }, geometry: { kind: "planar", points: [], scale_fact_id: "", dimension_check_ids: [] },
        readings: [], evidence_ids: [], issues: [] };
      editing = { mode: "add", snapshot: controller.snapshot(), segment: null, observation,
        sourceEvidence: sourceEvidence(source), state: "included", role: "duct", evidence_ids: [], groupEvidenceIds: [],
        shape: "", sizeText: "", sizeUnit: "in", sizeChanged: false, reason: "", geometry: geometryDraft(observation),
        capture: { kind: "graphic", text: "", bbox: null, active: false } };
      pathPicking = false; splitting = null; env.showSource({ ...source, bbox: [0, 0, 1, 1] }); env.render();
    }
    function beginFamily(memberId) {
      stopPicking(); editing = null; splitting = null; supplementalCapture = null;
      const snapshot = controller.familySnapshot(memberId), family = controller.currentFamily(snapshot);
      const draft = familyTools.create(family, snapshot);
      draft.familyEdit = true;
      draft.geometry = geometryDraft(draft.observation);
      draft.sourceEvidence = sourceEvidence(family.source);
      // The root no longer consumes readings. Foreign reading/marker evidence stays
      // in immutable check origins and explicit dispositions, never root graphics.
      const localEvidenceIds = new Set(draft.sourceEvidence.map(entry => entry.id));
      draft.observation.evidence_ids = draft.observation.evidence_ids.filter(id => localEvidenceIds.has(id));
      draft.children = family.leaves.map(leaf => childDraft(leaf.group));
      if (draft.children.length !== draft.cuts.length + 1) draft.children = Array.from({ length: draft.cuts.length + 1 }, () => childDraft(family.root.group));
      draft.groupReviews = [];
      draft.capture = { kind: "graphic", text: "", bbox: null, active: false };
      draft.groupEvidenceIds = [];
      for (const decision of draft.decisions) {
        const check = family.checks.find(entry => entry.id === decision.id);
        const checkDraft = familyCheckDraft(draft, decision, check);
        initializeSupplemental(checkDraft, check.id);
      }
      familyEditing = draft; show({ observation: family.root }); env.render();
    }
    function familyCheckDraft(draft, decision, check) {
      const geometry = { kind: "circular_arc", points: draft.observation.geometry.points, scale: draft.geometry.scale,
        dimensionChecks: check.origin.role === "dimension_check_ids" ? [decision.reading] : [],
        radiusChecks: check.origin.role === "radius_check_ids" ? [decision.reading] : [] };
      draft.checkDrafts ||= new Map();
      const result = draft.checkDrafts.get(check.id) || {};
      Object.assign(result, { mode: "family-check", ownerFamily: draft, decision, target: { id: check.id },
        snapshot: draft.snapshot, observation: draft.observation, geometry, sourceEvidence: draft.sourceEvidence });
      draft.checkDrafts.set(check.id, result); return result;
    }
    function familyBoundarySignature(draft, index) {
      return JSON.stringify([draft.observation.geometry, draft.cuts[index]]);
    }
    function familyGroupSignature(draft, index) {
      return JSON.stringify([draft.observation.geometry, draft.cuts, childGroups(draft)[index]]);
    }
    function familyContext(draft) {
      const view = env.getView();
      return { selection: env.getSelection(), page: pageGeometry(draft.observation.source),
        sources: view.duct_takeoff.current_sources, evidence: view.duct_takeoff.available_evidence,
        boundaryReviews: draft.boundaryReviews, state: view.duct_takeoff.state_fingerprint, relationships: view.duct_takeoff.reading_relationships };
    }
    function familySignature(draft) { draft.observation.geometry.scale_fact_id = draft.geometry.scale; return familyTools.signature(draft, childGroups(draft), familyContext(draft)); }
    function familyPayload(draft) {
      controller.currentFamily(draft.snapshot);
      if (draft.cuts.some((_, index) => draft.boundaryReviews?.[index] !== familyBoundarySignature(draft, index)))
        throw new Error("Review every boundary on the corrected root with its source region.");
      if (draft.children.some((_, index) => draft.groupReviews[index] !== familyGroupSignature(draft, index)))
        throw new Error("Review every resulting portion against its current geometry and labels.");
      for (const decision of draft.decisions) {
        const check = draft.family.checks.find(entry => entry.id === decision.id);
        decision.relationships = decision.state === "active" ? reviewedSupplemental(familyCheckDraft(draft, decision, check)) : [];
      }
      return familyTools.payload(draft, childGroups(draft));
    }
    function familyFields(target, data) {
      const history = list(data.arc_repartition_events);
      if (history.length) {
        const section = details(target, "Saved arc family correction history · " + history.length);
        section.append(node("p", "Correction history and original checks remain in the saved project export. Quantities shown above are server-calculated saved results."));
        for (const event of history) {
          const entry = details(section, [event.actor, event.at, event.reason].filter(Boolean).join(" · ") || "Saved family correction");
          entry.append(node("p", "Retained before/after family evidence · " + (event.id || "")));
          for (const [key, title] of [["before", "Before correction"], ["after", "After correction"]]) {
            const snapshot = event[key], root = snapshot?.root || snapshot?.family?.root;
            if (root?.source && root.geometry) entry.append(button("Show " + title.toLowerCase(), () => show({ observation: root })));
            if (snapshot?.checks) entry.append(node("p", title + " · " + snapshot.checks.length + " retained checks"));
          }
        }
      }
      if (!familyEditing) return;
      const draft = familyEditing, section = details(target, "Correct saved arc family", true);
      try { controller.currentFamily(draft.snapshot); } catch (error) {
        stopPicking(); section.append(node("p", error.message + " The unsaved proposal is retained until you close it.", "error"),
          button("Close stale family proposal", () => { familyEditing = null; env.render(); })); return;
      }
      draft.sourceEvidence = sourceEvidence(draft.observation.source, draft.sourceEvidence);
      section.append(node("p", "Page " + (draft.family.source.index + 1) + " · " + draft.family.leaves.length + " saved portions · " + draft.family.parents.length + " retained parents · " + draft.decisions.length + " retained checks. Review all portions and every check before saving."),
        node("p", "Draft paths and radial markers are previews. The server validates exact geometry and computes quantities after saving. Unresolved checks block dependent quantities.", "section-hint"));
      const saved = details(section, "Complete saved family");
      saved.append(button("Show saved root", () => show({ observation: draft.family.root })));
      for (const [index, cut] of draft.family.cuts.entries()) saved.append(button("Show saved boundary " + (index + 1), () =>
        env.showSource(draft.family.source, { geometry: clone(draft.family.root.geometry), page_geometry: pageGeometry(draft.family.source), arc_marker: clone(cut.source_point) })));
      for (const [index, observation] of [...draft.family.parents, ...draft.family.leaves].entries())
        saved.append(button("Show saved family member " + (index + 1) + " · " + groupLabel(observation.group), () => show({ observation })));
      section.append(button("Show proposed root", () => show({ observation: draft.observation })),
        button("Edit family circular arc anchors", () => { draft.familyPick = "root"; startPath(draft, "circular_arc"); }),
        button("Add family size boundary", () => { draft.familyPick = "cut"; draft.selectionIndex = null; startPath(draft, "arc_split"); }, draft.cuts.length >= 127));
      if (pathRequest && pathDraftTarget === draft) for (const [action, title] of [["undo", "Undo family path point"], ["finish", "Finish family path"], ["cancel", "Cancel family path edit"]])
        section.append(button(title, () => env.pathCommand({ request_id: pathRequest.request_id, source: pathRequest.source, action })));
      section.append(button("Undo family geometry change", () => {
        stopPicking(); familyTools.undo(draft); draft.geometry = geometryDraft(draft.observation); env.render();
      }, !draft.undo.length));
      scaleField(section, draft);
      // The scale control is part of the source-bound proposal, without changing saved anchors.
      draft.observation.geometry.scale_fact_id = draft.geometry.scale;
      captureFields(section, draft);
      evidenceChoices(details(section, "Correction source evidence", true), draft.sourceEvidence, draft.evidence_ids);
      evidenceChoices(details(section, "Corrected root graphic evidence"), draft.sourceEvidence.filter(entry => entry.kind === "graphic"), draft.observation.evidence_ids);
      for (const [index, cut] of draft.cuts.entries()) {
        const entry = details(section, "Family size boundary " + (index + 1), true);
        entry.append(button("Show family boundary " + (index + 1), () => showArcBoundary(draft, cut)),
          button("Move family boundary " + (index + 1), () => { draft.familyPick = "cut"; draft.selectionIndex = index; startPath(draft, "arc_split"); }),
          button("Remove family boundary " + (index + 1), () => {
            stopPicking(); familyTools.checkpoint(draft); draft.cuts.splice(index, 1); draft.children.splice(index + 1, 1);
            familyTools.invalidate(draft, true); env.render();
          }));
        evidenceChoices(entry, draft.sourceEvidence.filter(evidence => ["graphic", "label"].includes(evidence.kind)), cut.evidence_ids);
        checkbox(entry, "Reviewed family boundary " + (index + 1) + " on the corrected root", draft.boundaryReviews?.[index] === familyBoundarySignature(draft, index), checked => {
          draft.boundaryReviews ||= []; draft.boundaryReviews[index] = checked ? familyBoundarySignature(draft, index) : null;
          familyTools.invalidate(draft); env.render();
        });
      }
      if (!draft.cuts.length) section.append(node("p", "No size boundaries: the corrected root will be saved as one circular arc."));
      childFields(section, draft);
      for (const [index] of draft.children.entries()) checkbox(section, "Reviewed resulting portion " + (index + 1) + " geometry, grouping and source labels",
        draft.groupReviews[index] === familyGroupSignature(draft, index), checked => {
          draft.groupReviews[index] = checked ? familyGroupSignature(draft, index) : null; familyTools.invalidate(draft); env.render();
        });
      const checks = details(section, "Every retained dimension and radius check", true);
      for (const [index, decision] of draft.decisions.entries()) {
        const check = draft.family.checks[index], origin = check.origin;
        const originalReading = origin.observation.readings.find(reading => reading.id === origin.reading_id);
        const entry = details(checks, "Retained check " + (index + 1) + " · " + (origin.role === "radius_check_ids" ? "Radius" : "Whole interval length") + " · saved " + label(check.state), true);
        entry.append(node("p", "Original " + originalReading.value + " " + originalReading.unit + " · " + origin.target_id),
          button("Show original check interval " + (index + 1), () => show({ observation: origin.observation })));
        if (check.observation) entry.append(button("Show current check interval " + (index + 1), () => show({ observation: check.observation })));
        if (check.retained_observation) entry.append(button("Show last reviewed check interval " + (index + 1), () => show({ observation: check.retained_observation })));
        field(entry, "Check " + (index + 1) + " disposition", decision.state, value => {
          decision.state = value; decision.scope = null; decision.relationships = []; familyTools.invalidate(draft); env.render();
        }, [["", "Choose an explicit decision"], ["active", "Use a reviewed check"], ["unresolved", "Keep unresolved · block quantities"], ["not_applicable", "Source proves not applicable"]]);
        if (decision.state === "active") {
          const scopeValue = decision.scope?.kind === "portion" ? "portion:" + decision.scope.index : decision.scope?.kind || "";
          field(entry, "Check " + (index + 1) + " scope", scopeValue, value => {
            decision.scope = value.startsWith("portion:") ? { kind: "portion", index: Number(value.split(":")[1]) } :
              value === "interval" ? { kind: "interval", ...clone(decision.interval) } : value ? { kind: value } : null;
            if (decision.reading.supplemental) decision.reading.supplemental.reviewed = false;
            familyTools.invalidate(draft); env.render();
          }, [["", "Choose physical check scope"], ["retained", "Keep last reviewed physical interval · unchanged circle required"], ["root", "Corrected whole root"],
            ...draft.children.map((_, i) => ["portion:" + i, "Resulting portion " + (i + 1)]), ["interval", "Independent interval across size boundaries"]]);
          if (decision.scope?.kind === "interval") for (const [key, title] of [["start_point", "start"], ["end_point", "end"]]) {
            entry.append(button("Pick check " + (index + 1) + " interval " + title, () => {
              draft.familyPick = "interval"; draft.intervalDecision = index; draft.intervalEndpoint = key; startPath(draft, "arc_split");
            }));
            for (const [rootIndex, rootTitle] of [[0, "root start"], [2, "root end"]]) entry.append(button("Use " + rootTitle + " for check " + (index + 1) + " interval " + title, () => {
              stopPicking(); decision.interval[key] = clone(draft.observation.geometry.points[rootIndex]); decision.scope[key] = clone(decision.interval[key]);
              familyTools.invalidate(draft); env.render();
            }));
            entry.append(node("p", title + " source point: " + (decision.scope[key].length ? decision.scope[key].join(", ") : "Choose on drawing")));
          }
          readingFields(entry, "Check " + (index + 1) + " reading", decision.reading, draft.sourceEvidence);
          supplementalFields(entry, familyCheckDraft(draft, decision, check));
        }
        evidenceChoices(details(entry, "Check " + (index + 1) + " decision evidence"), draft.sourceEvidence, decision.evidence_ids);
        field(entry, "Check " + (index + 1) + " reason", decision.reason, value => { decision.reason = value; });
      }
      field(section, "Family correction reason", draft.reason, value => { draft.reason = value; });
      const review = details(section, "Review complete family proposal", true);
      review.append(node("p", draft.family.cuts.length + " saved boundaries → " + draft.cuts.length + " proposed boundaries · " + draft.children.length + " resulting portions · " + draft.decisions.filter(value => value.state === "unresolved").length + " explicitly unresolved checks."));
      checkbox(review, "Reviewed the complete family correction against its drawing and evidence", draft.acknowledgment === familySignature(draft), checked => {
        draft.acknowledgment = null;
        if (checked) { try { familyPayload(draft); draft.acknowledgment = familySignature(draft); } catch (error) { env.error(error.message); } }
        env.render();
      });
      review.append(button("Save family correction", async () => {
        if (!draft.acknowledgment || draft.acknowledgment !== familySignature(draft)) throw new Error("Review the complete current proposal again before saving.");
        const payload = familyPayload(draft);
        if (await controller.repartition(draft.snapshot, payload, draft.reason)) { stopPicking(); familyEditing = null; env.render(); }
      }), button("Cancel family correction", () => { stopPicking(); familyEditing = null; env.render(); }));
    }

    function beginSplit(segment) {
      const observation = clone(segment.observation);
      if (!(isArc(observation.geometry) || (observation.geometry.kind === "planar" && observation.geometry.points.length >= 2)))
        throw new Error("Split a supported planar centerline. Correct the geometry first if the route needs repair.");
      stopPicking();
      splitting = { snapshot: controller.snapshot(segment), segment, observation, geometry: geometryDraft(observation),
        sourceEvidence: sourceEvidence(observation.source, segment.evidence), evidence_ids: clone(observation.evidence_ids),
        groupEvidenceIds: clone(observation.group.evidence_ids), capture: { kind: "label", text: "", bbox: null, active: false },
        cuts: [], children: [], selectionIndex: null, reason: "" };
      editing = null; pathPicking = false; show(segment); env.render();
    }
    function childDraft(group) {
      return { group: clone(group), shape: group.size?.shape || "", dimensions: list(group.size?.dimensions).join(" × "),
        unit: group.size?.unit || "in", sizeChanged: false };
    }
    function splitPreview(points, cuts) {
      const paths = []; let current = [points[0]];
      for (let index = 0; index < points.length - 1; index++) {
        const from = points[index], to = points[index + 1];
        for (const cut of cuts.filter((entry) => entry.edge_index === index)) {
          const fraction = Number(cut.fraction), point = fraction === 1 ? to : from.map((value, axis) => value + (to[axis] - value) * fraction);
          current.push(point); paths.push(current); current = [point];
        }
        if (current[current.length - 1] !== to) current.push(to);
      }
      paths.push(current); return paths;
    }
    function showArcBoundary(draft, cut) {
      env.showSource(draft.observation.source, { geometry: clone(draft.observation.geometry),
        page_geometry: pageGeometry(draft.observation.source), arc_marker: clone(cut.source_point) });
    }
    function showChild(draft, index, previews) {
      const geometry = draft.observation.geometry;
      env.showSource(draft.observation.source, isArc(geometry) ? { geometry: clone(geometry),
        page_geometry: pageGeometry(draft.observation.source), arc_preview: { cuts: draft.cuts.map(cut => clone(cut.source_point)), portion: index } } : { points: previews[index] });
    }
    function childGroups(draft) {
      return draft.children.map(child => {
        const group = clone(child.group);
        if (child.sizeChanged) group.size = child.shape ? { shape: child.shape, dimensions: child.dimensions.split(/\s*[x×,]\s*/i).map(value => value.trim()),
          unit: child.unit, original_text: child.dimensions.trim() } : null;
        return group;
      });
    }
    function childFields(target, draft, previews = null) {
      for (const [index, child] of draft.children.entries()) {
        const name = "Child " + (index + 1), entry = details(target, name + " · " + groupLabel(child.group), true);
        field(entry, name + " shape", child.shape, (value) => { child.shape = value; child.sizeChanged = true; },
          [["", "Unknown size"], ["rectangular", "Rectangular"], ["round", "Round"], ["oval", "Oval"]]);
        field(entry, name + " dimensions", child.dimensions, (value) => { child.dimensions = value; child.sizeChanged = true; });
        field(entry, name + " dimension unit", child.unit, (value) => { child.unit = value; child.sizeChanged = true; }, [["in", "Inches"], ["ft", "Feet"], ["mm", "Millimeters"], ["m", "Meters"]]);
        field(entry, name + " system", child.group.system, (value) => { child.group.system = value.trim() || null; });
        field(entry, name + " material", child.group.material, (value) => { child.group.material = value.trim() || null; });
        field(entry, name + " work status", child.group.work_status || "", (value) => { child.group.work_status = value || null; }, [["", "Unknown"], ...Object.entries(workNames)]);
        const labels = details(entry, "Labels for " + name.toLowerCase()); evidenceChoices(labels, draft.sourceEvidence, child.group.evidence_ids);
        entry.append(button("Show " + name.toLowerCase() + " path", () => showChild(draft, index, previews)));
      }
    }
    function splitFields(target) {
      if (!splitting) return;
      const draft = splitting;
      draft.sourceEvidence = sourceEvidence(draft.observation.source, draft.sourceEvidence);
      const section = details(target, "Split the duct portion at supported size changes", true);
      try { controller.current(draft.snapshot); } catch (error) {
        section.append(node("p", error.message, "error"), button("Close split review", () => { stopPicking(); splitting = null; env.render(); })); return;
      }
      section.append(node("p", "Choose each size-change point on the saved centerline, then review the child sizes and their labels. Splitting preserves the route and any unresolved source issues.", "section-hint"));
      if (isArc(draft.observation.geometry)) section.append(node("p", "The boundary marker is an approximate radial projection. Each cited region must contain the actual circular boundary. The saved parent retains whole-arc and radius checks; inherited checks are not copied into children.", "section-hint"));
      function selectCut(index) {
        draft.selectionIndex = index;
        startPath(draft, isArc(draft.observation.geometry) ? "arc_split" : "split");
      }
      section.append(button(draft.cuts.length ? "Add another size-change point" : "Select size-change point", () => selectCut(null)));
      captureFields(section, draft);
      const previews = isArc(draft.observation.geometry) ? null : splitPreview(draft.geometry.points, draft.cuts);
      for (const [index, cut] of draft.cuts.entries()) {
        const entry = details(section, "Size change " + (index + 1), true);
        entry.append(button("Move size change " + (index + 1), () => selectCut(index)));
        if (isArc(draft.observation.geometry)) entry.append(button("Show size change " + (index + 1), () => showArcBoundary(draft, cut)));
        const boundaries = draft.sourceEvidence.filter((entry) => ["graphic", "label"].includes(entry.kind));
        if (!boundaries.length) entry.append(node("p", "Capture a graphic or label region containing this size-change point.", "section-hint"));
        for (const evidence of boundaries) {
          checkbox(entry, "Size-change evidence · " + (evidence.text || "Graphic source region"), cut.evidence_ids.includes(evidence.id), (checked) => {
            cut.evidence_ids = checked ? [...new Set([...cut.evidence_ids, evidence.id])] : cut.evidence_ids.filter((id) => id !== evidence.id);
          });
          sources(entry, [evidence]);
        }
      }
      childFields(section, draft, previews);
      field(section, "Split reason", draft.reason, (value) => { draft.reason = value; });
      section.append(button("Save size-change split", async () => {
        if (!draft.reason.trim() || !draft.cuts.length || draft.cuts.some((cut) => !cut.evidence_ids.length))
          throw new Error("Choose a size-change point, its source support, and explain the split.");
        const groups = draft.children.map((child) => {
          const group = clone(child.group);
          if (child.sizeChanged) group.size = child.shape ? { shape: child.shape, dimensions: child.dimensions.split(/\s*[x×,]\s*/i).map((value) => value.trim()),
            unit: child.unit, original_text: child.dimensions.trim() } : null;
          return group;
        });
        if (await controller.split(draft.snapshot, draft.cuts, groups, draft.reason)) { stopPicking(); splitting = null; env.render(); }
      }, !draft.cuts.length));
      section.append(button("Cancel size-change split", () => { stopPicking(); splitting = null; env.render(); }));
    }
    function captureFields(target, draft) {
      const capture = draft.capture;
      const panel = details(target, draft.graphicCapture ? "Capture parent graphic evidence" : draft.readingCapture ? "Capture supplemental source evidence" : "Capture another source region", capture.active || !!capture.bbox);
      if (!draft.graphicCapture) field(panel, "Source evidence kind", capture.kind, (value) => { capture.kind = value; env.render(); },
        [["graphic", "Graphic support"], ["label", "Size / system label"], ["dimension", "Dimension"], ["elevation", "Elevation"], ["datum", "Datum"]]);
      if (capture.kind !== "graphic") {
        panel.append(node("p", "This text is your transcription of the selected source region.", "section-hint"));
        field(panel, "Source transcription", capture.text, (value) => { capture.text = value; });
      }
      panel.append(button(capture.active ? "Cancel source selection" : "Select source region", () => {
        controller.current(draft.snapshot);
        invalidateGraphicReview(draft.owner);
        const active = !capture.active; stopPicking(); capture.active = active;
        if (capture.active) {
          pathRequest = null; env.showSource({ ...draft.observation.source, bbox: [0, 0, 1, 1] });
        }
        env.pickRegion(capture.active); env.render();
      }));
      if (capture.bbox) panel.append(button("Show selected source region", () => env.showSource({ ...draft.observation.source, bbox: capture.bbox })));
      panel.append(button("Save source evidence", async () => {
        if (!capture.bbox) throw new Error("Select the source region on the drawing first.");
        if (capture.kind !== "graphic" && !capture.text.trim()) throw new Error("Transcribe the source text for this reading.");
        const result = await controller.capture(draft.snapshot, { source: { revision_id: draft.observation.source.revision_id,
          index: draft.observation.source.index }, bbox: capture.bbox, text: capture.kind === "graphic" ? null : capture.text.trim(), kind: capture.kind },
          "Captured " + capture.kind + " source evidence for a duct correction");
        if (!result) return;
        if (!sameSource(result.evidence.source, draft.observation.source)) throw new Error("The drawing source changed. Reopen the correction before using the captured evidence.");
        draft.sourceEvidence = sourceEvidence(draft.observation.source, [...draft.sourceEvidence, { ...result.evidence, kind: result.kind }]);
        if (!draft.readingCapture) {
          draft.evidence_ids = [...new Set([...draft.evidence_ids, result.id])];
          if (result.kind === "graphic") draft.geometry.supportIds = [...new Set([...draft.geometry.supportIds, result.id])];
          if (result.kind === "label") draft.groupEvidenceIds = [...new Set([...draft.groupEvidenceIds, result.id])];
        }
        invalidateGraphicReview(draft.owner);
        capture.active = false; capture.bbox = null; capture.text = ""; env.pickRegion(false); env.render();
      }, !capture.bbox));
    }
    function correction(target, data) {
      if (!editing) return;
      const draft = editing;
      draft.sourceEvidence = sourceEvidence(draft.observation.source, draft.sourceEvidence);
      if (draft.mode === "parent-graphic") { parentGraphicFields(target, draft); return; }
      if (draft.mode === "parent" || draft.mode === "parent-edit") {
        const repairing = draft.mode === "parent-edit";
        const panel = details(target, repairing ? "Correct retained parent dimension readings" : "Review retained parent reading applicability", true);
        try { controller.current(draft.snapshot); } catch (error) {
          panel.append(node("p", error.message, "error"), button("Close parent review", () => { stopPicking(); editing = null; env.render(); })); return;
        }
        panel.append(node("p", repairing ? "Correct the written whole-run dimensions from their sources. Every existing check remains attached to this parent; the saved route and split portions remain visible for comparison." :
          "This whole-run check remains attached to the retained parent. Review its current route and original readings before linking the supplemental source again.", "section-hint"),
          button("Show retained parent route", () => show(draft.target)));
        for (const { title, reading } of consumedSlots(draft)) {
          if (repairing) {
            const original = draft.observation.readings.find((entry) => entry.id === reading.id);
            panel.append(node("p", "Saved " + title.toLowerCase() + ": " + original.value + " " + original.unit, "section-hint"));
            readingFields(panel, title, reading, draft.sourceEvidence);
          }
          else panel.append(node("p", title + ": " + reading.value + " " + reading.unit));
        }
        if (repairing) {
          sources(panel, draft.sourceEvidence);
          panel.append(button("Capture parent dimension evidence", () => beginSupplementalCapture(draft, draft.observation.source, "dimension")));
        }
        supplementalFields(panel, draft);
        field(panel, repairing ? "Reading correction reason" : "Reading relationship reason", draft.reason, (value) => { draft.reason = value; });
        panel.append(button(repairing ? "Save parent reading correction" : "Save retained parent reading review", async () => {
          const relationships = reviewedSupplemental(draft);
          if (!repairing && !relationships.length) throw new Error("Choose a supplemental reading relationship to review.");
          const saved = repairing ? await controller.correctParentReadings(draft.snapshot, parentReadingProposal(draft).updates, relationships, draft.reason) :
            await controller.reviewReadings(draft.snapshot, draft.observation, relationships, draft.reason);
          if (saved) { stopPicking(); editing = null; env.render(); }
        }), button("Close parent review", () => { stopPicking(); editing = null; env.render(); })); return;
      }
      const panel = details(target, draft.mode === "add" ? "Add the missed duct portion" : "Correct the selected duct portion", true);
      let stale = false;
      try { controller.current(draft.snapshot); } catch (e) { stale = true; panel.append(node("p", e.message, "error")); }
      if (stale) {
        const fresh = list(data.segments).find((entry) => entry.id === draft.segment?.id);
        if (fresh) panel.append(button("Reopen current portion", () => beginCorrection(fresh)));
        panel.append(button("Close correction", () => { stopPicking(); editing = null; env.render(); })); return;
      }
      panel.append(node("p", "Correct the source reading and keep any unresolved portions visible. Quantities are recalculated from the saved evidence.", "section-hint"));
      const group = draft.observation.group;
      field(panel, "System / service", group.system, (v) => { group.system = v.trim() || null; });
      field(panel, "Material", group.material, (v) => { group.material = v.trim() || null; });
      field(panel, "Work status", group.work_status || "", (v) => { group.work_status = v || null; }, [["", "Unknown"], ...Object.entries(workNames)]);
      field(panel, "Duct shape", draft.shape, (v) => { draft.shape = v; draft.sizeChanged = true; }, [["", "Unknown size"], ["rectangular", "Rectangular"], ["round", "Round"], ["oval", "Oval"]]);
      field(panel, "Dimensions as written, separated by ×", draft.sizeText, (v) => { draft.sizeText = v; draft.sizeChanged = true; });
      field(panel, "Dimension unit", draft.sizeUnit, (v) => { draft.sizeUnit = v; draft.sizeChanged = true; }, [["in", "Inches"], ["ft", "Feet"], ["mm", "Millimeters"], ["m", "Meters"]]);
      if (draft.mode === "correct") {
        field(panel, "Treatment", draft.state, (v) => { draft.state = v; }, [["included", "Include duct portion"], ["unresolved", "Keep unresolved"], ["excluded", "Exclude confirmed non-duct linework"]]);
        field(panel, "Drawing role", draft.role, (v) => { draft.role = v; }, ["duct", "wall", "leader", "dimension", "legend", "grid", "unrelated_service", "unknown"].map((v) => [v, label(v)]));
      }
      const evidence = details(panel, "Source regions supporting this correction");
      evidenceChoices(evidence, draft.sourceEvidence, draft.evidence_ids);
      const labels = details(panel, "Labels supporting size, system, material and work status");
      evidenceChoices(labels, draft.sourceEvidence, draft.groupEvidenceIds);
      captureFields(panel, draft);
      geometryFields(panel, draft);
      supplementalFields(panel, draft);
      for (const issue of list(draft.segment?.observation.issues)) checkbox(panel, "Resolved from cited evidence: " + issue,
        !draft.observation.issues.includes(issue), (checked) => {
          draft.observation.issues = checked ? draft.observation.issues.filter((v) => v !== issue) : [...new Set([...draft.observation.issues, issue])];
        });
      if (draft.geometry.kind === "planar" && draft.mode === "correct") panel.append(button(pathPicking ? "Cancel centerline correction" : "Replace with a straight centerline", () => {
        pathPicking = !pathPicking; show(draft.segment); env.pickLine(pathPicking ? "duct-correction" : false); env.render();
      }));
      if (pathPicking) panel.append(node("p", "Drag between the two endpoints on this portion’s drawing. Save the correction after checking the highlighted path.", "section-hint"));
      panel.append(button("Show current correction path", () => env.showSource({ ...draft.observation.source, bbox: [0, 0, 1, 1] },
        isArc(draft.geometry) ? { geometry: draft.geometry.kind === "circular_arc_span" ? clone(draft.observation.geometry) : { kind: "circular_arc", points: clone(draft.geometry.points) },
          page_geometry: pageGeometry(draft.observation.source) } :
          draft.geometry.points.length > 1 ? { points: draft.geometry.points } : {})));
      field(panel, "Correction reason", draft.reason, (v) => { draft.reason = v; });
      panel.append(button(draft.mode === "add" ? "Save missed duct portion" : "Save duct correction", async () => {
        if (!draft.reason.trim()) throw new Error("Explain the correction using the cited drawing evidence.");
        const replacement = composeObservation(draft);
        const relationships = reviewedSupplemental(draft);
        if (relationships.length && (draft.state !== (draft.segment.state || "unresolved") || draft.role !== (draft.segment.role || "unknown")))
          throw new Error("Save treatment or drawing-role changes separately before reviewing supplemental readings.");
        const saved = relationships.length ? await controller.reviewReadings(draft.readingSnapshot || draft.snapshot, replacement, relationships, draft.reason) :
          draft.mode === "add" ? await controller.add(draft.snapshot, replacement, draft.reason) :
          await controller.correct(draft.snapshot, { observation: replacement, state: draft.state, role: draft.role, evidence_ids: draft.evidence_ids }, draft.reason);
        if (saved) {
          stopPicking(); editing = null; env.render();
        }
      }));
      panel.append(button("Cancel correction", () => { stopPicking(); editing = null; env.render(); }));
    }
    function emptyRefreshGroup() {
      return { incoming_ids: [], current_ids: [], disposition: "", evidence_ids: [], reason: "", cuts: [], refreshReplacement: true };
    }
    function readingHistory(target, data) {
      const relationships = list(data.reading_relationships), targets = list(data.reading_targets);
      if (!relationships.length && !targets.some((entry) => entry.kind === "split_parent") && !list(data.reading_relationship_events).length &&
          !list(data.parent_reading_events).length && !list(data.parent_graphic_events).length) return;
      const panel = details(target, "Supplemental reading relationships", true);
      for (const parent of targets.filter((entry) => entry.kind === "split_parent")) {
        const entry = details(panel, "Retained whole-run parent · " + groupLabel(parent.observation.group));
        if (list(data.arc_families).some(family => family.root_id === parent.id || list(family.parents).some(value => value.id === parent.id)))
          entry.append(button("Correct saved arc family", () => beginFamily(parent.id)));
        entry.append(button("Show retained parent route", () => show(parent)), button("Review retained parent reading links", () => beginParent(parent)));
        if (["planar", "circular_arc", "circular_arc_span"].includes(parent.observation.geometry.kind) &&
            (list(parent.observation.geometry.dimension_check_ids).length + list(parent.observation.geometry.radius_check_ids).length))
          entry.append(button("Correct retained parent readings", () => beginParent(parent, true)));
        if (["planar", "circular_arc", "circular_arc_span"].includes(parent.observation.geometry.kind)) entry.append(button("Correct retained parent graphics", () => beginParent(parent, false, true)));
      }
      function referenceOverlay(source, ids) {
        const regions = allSourceEvidence(true).filter((entry) => ids.includes(entry.id) && sameSource(entry.source, source) && entry.bbox);
        const bbox = regions.length ? [Math.min(...regions.map((entry) => entry.bbox[0])), Math.min(...regions.map((entry) => entry.bbox[1])),
          Math.max(...regions.map((entry) => entry.bbox[2])), Math.max(...regions.map((entry) => entry.bbox[3]))] : [0, 0, 1, 1];
        env.showSource({ ...source, bbox });
      }
      for (const relationship of relationships) {
        const record = relationship.record, saved = clone(relationship), snapshot = controller.snapshot();
        const currentTarget = targets.find((entry) => entry.id === relationship.target_id && entry.kind === relationship.target_kind);
        const reading = list(currentTarget?.observation.readings).find((entry) => entry.id === relationship.reading_id);
        const entry = details(panel, label(relationship.validity) + " supplemental reading" + (reading ? " · " + reading.value + " " + reading.unit : ""));
        for (const issue of list(relationship.issues)) entry.append(node("p", issueText(issue), "error"));
        entry.append(button("Show target reference markers", () => referenceOverlay(record.observation_source, record.observation_reference_evidence_ids)),
          button("Show supplemental reference markers", () => referenceOverlay(record.reading_source, record.reading_reference_evidence_ids)));
        if (currentTarget?.kind === "observation") {
          const segment = list(data.segments).find((value) => value.id === currentTarget.id);
          if (segment) entry.append(button("Review this reading relationship", () => beginCorrection(segment)));
        }
        if (relationship.validity !== "withdrawn") {
          field(entry, "Withdrawal reason", withdrawalNotes.get(relationship.id) || "", (value) => withdrawalNotes.set(relationship.id, value));
          entry.append(button("Withdraw reading relationship", () => refresh(controller.withdrawReading(snapshot, saved, withdrawalNotes.get(relationship.id) || ""))));
        }
      }
      const history = details(panel, "Saved reading review history");
      for (const event of list(data.reading_relationship_events)) {
        const entry = details(history, label(event.kind) + " · " + [event.actor, event.reason].filter(Boolean).join(" · "));
        if (event.at) entry.append(node("p", event.at, "section-hint"));
        if (event.after) entry.append(button("Show saved target markers", () => referenceOverlay(event.after.observation_source, event.after.observation_reference_evidence_ids)),
          button("Show saved supplemental markers", () => referenceOverlay(event.after.reading_source, event.after.reading_reference_evidence_ids)));
      }
      const corrections = details(panel, "Saved parent dimension corrections");
      for (const event of list(data.parent_reading_events)) {
        const entry = details(corrections, [event.actor, event.reason].filter(Boolean).join(" · "));
        if (event.at) entry.append(node("p", event.at, "section-hint"));
        for (const update of list(event.reading_updates)) {
          const before = list(event.before?.readings).find((reading) => reading.id === update.reading_id);
          const after = list(event.after?.readings).find((reading) => reading.id === update.reading_id);
          if (!before || !after) continue;
          const index = list(event.before.geometry.dimension_check_ids).indexOf(update.reading_id) + 1;
          entry.append(node("p", "Dimension check " + index + ": " + before.value + " " + before.unit + " → " + after.value + " " + after.unit));
          for (const [reading, generationId, title] of [[before, event.base_generation_id, "Show original parent dimension source"],
            [after, event.replacement_generation_id, "Show corrected parent dimension source"]]) {
            const generation = list(data.generations).find((value) => value.id === generationId);
            // A pinned correction resolves its own immutable evidence, including legitimate foreign readings and datums.
            const retained = generation ? generationEvidence(generation) : generationId ? [] : allSourceEvidence(true);
            const evidence = [...new Map(retained.filter((value) => list(reading.evidence_ids).includes(value.id))
              .map((value) => [JSON.stringify([value.id, sourceKey(value.source)]), value])).values()];
            for (const region of evidence) entry.append(button(title, () => env.showSource({ ...region.source, bbox: region.bbox })));
            if (!evidence.length) entry.append(node("p", "The retained dimension source region is unavailable.", "error"));
          }
        }
      }
      const graphicHistory = details(panel, "Saved parent graphic corrections");
      for (const event of list(data.parent_graphic_events)) {
        const entry = details(graphicHistory, [event.actor, event.reason].filter(Boolean).join(" · "));
        if (event.at) entry.append(node("p", event.at, "section-hint"));
        entry.append(node("p", list(event.add_evidence_ids).length + " graphic regions added · " + list(event.remove_evidence_ids).length + " citations removed"));
        for (const [ids, source, title] of [[event.remove_evidence_ids, event.before?.source, "Show removed parent graphic"], [event.add_evidence_ids, event.after?.source, "Show added parent graphic"]]) {
          for (const id of list(ids)) {
            const evidence = retainedGraphicEvidence().find((region) => region.id === id && sameSource(region.source, source));
            if (evidence) entry.append(button(title, () => env.showSource({ ...evidence.source, bbox: evidence.bbox })));
            else entry.append(node("p", "The retained graphic source region is unavailable.", "error"));
          }
        }
      }
    }
    function decimalFraction(point, start, end) {
      const parts = [point, start, end].map((value) => {
        const match = String(value).match(/^(-?)(\d+)(?:\.(\d*))?(?:e([+-]?\d+))?$/i);
        if (!match) throw new Error("The proposed boundary coordinates could not be read.");
        const digits = (match[3] || ""), exponent = Number(match[4] || 0) - digits.length;
        return { integer: BigInt((match[1] || "") + match[2] + digits) * 10n ** BigInt(Math.max(0, exponent)), scale: Math.max(0, -exponent) };
      });
      const scale = Math.max(...parts.map((part) => part.scale));
      const [p, a, b] = parts.map((part) => part.integer * 10n ** BigInt(scale - part.scale));
      let numerator = p - a, denominator = b - a;
      if (denominator < 0n) { numerator = -numerator; denominator = -denominator; }
      if (denominator <= 0n || numerator <= 0n || numerator > denominator) throw new Error("Choose a boundary within the saved route.");
      if (numerator === denominator) return "1";
      let digits = "";
      // This locates a proposed boundary, not a quantity. The backend validates the exact partition.
      for (let index = 0; index < 34 && numerator; index++) { numerator *= 10n; digits += String(numerator / denominator); numerator %= denominator; }
      return "0." + digits;
    }
    function proposedBoundaries(parent, children) {
      const path = parent.geometry.points;
      return children.slice(0, -1).map((child, index) => {
        const point = child.observation.geometry.points.at(-1), next = children[index + 1].observation.geometry.points[0];
        if (!point || !next || point.some((value, axis) => Number(value) !== Number(next[axis])))
          throw new Error("The selected proposals do not share boundary endpoints. Review their order and source paths.");
        let chosen = null;
        for (let edge = 0; edge < path.length - 1; edge++) {
          const a = path[edge].map(Number), b = path[edge + 1].map(Number), delta = b.map((value, axis) => value - a[axis]);
          const squared = delta.reduce((sum, value) => sum + value * value, 0);
          if (!squared) continue;
          const fraction = point.reduce((sum, value, axis) => sum + (Number(value) - a[axis]) * delta[axis], 0) / squared;
          if (fraction <= 0 || fraction > 1 || edge + fraction >= path.length - 1) continue;
          const distance = point.reduce((sum, value, axis) => sum + (Number(value) - a[axis] - fraction * delta[axis]) ** 2, 0);
          if (!chosen || distance < chosen.distance) chosen = { edge, distance, axis: Math.abs(delta[0]) >= Math.abs(delta[1]) ? 0 : 1 };
        }
        if (!chosen) throw new Error("The proposal boundary is outside the saved route. Select its source location for review.");
        return { edge_index: chosen.edge, fraction: decimalFraction(point[chosen.axis], path[chosen.edge][chosen.axis], path[chosen.edge + 1][chosen.axis]), evidence_ids: [] };
      });
    }
    function beginRefresh(stage) {
      controller.refreshStage(stage, true); stopPicking();
      refreshEditing = { stage: { id: stage.id, fingerprint: stage.fingerprint }, groups: [], builder: emptyRefreshGroup(), captureDraft: null };
      env.render();
    }
    function showIncoming(proposal, stage) {
      const observation = proposal.observation;
      const ids = [...new Set([...list(observation.geometry?.support_ids),
        ...list(observation.readings).flatMap((reading) => list(reading.evidence_ids)),
        ...list(observation.evidence_ids), ...list(observation.group?.evidence_ids)])];
      const evidence = ids.map((id) => list(stage.evidence).find((entry) => entry.id === id && sameSource(entry.source, observation.source))).filter(Boolean);
      show({ ...proposal, evidence });
    }
    function refreshGroupEvidence(stage, builder, data) {
      const observations = builder.incoming_ids.map((id) => stage.incoming.find((entry) => entry.id === id)?.observation)
        .concat(builder.current_ids.map((id) => list(data.segments).find((entry) => entry.id === id)?.observation)).filter(Boolean);
      if (!observations.length || observations.some((entry) => !sameSource(entry.source, observations[0].source))) return [];
      const entries = [...list(stage.evidence), ...list(data.segments).filter((entry) => builder.current_ids.includes(entry.id)).flatMap((entry) => list(entry.evidence)),
        ...sourceEvidence(observations[0].source)];
      return [...new Map(entries.filter((entry) => sameSource(entry.source, observations[0].source)).map((entry) => [entry.id, entry])).values()];
    }
    function refreshBuilder(target, stage, data) {
      const draft = refreshEditing;
      if (!draft || draft.stage.id !== stage.id) return;
      try { controller.refreshStage(draft.stage, true); } catch (error) { target.append(node("p", error.message, "error")); return; }
      const builder = draft.builder;
      const usedIncoming = new Set(draft.groups.flatMap((group) => group.incoming_ids));
      const usedCurrent = new Set(draft.groups.flatMap((group) => group.current_ids));
      const current = list(data.segments).filter((entry) => stage.current_ids.includes(entry.id));
      function selection(kind, id, checked) {
        stopPicking(); draft.captureDraft = null; builder.cuts = []; builder.arcPartition = false; builder.children = [];
        builder[kind] = checked ? [...new Set([...builder[kind], id])] : builder[kind].filter((value) => value !== id);
        builder.evidence_ids = []; env.render();
      }
      target.append(node("p", "Compare the new portions with saved results. Choose the matching portions and record a source-backed decision; saved quantities stay in place until the complete review is applied.", "section-hint"));
      const incomingPanel = details(target, "New drawing portions", true);
      for (const [index, entry] of stage.incoming.entries()) {
        const title = "New portion " + (index + 1);
        if (!usedIncoming.has(entry.id)) checkbox(incomingPanel, title + " · " + groupLabel(entry.observation.group), builder.incoming_ids.includes(entry.id),
          (checked) => selection("incoming_ids", entry.id, checked));
        else incomingPanel.append(node("p", title + " · Included in a decision group"));
        incomingPanel.append(button("Show new portion " + (index + 1), () => showIncoming(entry, stage)));
      }
      const savedPanel = details(target, "Saved duct portions", true);
      for (const [index, entry] of current.entries()) {
        const title = "Saved portion " + (index + 1);
        if (!usedCurrent.has(entry.id)) checkbox(savedPanel, title + " · " + groupLabel(entry.observation.group) +
          (entry.status === "current" && entry.feet != null ? " · " + entry.feet + " ft" : " · " + label(entry.status)), builder.current_ids.includes(entry.id),
          (checked) => selection("current_ids", entry.id, checked));
        else savedPanel.append(node("p", title + " · Included in a decision group"));
        savedPanel.append(button("Show saved portion " + (index + 1), () => show(entry)));
      }
      field(target, "Reading decision", builder.disposition, (value) => {
        stopPicking(); builder.disposition = value; builder.cuts = []; builder.arcPartition = false; builder.children = []; env.render();
      }, [["", "Choose a decision"], ["retain_current", "Keep selected saved portions"], ["replace_current", "Replace one saved portion"],
        ["add_new", "Add distinct new portions"], ["exclude", "Exclude these new proposals"]]);
      const evidence = refreshGroupEvidence(stage, builder, data);
      const evidencePanel = details(target, "Source support for this decision", true);
      if (!evidence.length) evidencePanel.append(node("p", "Select portions from the same drawing source to choose supporting evidence.", "section-hint"));
      for (const entry of evidence) {
        checkbox(evidencePanel, "Decision evidence · " + (entry.text || "Graphic drawing region"), builder.evidence_ids.includes(entry.id), (checked) => {
          builder.evidence_ids = checked ? [...new Set([...builder.evidence_ids, entry.id])] : builder.evidence_ids.filter((id) => id !== entry.id);
        });
        sources(evidencePanel, [entry]);
      }
      const arcParent = current.find(entry => entry.id === builder.current_ids[0]);
      const arcIncoming = stage.incoming.find(entry => entry.id === builder.incoming_ids[0]);
      const arcEligible = builder.disposition === "replace_current" && builder.current_ids.length === 1 && builder.incoming_ids.length === 1 &&
        isArc(arcParent?.observation.geometry) && arcIncoming?.observation.schema === "duct-observation-2" && arcIncoming.observation.geometry.kind === "circular_arc";
      if (arcEligible) {
        checkbox(target, "Preserve the saved circular interval from the new full arc", Boolean(builder.arcPartition), checked => {
          stopPicking(); builder.arcPartition = checked; builder.cuts = []; builder.selectionIndex = null;
          builder.snapshot = controller.snapshot(arcParent); builder.observation = clone(arcParent.observation);
          builder.geometry = geometryDraft(arcParent.observation); builder.children = checked ? [childDraft(arcParent.observation.group)] : [];
          env.render();
        });
        if (builder.arcPartition) {
          builder.sourceEvidence = evidence;
          const replacement = details(target, "Review the retained circular interval", true);
          replacement.append(node("p", "With no cuts, reread this saved interval with its existing boundaries. Add cuts to partition it further. The new source must support the same circle, direction and full retained interval. Wider whole-arc readings require retained parent repair.", "section-hint"));
          replacement.append(button("Select arc replacement boundary", () => {
            controller.refreshStage(draft.stage, true); builder.selectionIndex = null; startPath(builder, "arc_split");
          }, builder.cuts.length >= 127));
          for (const [index, cut] of builder.cuts.entries()) {
            const boundary = details(replacement, "Replacement boundary " + (index + 1), true);
            boundary.append(button("Show replacement boundary " + (index + 1), () => showArcBoundary(builder, cut)));
            boundary.append(button("Move replacement boundary " + (index + 1), () => {
              controller.refreshStage(draft.stage, true); builder.selectionIndex = index; startPath(builder, "arc_split");
            }));
            for (const entry of evidence.filter(entry => ["graphic", "label"].includes(entry.kind))) {
              checkbox(boundary, "Boundary evidence · " + (entry.text || "Graphic source region"), cut.evidence_ids.includes(entry.id), checked => {
                cut.evidence_ids = checked ? [...new Set([...cut.evidence_ids, entry.id])] : cut.evidence_ids.filter(id => id !== entry.id);
                if (checked) builder.evidence_ids = [...new Set([...builder.evidence_ids, entry.id])];
              });
              sources(boundary, [entry]);
            }
          }
          childFields(replacement, builder);
        }
      }
      if (builder.disposition === "replace_current" && builder.current_ids.length === 1 && builder.incoming_ids.length > 1) {
        const parent = current.find((entry) => entry.id === builder.current_ids[0]);
        const children = builder.incoming_ids.map((id) => stage.incoming.find((entry) => entry.id === id));
        const replacement = details(target, "Order replacement portions along the saved route", true);
        children.forEach((entry, index) => {
          const name = "New portion " + (stage.incoming.indexOf(entry) + 1);
          replacement.append(node("p", (index + 1) + ". " + name + " · " + groupLabel(entry.observation.group)));
          if (index > 0) replacement.append(button("Move " + name.toLowerCase() + " earlier", () => {
            stopPicking(); [builder.incoming_ids[index - 1], builder.incoming_ids[index]] = [builder.incoming_ids[index], builder.incoming_ids[index - 1]];
            builder.cuts = []; env.render();
          }));
        });
        const planar = parent?.observation.geometry.kind === "planar" && children.every((entry) => entry.observation.geometry.kind === "planar");
        if (!planar) replacement.append(node("p", "Replacing one portion with several requires an exact planar route partition. Keep the saved portion or review separate proposals when that relationship is unsupported.", "error"));
        replacement.append(button("Use boundaries from selected proposals", () => {
          controller.refreshStage(draft.stage, true);
          if (!planar) throw new Error("Use supported planar paths for the replacement partition.");
          builder.cuts = proposedBoundaries(parent.observation, children); env.render();
        }, !planar));
        replacement.append(node("p", "Proposal boundaries are checked against the saved route when you apply the review. Select their source support below.", "section-hint"));
        replacement.append(button("Select replacement boundary", () => {
          if (!planar) throw new Error("Use a supported planar parent and planar replacements.");
          if (builder.cuts.length >= children.length - 1) throw new Error("The required boundary points are already selected.");
          builder.snapshot = controller.snapshot(); builder.observation = clone(parent.observation); builder.geometry = geometryDraft(parent.observation);
          startPath(builder, "split");
        }, !planar || builder.cuts.length >= children.length - 1));
        for (const [index, cut] of builder.cuts.entries()) {
          const boundary = details(replacement, "Replacement boundary " + (index + 1), true);
          for (const entry of evidence) checkbox(boundary, "Boundary evidence · " + (entry.text || "Graphic source region"), cut.evidence_ids.includes(entry.id), (checked) => {
            cut.evidence_ids = checked ? [...new Set([...cut.evidence_ids, entry.id])] : cut.evidence_ids.filter((id) => id !== entry.id);
            if (checked) builder.evidence_ids = [...new Set([...builder.evidence_ids, entry.id])];
          });
          boundary.append(button("Remove replacement boundary " + (index + 1), () => { builder.cuts.splice(index, 1); env.render(); }));
        }
      }
      const sourceObservation = stage.incoming.find((entry) => builder.incoming_ids.includes(entry.id))?.observation;
      if (sourceObservation) {
        target.append(button("Capture more decision evidence", () => {
          controller.refreshStage(draft.stage, true); stopPicking();
          draft.captureDraft = { snapshot: controller.snapshot(), observation: clone(sourceObservation), geometry: geometryDraft(sourceObservation),
            sourceEvidence: evidence, evidence_ids: [], groupEvidenceIds: [], capture: { kind: "graphic", text: "", bbox: null, active: false } };
          env.render();
        }));
        if (draft.captureDraft) captureFields(target, draft.captureDraft);
      }
      field(target, "Decision reason", builder.reason, (value) => { builder.reason = value; });
      target.append(button("Add decision group", () => {
        controller.refreshStage(draft.stage, true);
        const disposition = builder.disposition;
        if (!builder.incoming_ids.length || !builder.reason.trim() || !builder.evidence_ids.length)
          throw new Error("Choose the new portions, source evidence and a reason for this decision.");
        if (!["retain_current", "replace_current", "add_new", "exclude"].includes(disposition)) throw new Error("Choose a reading decision.");
        if ((disposition === "retain_current" && !builder.current_ids.length) ||
            (disposition === "replace_current" && builder.current_ids.length !== 1) ||
            (["add_new", "exclude"].includes(disposition) && builder.current_ids.length))
          throw new Error("Keep decisions need saved portions; replacement needs exactly one. Add and exclude decisions select new proposals only.");
        if (builder.evidence_ids.some((id) => !evidence.some((entry) => entry.id === id))) throw new Error("Choose evidence on the exact source of the selected portions.");
        if (builder.arcPartition && !arcEligible) throw new Error("Reopen the matching circular interval review.");
        if (disposition === "replace_current" && arcParent?.observation.geometry.kind === "circular_arc_span" && !builder.arcPartition)
          throw new Error("Explicitly preserve the saved circular interval; a full arc cannot replace its boundaries.");
        const count = builder.arcPartition ? builder.children.length - 1 : disposition === "replace_current" ? builder.incoming_ids.length - 1 : 0;
        if (builder.cuts.length !== count || builder.cuts.some((cut) => !cut.evidence_ids.length || cut.evidence_ids.some((id) => !builder.evidence_ids.includes(id))))
          throw new Error("Select each required replacement boundary and include its source support in this decision.");
        const group = Object.fromEntries(["incoming_ids", "current_ids", "disposition", "evidence_ids", "reason", "cuts"].map((key) => [key, clone(builder[key])]));
        if (builder.arcPartition) { group.schema = "duct-refresh-arc-partition-1"; group.groups = childGroups(builder); }
        draft.groups.push(group); stopPicking(); draft.builder = emptyRefreshGroup(); draft.captureDraft = null; env.render();
      }));
      const decisions = details(target, "Prepared decision groups · " + draft.groups.length, true);
      for (const [index, group] of draft.groups.entries()) {
        const title = ({ retain_current: "Keep saved portions", replace_current: "Replace saved portion", add_new: "Add new portions", exclude: "Exclude new proposals" })[group.disposition];
        decisions.append(node("p", title + " · " + group.incoming_ids.length + " new · " + group.current_ids.length + " saved · " + group.reason),
          button("Remove decision group " + (index + 1), () => { draft.groups.splice(index, 1); env.render(); }));
      }
      target.append(button("Apply reviewed drawing reading", async () => {
        if (await controller.reviewRefresh(draft.stage, draft.groups)) { stopPicking(); refreshEditing = null; env.render(); }
      }));
      target.append(button("Close reading review", () => { stopPicking(); refreshEditing = null; env.render(); }));
    }
    function refreshReadings(target, data) {
      const stages = list(data?.refreshes);
      if (!stages.length) return;
      target.append(node("h3", "Compare new drawing readings"));
      for (const entry of stages.slice().reverse()) {
        const panel = details(target, (entry.state === "pending" ? "Review pending" : label(entry.state)) + " · " + entry.incoming.length + " new portions", entry.state === "pending");
        for (const issue of list(entry.issues)) panel.append(node("p", issueText(issue), "error"));
        if (entry.state !== "pending") {
          const historyLabel = {
            applied: "This reading has a saved review decision.",
            rejected: "This reading was rejected; its original proposals remain retained.",
            superseded: "A later comparison replaces this saved comparison; its original proposals remain retained.",
          }[entry.state] || "This saved comparison is retained in the review history.";
          panel.append(node("p", historyLabel, "section-hint"));
          for (const [index, proposal] of entry.incoming.entries()) panel.append(button("Show retained proposal " + (index + 1), () => showIncoming(proposal, entry)));
          continue;
        }
        if (entry.validity === "stale") panel.append(node("p", "The saved results or source evidence changed. Restage this reading before applying new decisions.", "section-hint"),
          button("Restage against current results", () => refresh(controller.restage(entry))));
        else panel.append(button("Review this drawing reading", () => beginRefresh(entry)));
        refreshBuilder(panel, entry, data);
        field(panel, "Reason to reject this reading", refreshNotes.get(entry.id) || "", (value) => refreshNotes.set(entry.id, value));
        panel.append(button("Reject this drawing reading", async () => {
          if (await controller.rejectRefresh(entry, refreshNotes.get(entry.id) || "")) {
            if (refreshEditing?.stage.id === entry.id) { stopPicking(); refreshEditing = null; } env.render();
          }
        }));
      }
    }
    const revisionNames = { retain_current: "Keep saved portions", replace_current: "Replace complete saved scope",
      add_new: "Add new portions", exclude: "Exclude new proposals" };
    const emptyRevisionGroup = () => ({ incoming_ids: [], current_ids: [], disposition: "", evidence_ids: [], reason: "" });
    const sourceKey = (source) => JSON.stringify([source?.revision_id, source?.index, source?.sheet_id, source?.geometry_fingerprint]);
    function sourceName(source) {
      const page = list(env.getView().inventory).find((entry) => entry.revision_id === source.revision_id && entry.index === source.index);
      return (page?.assignment?.label || page?.document_name || "Drawing") + " · page " + (source.index + 1);
    }
    function revisionSources(stage, group) {
      const selected = [...stage.incoming.filter((entry) => group.incoming_ids.includes(entry.id)).map((entry) => entry.observation.source),
        ...stage.current_targets.filter((entry) => group.current_ids.includes(entry.id)).map((entry) => entry.source)];
      if (!stage.incoming.length) selected.push(...list(stage.evidence).filter((entry) =>
        Object.hasOwn(stage.source_bindings || {}, entry.source.sheet_id)).map((entry) => entry.source));
      return [...new Map(selected.map((source) => [sourceKey(source), source])).values()];
    }
    function validateRevisionGroup(stage, group) {
      if (!Object.hasOwn(revisionNames, group.disposition)) throw new Error("Choose a source revision decision.");
      if (!group.reason.trim() || !group.evidence_ids.length) throw new Error("Choose source evidence and explain this revision decision.");
      if (!stage.incoming.length) {
        if (group.disposition !== "exclude" || group.incoming_ids.length || group.current_ids.length)
          throw new Error("An empty drawing reading can only be explicitly excluded or rejected; it cannot replace saved portions.");
      } else if (!group.incoming_ids.length) throw new Error("Choose the new portions for this revision decision.");
      if (["replace_current", "retain_current"].includes(group.disposition) ? !group.current_ids.length : group.current_ids.length)
        throw new Error("Keep and replace decisions need saved portions. Add and exclude decisions select new portions only.");
      if (group.disposition === "replace_current") {
        const required = new Set(stage.current_targets.filter((entry) => group.current_ids.includes(entry.id)).flatMap((entry) => list(entry.required_current_ids)));
        if (required.size !== group.current_ids.length || group.current_ids.some((id) => !required.has(id)))
          throw new Error("Explicitly select the complete related scope before replacing a split family or linked depiction.");
      }
      const requiredSources = revisionSources(stage, group);
      const evidence = list(stage.evidence).filter((entry) => group.evidence_ids.includes(entry.id));
      if (!requiredSources.length || requiredSources.some((source) => !evidence.some((entry) => sameSource(source, entry.source))) ||
          (!stage.incoming.length && Object.keys(stage.source_bindings || {}).some((id) => !evidence.some((entry) => entry.source.sheet_id === id))) ||
          group.evidence_ids.some((id) => !evidence.some((entry) => entry.id === id && requiredSources.some((source) => sameSource(source, entry.source)))))
        throw new Error("Choose decision evidence on every old and new drawing represented in this group.");
    }
    function invalidateRevision(draft) {
      draft.acknowledged = null;
      if (draft.ackControl) draft.ackControl.checked = false;
    }
    const revisionSignature = (stage, draft) => JSON.stringify({ stage, groups: draft.groups, builder: draft.builder, context: revisionCheckContext() });
    function beginRevision(stage) {
      controller.revisionStage(stage, true); stopPicking(); editing = null; splitting = null; refreshEditing = null; supplementalCapture = null;
      if (revisionEditing?.stage.id !== stage.id || revisionEditing.stage.fingerprint !== stage.fingerprint)
        revisionEditing = { stage: { id: stage.id, fingerprint: stage.fingerprint }, groups: [], builder: emptyRevisionGroup(), acknowledged: null };
      env.render();
    }
    function revisionGroups(target, stage, draft, editable) {
      const decisions = details(target, "Prepared revision groups · " + draft.groups.length, true);
      for (const [index, group] of draft.groups.entries()) {
        const panel = details(decisions, "Revision group " + (index + 1) + " · " + revisionNames[group.disposition], true);
        panel.append(node("p", group.incoming_ids.length + " new · " + group.current_ids.length + " saved · " + group.reason));
        for (const source of revisionSources(stage, group)) panel.append(node("p", sourceName(source), "section-hint"));
        sources(panel, list(stage.evidence).filter((entry) => group.evidence_ids.includes(entry.id)));
        if (editable) panel.append(button("Remove revision decision group " + (index + 1), () => {
          draft.groups.splice(index, 1); invalidateRevision(draft); env.render();
        }));
      }
    }
    function revisionCheckContext() {
      const view = env.getView();
      return { selection: env.getSelection(), pages: view.sheet_geometries, sources: view.duct_takeoff.current_sources,
        evidence: view.duct_takeoff.available_evidence, relationships: view.duct_takeoff.reading_relationships,
        state: view.duct_takeoff.state_fingerprint };
    }
    function checkEvidence(entry = null, stage = null) {
      return revisionCheckTools.evidence([...list(stage?.evidence), ...list(entry?.evidence), ...allSourceEvidence()], env.getView().duct_takeoff.available_evidence);
    }
    function checkReadingDraft(owner, entry, decision, targets, evidence, later) {
      const selected = targets.find(target => decision.target_ids?.includes(target.id));
      if (!selected || selected.observation.geometry.kind !== "circular_arc") return null;
      owner.readingDrafts ||= new Map();
      const saved = entry.record || entry, result = owner.readingDrafts.get(entry.id) || {};
      Object.assign(result, { mode: "revision-check", ownerKey: later ? owner.snapshot : owner.stage,
        snapshot: later ? owner.snapshot : controller.snapshot(), decision, target: { id: entry.id },
        observation: selected.observation, geometry: { kind: "circular_arc", points: selected.observation.geometry.points,
          dimensionChecks: saved.origin.role === "dimension_check_ids" ? [decision.reading] : [],
          radiusChecks: saved.origin.role === "radius_check_ids" ? [decision.reading] : [] },
        sourceEvidence: evidence.filter(item => sameSource(item.source, selected.observation.source)),
        isCurrent: () => {
          if (later) { const current = controller.currentRevisionCheck(owner.snapshot); return owner === revisionCheckEditing &&
            current.targets.some(target => target.id === selected.id && target.fingerprint === selected.fingerprint) && decision.target_ids.includes(selected.id); }
          const stage = controller.revisionStage(owner.stage, true);
          return owner === revisionEditing && owner.builder.checkDrafts?.includes(decision) && decision.target_ids.includes(selected.id) &&
            stage.incoming.some(target => target.id === selected.id && JSON.stringify(target.observation) === JSON.stringify(selected.observation));
        } });
      owner.readingDrafts.set(entry.id, result); return result;
    }
    function checkDecisionFields(target, owner, entry, decision, targets, evidence, later, number) {
      const saved = entry.record || entry, title = (later ? "Resolution check " : "Revision check ") + number;
      const panel = details(target, title + " · " + (saved.origin.role === "radius_check_ids" ? "Radius" : "Length") + " · saved " + label(saved.state), true);
      panel.append(button("Show original " + title.toLowerCase(), () => show({ observation: saved.origin.observation })));
      const current = saved.observation || saved.retained_observation;
      if (current) panel.append(button((saved.observation ? "Show current " : "Show last reviewed ") + title.toLowerCase(), () => show({ observation: current })));
      for (const source of revisionCheckTools.priorSources(entry, later ? entry.targets : controller.revisionStage(owner.stage).current_targets))
        panel.append(node("p", "Current applicability · " + sourceName(source)), button("Show current page for " + title.toLowerCase(), () => env.showSource(source)));
      function change() { stopPicking(); if (later) owner.acknowledgment = null; else invalidateRevision(owner); env.render(); }
      field(panel, title + " disposition", decision.state, value => {
        decision.state = value; decision.target_ids = []; decision.scope = null; decision.relationships = [];
        decision.reading.evidence_ids = []; delete decision.reading.supplemental; change();
      }, [["", "Choose an explicit check decision"], ["active", "Map a reviewed circular check"], ["unresolved", "Keep unresolved · block dependent quantities"], ["not_applicable", "Retire with source evidence"]]);
      if (decision.state) for (const [index, candidate] of targets.entries()) {
        checkbox(panel, title + " target " + (index + 1) + " · " + sourceName(candidate.observation.source) + " · " + label(candidate.observation.geometry.kind) + (candidate.kind === "split_parent" ? " · retained root" : ""), decision.target_ids.includes(candidate.id), checked => {
          decision.target_ids = checked ? decision.state === "active" ? [candidate.id] : [...new Set([...decision.target_ids, candidate.id])] : decision.target_ids.filter(id => id !== candidate.id);
          decision.scope = null; decision.relationships = []; decision.reading.evidence_ids = []; delete decision.reading.supplemental; change();
        });
        panel.append(button("Show " + title.toLowerCase() + " target " + (index + 1), () => show({ observation: candidate.observation })));
      }
      if (decision.state === "not_applicable") panel.append(node("p", "Select every dependent target to retire this check across the complete scope."));
      if (decision.state === "active") {
        const readingDraft = checkReadingDraft(owner, entry, decision, targets, evidence, later);
        if (!readingDraft) panel.append(node("p", "Select exactly one circular root. Planar targets can remain unresolved or be retired with source evidence.", "section-hint"));
        else {
          const selected = targets.find(value => decision.target_ids.includes(value.id)), scopeId = later ? "observation_id" : "incoming_id";
          field(panel, title + " scope", decision.scope?.kind || "", value => {
            decision.scope = value ? { kind: value, [scopeId]: selected.id, ...(value === "interval" ? { start_point: [], end_point: [] } : {}) } : null; change();
          }, [["", "Choose physical check scope"], ["root", "Whole selected circular root"], ["interval", "Source-selected circular interval"]]);
          if (decision.scope?.kind === "interval") for (const [key, endpoint] of [["start_point", "start"], ["end_point", "end"]]) {
            panel.append(button("Pick " + title.toLowerCase() + " interval " + endpoint, () => { readingDraft.endpoint = key; startPath(readingDraft, "arc_split"); }));
            for (const [index, name] of [[0, "root start"], [2, "root end"]]) panel.append(button("Use " + name + " for " + title.toLowerCase() + " interval " + endpoint, () => {
              decision.scope[key] = clone(selected.observation.geometry.points[index]); change();
            }));
            panel.append(node("p", endpoint + " source point: " + (decision.scope[key].join(", ") || "Choose on drawing")));
          }
          if (pathRequest && pathDraftTarget === readingDraft) panel.append(button("Cancel " + title.toLowerCase() + " interval pick", () => { stopPicking(); env.render(); }));
          readingFields(panel, title + " reading", decision.reading, readingDraft.sourceEvidence);
          supplementalFields(panel, readingDraft);
        }
      }
      const sourcesToReview = [...revisionCheckTools.priorSources(entry, later ? entry.targets : controller.revisionStage(owner.stage).current_targets),
        ...targets.filter(value => decision.target_ids.includes(value.id)).map(value => value.observation.source)];
      const sourcePanel = details(panel, title + " decision evidence", true);
      for (const item of evidence.filter(value => sourcesToReview.some(source => sameSource(source, value.source)))) {
        checkbox(sourcePanel, title + " evidence · " + (item.text || "Drawing region") + " · " + sourceName(item.source), decision.evidence_ids.includes(item.id), checked => {
          decision.evidence_ids = checked ? [...new Set([...decision.evidence_ids, item.id])] : decision.evidence_ids.filter(id => id !== item.id); change();
        }); sources(sourcePanel, [item]);
      }
      field(panel, title + " reason", decision.reason, value => { decision.reason = value; if (later) owner.acknowledgment = null; else invalidateRevision(owner); });
    }
    function stagedCheckFields(target, stage, owner) {
      const builder = owner.builder;
      if (builder.disposition !== "replace_current") return;
      const affected = revisionCheckTools.affected(stage, builder);
      builder.checkDrafts ||= [];
      builder.checkDrafts = affected.map(entry => builder.checkDrafts.find(value => value.id === entry.id && value.fingerprint === entry.fingerprint) || revisionCheckTools.create(entry));
      const targets = stage.incoming.filter(value => builder.incoming_ids.includes(value.id));
      affected.forEach((entry, index) => checkDecisionFields(target, owner, entry, builder.checkDrafts[index], targets, checkEvidence(null, stage), false, index + 1));
    }
    function revisionCheckGroup(stage, builder) {
      if (builder.disposition === "replace_current" && builder.checkDrafts) for (const decision of builder.checkDrafts) {
        const entry = stage.arc_check_index.find(value => value.id === decision.id);
        const draft = checkReadingDraft(revisionEditing, entry, decision, stage.incoming.filter(value => builder.incoming_ids.includes(value.id)), checkEvidence(null, stage), false);
        decision.relationships = decision.state === "active" && draft ? reviewedSupplemental(draft) : [];
      }
      return revisionCheckTools.group(stage, builder, checkEvidence(null, stage));
    }
    function checkHistory(target, saved, title) {
      if (!saved) return;
      target.append(node("p", title + " · " + label(saved.state) + " · " + (saved.reason || "")));
      if (saved.origin?.observation) target.append(button("Show " + title.toLowerCase() + " original source", () => show({ observation: saved.origin.observation })));
      if (saved.observation) target.append(button("Show " + title.toLowerCase() + " current source", () => show({ observation: saved.observation })));
      else if (saved.current_source) target.append(button("Show " + title.toLowerCase() + " current page", () => env.showSource(saved.current_source)));
    }
    function revisionCheckFields(target, data) {
      if (list(data.revision_checks).length) {
        const section = details(target, "Retained checks across drawing revisions", true);
        for (const [index, entry] of data.revision_checks.entries()) {
          const panel = details(section, "Current revision check " + (index + 1) + " · " + label(entry.state), entry.state === "unresolved");
          checkHistory(panel, entry.record, "Saved check " + (index + 1));
          for (const issue of list(entry.issues)) panel.append(node("p", issueText(issue), "error"));
          for (const [number, candidate] of list(entry.targets).entries()) panel.append(button("Show dependent target " + (number + 1) + " for check " + (index + 1), () => show({ observation: candidate.observation })));
          panel.append(button("Review retained revision check " + (index + 1), () => {
            stopPicking(); revisionCheckEditing = { snapshot: controller.revisionCheckSnapshot(entry), decision: revisionCheckTools.create(entry), acknowledgment: null }; env.render();
          }, entry.validity !== "current"));
        }
      }
      if (list(data.revision_check_events).length) {
        const history = details(target, "Sealed revision check resolution history");
        for (const [index, event] of data.revision_check_events.entries()) {
          const panel = details(history, "Check resolution " + (index + 1), true);
          panel.append(node("p", event.reason || event.request?.reason || event.disposition?.reason || "Saved check review"));
          for (const transition of list(event.arc_check_transitions)) {
            checkHistory(panel, transition.before, "Before resolution " + (index + 1));
            checkHistory(panel, transition.after, "After resolution " + (index + 1));
            if (!transition.after) panel.append(node("p", "Retired across the complete dependent scope · original source retained above"));
            sources(panel, list(transition.decision_evidence));
          }
        }
      }
      const owner = revisionCheckEditing;
      if (!owner) return;
      const panel = details(target, "Resolve retained revision check", true);
      let entry;
      try { entry = controller.currentRevisionCheck(owner.snapshot); }
      catch (error) { panel.append(node("p", error.message + " The unsaved proposal is retained until closed.", "error"), node("p", owner.decision.reason),
        button("Close retained check review", () => { stopPicking(); revisionCheckEditing = null; env.render(); })); return; }
      checkDecisionFields(panel, owner, entry, owner.decision, entry.targets, checkEvidence(entry), true, 1);
      const signature = () => JSON.stringify([owner.snapshot, owner.decision, entry, revisionCheckContext()]);
      checkbox(panel, "Reviewed the retained check decision, targets and source evidence", owner.acknowledgment === signature(), checked => { owner.acknowledgment = checked ? signature() : null; });
      panel.append(button("Save retained check decision", async () => {
        const current = controller.currentRevisionCheck(owner.snapshot);
        if (owner.acknowledgment !== signature() || JSON.stringify(entry) !== JSON.stringify(current)) throw new Error("Review and acknowledge the current retained check decision again.");
        const draft = checkReadingDraft(owner, current, owner.decision, current.targets, checkEvidence(current), true);
        const decision = { ...owner.decision, relationships: owner.decision.state === "active" && draft ? reviewedSupplemental(draft) : [] };
        const disposition = revisionCheckTools.validate(current, decision, current.targets, checkEvidence(current), revisionCheckTools.priorSources(current, current.targets), true, current.current_ids);
        if (await controller.reviewRevisionCheck(owner.snapshot, disposition)) { stopPicking(); revisionCheckEditing = null; env.render(); }
      }), button("Close retained check review", () => { stopPicking(); revisionCheckEditing = null; env.render(); }));
    }
    function revisionBuilder(target, stage) {
      const draft = revisionEditing;
      if (!draft || draft.stage.id !== stage.id) return;
      let stale = null;
      try { controller.revisionStage(draft.stage, true); } catch (error) { stale = error.message; }
      if (stale) {
        target.append(node("p", stale + " Your prepared decisions are retained below for comparison.", "error"));
        revisionGroups(target, stage, draft, false);
        target.append(node("p", "Unfinished group · " + (revisionNames[draft.builder.disposition] || "Decision not chosen") + " · " + draft.builder.reason));
        for (const [index, check] of list(draft.builder.checkDrafts).entries()) target.append(node("p", "Retained check " + (index + 1) + " · " + (check.state ? label(check.state) : "Decision not chosen") + " · " + check.reason));
        target.append(button("Close source revision review", () => { revisionEditing = null; env.render(); })); return;
      }
      const builder = draft.builder;
      const usedNew = draft.groups.flatMap((group) => group.incoming_ids), usedOld = draft.groups.flatMap((group) => group.current_ids);
      target.append(node("p", "Compare each drawing in its own coordinates. Choose what this revision adds, replaces, retains or excludes. Replacement must include the complete related scope; no coordinates, scale or old checks transfer to new portions.", "section-hint"));
      const choices = details(target, "Choose the revision decision scope", true);
      const select = (key, id, checked) => {
        builder[key] = checked ? [...new Set([...builder[key], id])] : builder[key].filter((value) => value !== id);
        // Keep still-applicable choices visible, but require evidence to be rechecked if a source was removed.
        const sources = revisionSources(stage, builder);
        builder.evidence_ids = builder.evidence_ids.filter((value) => list(stage.evidence).some((entry) => entry.id === value && sources.some((source) => sameSource(source, entry.source))));
        invalidateRevision(draft); env.render();
      };
      const selectScope = (ids) => {
        const required = stage.current_targets.filter((entry) => ids.includes(entry.id)).flatMap((entry) => entry.required_current_ids);
        if (required.some((id) => usedOld.includes(id))) throw new Error("A related saved portion already belongs to another decision group. Remove that group before changing its scope.");
        builder.current_ids = [...new Set([...builder.current_ids, ...required])]; invalidateRevision(draft); env.render();
      };
      for (const [index, family] of list(stage.families).entries()) {
        choices.append(node("p", "Retained split family " + (index + 1) + " · saved portions " + family.descendant_ids.map((id) => stage.current_targets.findIndex((entry) => entry.id === id) + 1).join(", "), "section-hint"),
          button("Select complete split family " + (index + 1), () => selectScope(family.descendant_ids), !stage.incoming.length));
      }
      for (const [index, correspondence] of list(stage.correspondences).entries()) choices.append(node("p", "Related depiction set " + (index + 1) + " · " + label(correspondence.record?.state) + " · saved portions " +
        correspondence.member_ids.map((id) => stage.current_targets.findIndex((entry) => entry.id === id) + 1).join(", "), "section-hint"));
      for (const [index, proposal] of stage.incoming.entries()) {
        const input = checkbox(choices, "Revision new portion " + (index + 1) + " · " + sourceName(proposal.observation.source) + " · " + groupLabel(proposal.observation.group),
          builder.incoming_ids.includes(proposal.id), (checked) => select("incoming_ids", proposal.id, checked));
        input.disabled ||= usedNew.includes(proposal.id);
        if (usedNew.includes(proposal.id)) choices.append(node("p", "New portion " + (index + 1) + " is already in a prepared group.", "section-hint"));
      }
      if (!stage.incoming.length) choices.append(node("p", "This reading contains no duct portions. Review its source evidence to exclude it, or reject the reading. Saved portions cannot be removed by an empty reading.", "section-hint"));
      for (const [index, current] of stage.current_targets.entries()) {
        const input = checkbox(choices, "Revision saved portion " + (index + 1) + " · " + sourceName(current.source) + " · " + groupLabel(current.observation.group),
          builder.current_ids.includes(current.id), (checked) => select("current_ids", current.id, checked));
        input.disabled ||= usedOld.includes(current.id) || !stage.incoming.length;
        if (list(current.required_current_ids).length > 1) {
          const related = stage.current_targets.filter((entry) => current.required_current_ids.includes(entry.id));
          choices.append(node("p", "Replacing saved portion " + (index + 1) + " requires " + related.map((entry) =>
            "portion " + (stage.current_targets.indexOf(entry) + 1) + " on " + sourceName(entry.source)).join("; ") + ".", "section-hint"));
          choices.append(button("Select complete related scope for saved portion " + (index + 1), () => {
            selectScope([current.id]);
          }, !stage.incoming.length));
        }
      }
      field(target, "Revision decision", builder.disposition, (value) => { builder.disposition = value; invalidateRevision(draft); env.render(); },
        [["", "Choose the effect on saved duct scope"], ...Object.entries(revisionNames)]);
      const evidencePanel = details(target, "Source evidence for this revision decision", true);
      const requiredSources = revisionSources(stage, builder);
      if (!stage.incoming.length && Object.keys(stage.source_bindings || {}).some((id) => !requiredSources.some((source) => source.sheet_id === id)))
        evidencePanel.append(node("p", "An incoming drawing has no retained evidence region. Read or capture that source before staging a new exclusion review, or reject this reading.", "error"));
      for (const source of requiredSources) {
        const sourcePanel = details(evidencePanel, sourceName(source), true);
        const evidence = list(stage.evidence).filter((entry) => sameSource(entry.source, source));
        if (!evidence.length) sourcePanel.append(node("p", "This source has no retained decision region. Read or capture its evidence before staging another review.", "error"));
        for (const entry of evidence) {
          checkbox(sourcePanel, "Revision evidence · " + (entry.text || "Drawing region") + " · " + sourceName(entry.source), builder.evidence_ids.includes(entry.id), (checked) => {
            builder.evidence_ids = checked ? [...new Set([...builder.evidence_ids, entry.id])] : builder.evidence_ids.filter((id) => id !== entry.id);
            invalidateRevision(draft);
          });
          sources(sourcePanel, [entry]);
        }
      }
      if (!requiredSources.length) evidencePanel.append(node("p", "Select the new and saved portions to review their drawing evidence.", "section-hint"));
      stagedCheckFields(target, stage, draft);
      field(target, "Revision decision reason", builder.reason, (value) => { builder.reason = value; invalidateRevision(draft); });
      target.append(button("Add revision decision group", () => {
        controller.revisionStage(draft.stage, true); validateRevisionGroup(stage, builder);
        if ((!stage.incoming.length && draft.groups.length) || builder.incoming_ids.some((id) => usedNew.includes(id)) || builder.current_ids.some((id) => usedOld.includes(id)))
          throw new Error("Use each portion in only one revision decision group.");
        const group = revisionCheckGroup(stage, builder);
        draft.groups.push(group); draft.builder = emptyRevisionGroup(); invalidateRevision(draft); env.render();
      }));
      revisionGroups(target, stage, draft, true);
      draft.ackControl = checkbox(target, "I reviewed every revision decision, the complete affected scope, and the evidence on every old and new drawing", draft.acknowledged === revisionSignature(stage, draft),
        (checked) => { draft.acknowledged = checked ? revisionSignature(stage, draft) : null; });
      target.append(button("Apply reviewed source revision", async () => {
        const current = controller.revisionStage(draft.stage, true);
        if (draft.acknowledged !== revisionSignature(current, draft)) throw new Error("Review and acknowledge the current revision decisions and source evidence before applying them.");
        for (const group of draft.groups) { validateRevisionGroup(current, group); revisionCheckGroup(current, group); }
        if (await controller.reviewRevision(draft.stage, draft.groups)) { revisionEditing = null; env.render(); }
      }), button("Close source revision review", () => { revisionEditing = null; env.render(); }));
    }
    function revisionReadings(target, data) {
      const stages = list(data?.revisions);
      if (stages.length) target.append(node("h3", "Compare drawing sources and revisions"));
      for (const entry of stages.slice().reverse()) {
        const panel = details(target, (entry.state === "pending" ? "Source revision awaiting review" : label(entry.state) + " source revision") + " · " + entry.incoming.length + " new portions", entry.state === "pending");
        for (const issue of list(entry.issues)) panel.append(node("p", issueText(issue), "error"));
        const comparison = details(panel, "Compare retained old and new source portions", entry.state === "pending");
        for (const [index, proposal] of entry.incoming.entries()) comparison.append(node("p", "New portion " + (index + 1) + " · " + sourceName(proposal.observation.source)),
          button("Show revision new portion " + (index + 1), () => showIncoming(proposal, entry)));
        for (const [index, old] of entry.current_targets.entries()) comparison.append(node("p", "Saved portion " + (index + 1) + " · " + sourceName(old.source)),
          button("Show revision saved portion " + (index + 1), () => showIncoming(old, entry)));
        if (!entry.incoming.length) sources(comparison, list(entry.evidence).filter((evidence) => Object.hasOwn(entry.source_bindings || {}, evidence.source.sheet_id)));
        if (entry.state === "pending") {
          if (entry.validity === "stale" || controller.revisionNeedsCheckPins(entry)) panel.append(node("p", "The drawings, retained checks or saved scope changed. Restage this revision before applying decisions.", "section-hint"),
            button("Restage source revision against current results", () => refresh(controller.stageRevision(controller.snapshot(), entry.producer_record_id))));
          else panel.append(button("Review this source revision", () => beginRevision(entry)));
          revisionBuilder(panel, entry);
          field(panel, "Reason to reject this source revision", revisionNotes.get(entry.id) || "", (value) => revisionNotes.set(entry.id, value));
          panel.append(button("Reject this source revision", async () => {
            if (await controller.rejectRevision(entry, revisionNotes.get(entry.id) || "")) {
              if (revisionEditing?.stage.id === entry.id) revisionEditing = null; env.render();
            }
          }));
        } else panel.append(node("p", ({ applied: "These source decisions are saved. Reviewing a previous issue requires a new explicit comparison.",
          rejected: "This source revision was rejected; its original proposals and evidence remain in history.",
          superseded: "A later source comparison replaces this review. Its original proposals and evidence remain in history." })[entry.state] || "This source review is retained in history.", "section-hint"));
        for (const decision of list(data.revision_decisions).filter((value) => value.revision_id === entry.id && value.revision_fingerprint === entry.fingerprint)) {
          const history = details(panel, "Saved source revision decision", true);
          history.append(node("p", decision.request?.reason || "Source review saved"));
          for (const [index, group] of list(decision.groups).entries()) {
            const saved = details(history, "Saved revision group " + (index + 1) + " · " + (revisionNames[group.disposition] || label(group.disposition)), true);
            saved.append(node("p", group.reason));
            for (const [number, check] of list(group.check_dispositions).entries()) {
              const retained = list(entry.arc_check_index).find(value => value.id === check.id)?.record;
              const review = details(saved, "Sealed revision check " + (number + 1) + " · " + label(check.state), true);
              review.append(node("p", check.reason));
              if (retained) checkHistory(review, retained, "Original revision check " + (number + 1));
              for (const [targetIndex, incoming] of entry.incoming.filter(value => list(check.incoming_ids).includes(value.id)).entries())
                review.append(button("Show sealed check " + (number + 1) + " new target " + (targetIndex + 1), () => showIncoming(incoming, entry)));
              sources(review, list(entry.evidence).filter(value => list(check.evidence_ids).includes(value.id)));
            }
            for (const transition of list(group.arc_check_transitions)) {
              checkHistory(saved, transition.before, "Before source revision check");
              checkHistory(saved, transition.after, "After source revision check");
              if (!transition.after) saved.append(node("p", "Check retired across the complete replacement scope; original source retained above."));
              sources(saved, list(transition.decision_evidence));
            }
            for (const [name, sources] of [["Old", group.old_sources], ["New", group.new_sources]]) for (const source of list(sources)) {
              saved.append(node("p", name + " drawing · " + sourceName(source)));
              const evidence = list(entry.evidence).filter((value) => group.evidence_ids.includes(value.id) && sameSource(value.source, source));
              for (const region of evidence) saved.append(button("Show saved " + name.toLowerCase() + " revision evidence", () => env.showSource({ ...region.source, bbox: region.bbox })));
            }
          }
        }
      }
      if (!data?.current_generation_id) return;
      const candidates = new Map();
      const add = (id, source = null) => { if (id && !candidates.has(id)) candidates.set(id, source); };
      for (const job of list(env.getView().duct_producer?.jobs)) if (job.state === "completed") add(job.id, job.source);
      for (const generation of list(data.generations)) for (const record of list(generation.source_records)) {
        const source = list(record.sheet_ids).map((id) => generation.current_sources?.[id]?.evidence?.[0]?.source).find(Boolean); add(record.id, source);
      }
      for (const entry of [...list(data.refreshes), ...stages]) add(entry.producer_record_id, entry.incoming?.[0]?.observation.source);
      if (!candidates.size) return;
      const history = details(target, "Review a saved drawing reading again");
      history.append(node("p", "Stage a retained reading for a new comparison against today's saved scope. This includes earlier issued drawings; staging never restores previous quantities.", "section-hint"));
      if (!candidates.has(historicalProducer)) historicalProducer = "";
      field(history, "Saved reading to review", historicalProducer, (value) => { historicalProducer = value; }, [["", "Choose a retained reading"], ...[...candidates].map(([id, source], index) =>
        [id, "Saved reading " + (index + 1) + (source ? " · " + sourceName(source) : "")])]);
      const snapshot = controller.snapshot();
      history.append(button("Stage saved reading for source review", () => refresh(controller.stageRevision(snapshot, historicalProducer))));
    }
    function coverageReview(target, data) {
      if (!data.current_generation_id) return;
      const section = details(target, "Review drawing coverage and repeated depictions");
      section.append(node("p", "Check the selected views against the drawing. A completed model run alone does not establish complete coverage.", "section-hint"));
      section.append(button("Start coverage review", () => {
        coverageDraft = { snapshot: controller.snapshot(), coverage: clone(data.coverage_review_template || data.coverage),
          correspondences: clone(data.correspondences || []), reason: "", link: { members: [], primary: "", evidence_ids: [] } }; env.render();
      }));
      if (!coverageDraft) return;
      section.open = true;
      const draft = coverageDraft;
      try { controller.current(draft.snapshot); } catch (e) { section.append(node("p", e.message, "error")); return; }
      field(section, "Drawing coverage", draft.coverage.state, (v) => { draft.coverage.state = v; },
        [["unknown", "Unknown"], ["partial", "Partial · final quantity remains unknown"], ["complete", "All selected views and physical portions checked"]]);
      for (const issue of list((data.coverage_review_template || data.coverage)?.issues)) checkbox(section, "Resolved: " + issue,
        !draft.coverage.issues.includes(issue), (checked) => {
          draft.coverage.issues = checked ? draft.coverage.issues.filter((v) => v !== issue) : [...new Set([...draft.coverage.issues, issue])];
        });
      for (const segment of list(data.segments)) section.append(button("Show " + groupLabel(segment.observation.group), () => show(segment)));
      const evidence = Object.values(data.current_sources || {}).flatMap((source) => list(source.evidence));
      const evidencePanel = details(section, "Coverage source evidence");
      evidenceChoices(evidencePanel, evidence, draft.coverage.evidence_ids);
      const repeatPanel = details(section, "Repeated depictions of the same physical portion");
      repeatPanel.append(node("p", "Link only source-supported depictions of the same physical portion. Crossing lines, matching tags and equal lengths do not establish a shared portion.", "section-hint"));
      for (const [index, segment] of list(data.segments).entries()) {
        checkbox(repeatPanel, "Repeated depiction · portion " + (index + 1) + " · " + groupLabel(segment.observation.group),
          draft.link.members.includes(segment.id), (checked) => {
            draft.link.members = checked ? [...new Set([...draft.link.members, segment.id])] : draft.link.members.filter((id) => id !== segment.id);
            if (!draft.link.members.includes(draft.link.primary)) draft.link.primary = draft.link.members[0] || "";
            env.render();
          });
        repeatPanel.append(button("Show portion " + (index + 1), () => show(segment)));
      }
      field(repeatPanel, "Depiction to measure once", draft.link.primary, (value) => { draft.link.primary = value; },
        [["", "Choose one of the linked portions"], ...list(data.segments).filter((entry) => draft.link.members.includes(entry.id))
          .map((entry) => [entry.id, "Portion " + (list(data.segments).indexOf(entry) + 1) + " · " + groupLabel(entry.observation.group)])]);
      for (const entry of evidence) {
        checkbox(repeatPanel, "Correspondence evidence · " + (entry.text || "Drawing region"), draft.link.evidence_ids.includes(entry.id), (checked) => {
          draft.link.evidence_ids = checked ? [...new Set([...draft.link.evidence_ids, entry.id])] : draft.link.evidence_ids.filter((id) => id !== entry.id);
        });
        sources(repeatPanel, [entry]);
      }
      repeatPanel.append(button("Add repeated-depiction link", () => {
        if (draft.link.members.length < 2 || !draft.link.members.includes(draft.link.primary) || !draft.link.evidence_ids.length)
          throw new Error("Select at least two depictions, the portion to measure once, and supporting source evidence.");
        const members = Object.fromEntries(draft.link.members.map((id) => [id, list(data.segments).find((segment) => segment.id === id).fingerprint]));
        draft.correspondences.push({ id: env.newId(), members, primary_observation_id: draft.link.primary, state: "verified", evidence_ids: clone(draft.link.evidence_ids) });
        draft.link = { members: [], primary: "", evidence_ids: [] }; env.render();
      }));
      for (const record of draft.correspondences) {
        const link = details(section, "Recorded depiction link · " + Object.keys(record.members).length + " portions");
        field(link, "Link treatment", record.state, (value) => { record.state = value; },
          [["verified", "Same physical portion · measure once"], ["uncertain", "Uncertain · keep unresolved"], ["rejected", "Separate portions · do not merge"]]);
        for (const id of Object.keys(record.members)) {
          const segment = list(data.segments).find((entry) => entry.id === id);
          if (segment) link.append(button("Show linked depiction", () => show(segment)));
          else link.append(node("p", "A linked depiction is no longer current.", "error"));
        }
      }
      field(section, "Coverage review reason", draft.reason, (v) => { draft.reason = v; });
      section.append(button("Save coverage review", async () => {
        if (!draft.reason.trim()) throw new Error("Explain the checked coverage and any remaining gaps.");
        if (await controller.review(draft.snapshot, draft.coverage, draft.correspondences, draft.reason)) { coverageDraft = null; env.render(); }
      }));
    }
    function render(target) {
      const view = env.getView(); const data = view.duct_takeoff; const producer = view.duct_producer;
      if (!data && !producer) return;
      target.append(node("h3", "Duct lengths by size"), node("p", "Find ductwork on a drawing, then review exceptions against the highlighted sources. Lengths use drawn centerlines; fittings and allowances stay separate.", "section-hint"));
      const pages = list(view.inventory).filter((page) => ["plan", "detail", "riser"].includes(page.assignment?.role));
      const selection = env.getSelection();
      if (!pages.some((page) => page.revision_id + ":" + page.index === selectedPage)) {
        const selected = pages.find((page) => page.revision_id === selection?.revision && page.index === selection?.index) || pages[0];
        selectedPage = selected ? selected.revision_id + ":" + selected.index : "";
      }
      field(target, "Drawing to read", selectedPage, (v) => { selectedPage = v; }, [["", "Choose a drawing"], ...pages.map((page) =>
        [page.revision_id + ":" + page.index, (page.assignment.label || page.document_name) + " · page " + (page.index + 1)])]);
      if (!producer?.configured) target.append(node("p", "Configure a local drawing model to find ductwork. Saved results remain available.", "section-hint"));
      if (!pages.length) target.append(node("p", "Assign plan, detail or riser roles in Documents before finding ductwork.", "section-hint"));
      target.append(button("Find ductwork", () => {
        const page = pages.find((entry) => entry.revision_id + ":" + entry.index === selectedPage);
        if (!page) throw new Error("Choose a current plan, detail or riser drawing.");
        return refresh(controller.start({ revision_id: page.revision_id, index: page.index }));
      }, !producer?.configured || !pages.length || controller.active()));
      if (data?.current_generation_id) target.append(button("Add missed duct portion", () => {
        const page = pages.find((entry) => entry.revision_id + ":" + entry.index === selectedPage);
        if (!page) throw new Error("Choose the current drawing containing the missed portion.");
        beginAdd(page);
      }, !pages.length));
      for (const job of list(producer?.jobs).slice().reverse()) {
        const active = ["queued", "running"].includes(job.state);
        const entry = details(target, label(job.state) + " drawing reading · page " + (job.source?.index + 1 || "Unknown"), active);
        if (job.source) entry.append(button("Show drawing", () => env.showSource(job.source)));
        if (job.error) entry.append(node("p", job.error.message || "The drawing reading could not finish.", "error"));
        if (active) entry.append(button("Stop reading", () => refresh(controller.cancel(job.id))));
        if (controller.failures.has(job.id)) entry.append(node("p", controller.failures.get(job.id), "error"),
          button("Retry calculation from this reading", () => refresh(controller.retry(job.id))));
      }
      refreshReadings(target, data);
      revisionReadings(target, data);
      if (!data?.available) { target.append(node("p", "Duct quantities are unknown until drawing observations are available.", "section-hint")); return; }
      const pendingRevision = list(data.revisions).some((entry) => entry.state === "pending");
      const pendingReading = pendingRevision || list(data.refreshes).some((entry) => entry.state === "pending");
      target.append(node("p", pendingRevision ? "Saved quantities are retained while a drawing source revision awaits review." : pendingReading ? "Saved quantities are retained while a new drawing reading awaits review." :
        data.complete ? "Selected drawing coverage is complete." : "Coverage is incomplete · final duct quantity is unknown.",
        data.complete && !pendingReading ? "equipment-message" : "section-hint"));
      for (const issue of list(data.issues).filter((entry) => entry?.code !== "duct_segment")) target.append(node("p", issueText(issue), "error"));
      for (const group of list(data.groups)) {
        const entry = details(target, groupLabel(group.group), true);
        entry.append(node("strong", group.complete && group.total_ft != null ? group.total_ft + " ft" :
          (group.known_subtotal_ft == null ? "Unknown length" : group.known_subtotal_ft + " ft known subtotal") + " · final unknown"));
        for (const [method, values] of Object.entries(group.contributions || {})) entry.append(node("p",
          (method === "measured_planar" ? "Measured centerline" : "Derived from dimensions / elevations") + ": " + (values.feet ?? "Unknown") + " ft"));
      }
      const portions = details(target, "Drawing portions and exceptions · " + list(data.segments).length, true);
      for (const segment of list(data.segments)) {
        const entry = details(portions, groupLabel(segment.observation.group) + " · " +
          (segment.status === "current" && segment.feet != null ? segment.feet + " ft" : label(segment.status)));
        for (const issue of list(segment.issues)) entry.append(node("p", issueText(issue), "error"));
        entry.append(button("Show duct portion", () => show(segment)), button("Correct this portion", () => beginCorrection(segment)));
        if (["planar", "circular_arc", "circular_arc_span"].includes(segment.observation.geometry.kind)) entry.append(button("Split at size change", () => beginSplit(segment)));
        if (list(data.arc_families).some(family => family.root_id === segment.id || list(family.leaf_ids).includes(segment.id)))
          entry.append(button("Correct saved arc family", () => beginFamily(segment.id)));
        sources(entry, segment.evidence);
      }
      correction(target, data);
      splitFields(target);
      familyFields(target, data);
      revisionCheckFields(target, data);
      readingHistory(target, data);
      const recalculation = details(target, "Recalculate using current verified scales");
      const calibrationIds = {}; const snapshot = controller.snapshot();
      for (const segment of list(data.segments)) {
        const geometry = segment.observation.geometry;
        if (!["planar", "circular_arc", "circular_arc_span"].includes(geometry.kind) && geometry.projection?.kind !== "scaled_path") continue;
        const facts = list(view.calibrations).filter((fact) => fact.state === "verified" &&
          fact.source.revision_id === segment.source.revision_id && fact.source.index === segment.source.index);
        field(recalculation, groupLabel(segment.observation.group), "", (value) => {
          if (value) calibrationIds[segment.id] = value; else delete calibrationIds[segment.id];
        }, [["", "Keep saved scale binding"], ...facts.map((fact) => [fact.id, fact.label || "Verified drawing scale"])]);
      }
      recalculation.append(button("Recalculate duct lengths", () => refresh(controller.recalculate(snapshot, calibrationIds))));
      coverageReview(target, data);
    }
    function pickLine(value) {
      if (!editing || !pathPicking || value.purpose !== "duct-correction") return false;
      controller.current(editing.snapshot);
      const source = editing.observation.source;
      if (value.revision_id !== source.revision_id || value.index !== source.index)
        throw new Error("Choose the centerline on the portion’s original drawing.");
      editing.observation.geometry.points = clone(value.points);
      editing.geometry.points = clone(value.points);
      pathPicking = false; env.pickLine(false); env.render(); return true;
    }
    function pathDraft(value) {
      if (!pathRequest || value.request_id !== pathRequest.request_id || !sameSource(value.source, pathRequest.source)) return false;
      const draft = pathDraftTarget;
      if (!draft) return false;
      if (value.state === "cancelled") { pathRequest = null; env.render(); return true; }
      controller.current(draft.snapshot);
      if (value.state !== "finished") return false;
      if (draft.mode === "revision-check") {
        if (!draft.isCurrent() || JSON.stringify(pathRequest.page_geometry) !== JSON.stringify(pageGeometry(draft.observation.source))) {
          stopPicking(); throw new Error("The check target or drawing coordinate context changed. Reopen the interval pick.");
        }
        const point = value.split?.source_point;
        if (!pathTools.arcCut(draft.observation.geometry, point, pageGeometry(draft.observation.source), draft.observation.source)) throw new Error("Choose an interval endpoint within the selected circular root.");
        draft.decision.scope[draft.endpoint] = clone(point);
        pathRequest = null; pathDraftTarget = null; env.render(); return true;
      }
      if (draft.familyEdit) {
        if (draft !== familyEditing) return false;
        if (JSON.stringify(pathRequest.page_geometry) !== JSON.stringify(pageGeometry(draft.observation.source))) {
          stopPicking(); throw new Error("The drawing coordinate context changed. Reopen the family path edit.");
        }
        controller.currentFamily(draft.snapshot);
        if (draft.familyPick === "interval") {
          const point = value.split?.source_point;
          if (!pathTools.arcCut(draft.observation.geometry, point, pageGeometry(draft.observation.source), draft.observation.source))
            throw new Error("Choose an interval endpoint within the corrected circular arc.");
          const decision = draft.decisions[draft.intervalDecision];
          decision.interval[draft.intervalEndpoint] = clone(point); decision.scope[draft.intervalEndpoint] = clone(point);
          if (decision.reading.supplemental) decision.reading.supplemental.reviewed = false;
          familyTools.invalidate(draft); pathRequest = null; pathDraftTarget = null; env.render(); return true;
        }
        if (pathRequest.mode === "circular_arc") {
          if (!Array.isArray(value.points) || value.points.length !== 3 || value.points.some(point => !Array.isArray(point) || point.length !== 2 || point.some(v => !Number.isFinite(v) || v < 0 || v > 1)))
            throw new Error("Finish three circular anchors on the original drawing.");
          familyTools.checkpoint(draft); draft.observation.geometry.points = clone(value.points); draft.geometry.points = clone(value.points);
          familyTools.invalidate(draft, true); pathRequest = null; pathDraftTarget = null; env.render(); return true;
        }
      }
      if (pathRequest.mode === "arc_split") {
        if (draft.refreshReplacement) {
          if (!refreshEditing || refreshEditing.builder !== draft || !draft.arcPartition) return false;
          controller.refreshStage(refreshEditing.stage, true);
        }
        if (draft.cuts.length >= 127 && draft.selectionIndex == null) throw new Error("A split supports at most 127 internal boundaries.");
        const point = value.split?.source_point;
        const locate = cut => pathTools.arcCut(draft.observation.geometry, cut.source_point, pageGeometry(draft.observation.source), draft.observation.source)?.position;
        const position = locate({ source_point: point });
        if (position === undefined) throw new Error("Choose a point within the saved circular interval.");
        const cuts = draft.cuts.filter((_, index) => index !== draft.selectionIndex);
        if (cuts.some(cut => Math.abs(locate(cut) - position) < 1e-12)) throw new Error("That size-change ray is already selected.");
        const selected = { source_point: clone(point), evidence_ids: draft.selectionIndex == null ? [] : clone(draft.cuts[draft.selectionIndex].evidence_ids) };
        cuts.push(selected); cuts.sort((a, b) => locate(a) - locate(b));
        const added = cuts.indexOf(selected);
        if (draft.selectionIndex != null && added !== draft.selectionIndex) throw new Error("Keep the selected size change between its neighboring cuts, or start a new split review.");
        if (draft.familyEdit) familyTools.checkpoint(draft);
        if (draft.selectionIndex == null) {
          if (!draft.children.length) draft.children.push(childDraft(draft.observation.group));
          draft.children.splice(added + 1, 0, childDraft(draft.children[added].group));
        }
        draft.cuts = cuts;
        if (draft.familyEdit) familyTools.invalidate(draft, true);
      } else if (pathRequest.mode === "split") {
        if (!value.split || !Number.isInteger(value.split.edge_index) || typeof value.split.fraction !== "string") return false;
        const selected = value.split, fraction = Number(selected.fraction);
        const position = selected.edge_index + fraction;
        if (selected.edge_index < 0 || selected.edge_index >= draft.geometry.points.length - 1 || !Number.isFinite(fraction) ||
            fraction <= 0 || fraction > 1 || position >= draft.geometry.points.length - 1)
          throw new Error("Choose a size-change point inside the saved route.");
        if (draft.refreshReplacement) {
          if (!refreshEditing || refreshEditing.builder !== draft) return false;
          controller.refreshStage(refreshEditing.stage, true);
          if (draft.cuts.some((cut) => cut.edge_index + Number(cut.fraction) === position)) throw new Error("That replacement boundary is already selected.");
          draft.cuts.push({ edge_index: selected.edge_index, fraction: selected.fraction, evidence_ids: [] });
          draft.cuts.sort((a, b) => a.edge_index + Number(a.fraction) - b.edge_index - Number(b.fraction));
          pathRequest = null; pathDraftTarget = null; env.render(); return true;
        }
        const cuts = draft.cuts.filter((_, index) => index !== draft.selectionIndex);
        if (cuts.some((cut) => cut.edge_index + Number(cut.fraction) === position)) throw new Error("That size-change point is already selected.");
        cuts.push({ edge_index: selected.edge_index, fraction: selected.fraction,
          evidence_ids: draft.selectionIndex === null ? [] : clone(draft.cuts[draft.selectionIndex].evidence_ids) });
        cuts.sort((a, b) => (a.edge_index + Number(a.fraction)) - (b.edge_index + Number(b.fraction)));
        if (draft.selectionIndex !== null && cuts.findIndex((cut) => cut.edge_index + Number(cut.fraction) === position) !== draft.selectionIndex)
          throw new Error("Keep the selected size change between its neighboring cuts, or start a new split review.");
        if (draft.selectionIndex === null) {
          const added = cuts.findIndex((cut) => cut.edge_index + Number(cut.fraction) === position);
          if (!draft.children.length) draft.children.push(childDraft(draft.observation.group));
          draft.children.splice(added + 1, 0, childDraft(draft.children[added].group));
        }
        draft.cuts = cuts;
      } else {
        const points = value.points;
        if (!Array.isArray(points) || points.length < 2 || points.length > 128 ||
            (pathRequest.mode === "straight" && points.length !== 2) || points.some((point) => !Array.isArray(point) || point.length !== 2 ||
              point.some((coordinate) => !Number.isFinite(coordinate) || coordinate < 0 || coordinate > 1)))
          throw new Error("Finish a supported centerline within the drawing bounds.");
        if (pathRequest.mode === "circular_arc" && points.length !== 3)
          throw new Error("Finish exactly three ordered circular arc anchors.");
        draft.geometry.points = clone(points);
      }
      pathRequest = null; pathDraftTarget = null; env.render(); return true;
    }
    function pickRegion(value) {
      const draft = familyEditing?.capture?.active ? familyEditing : editing?.capture?.active ? editing : splitting?.capture?.active ? splitting :
        refreshEditing?.captureDraft?.capture.active ? refreshEditing.captureDraft : supplementalCapture?.capture.active ? supplementalCapture : null;
      if (!draft) return false;
      controller.current(draft.snapshot);
      if (value.revision_id !== draft.observation.source.revision_id || value.index !== draft.observation.source.index)
        throw new Error("Select evidence on this correction’s original drawing.");
      invalidateGraphicReview(draft.owner);
      draft.capture.bbox = clone(value.bbox); draft.capture.active = false; env.pickRegion(false); env.render(); return true;
    }
    return { controller, render, pickLine, pathDraft, pickRegion, cancelPicking: stopPicking };
  }
  return { createController, createPanel, groupLabel, sourceHighlight };
});
