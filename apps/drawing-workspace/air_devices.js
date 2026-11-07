"use strict";

// Counts and source admission belong to the persisted calculation. This panel
// submits evidence-bound reviews and never calculates or accepts a final total.
((root, factory) => {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.HeleosAirDevices = api;
})(typeof globalThis === "object" ? globalThis : this, () => {
  const list = (value) => Array.isArray(value) ? value : [];
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const fields = ["family", "type_tag", "system", "service", "work_status", "face_size", "neck_size", "opening_size", "assembly_length", "slot_count"];
  const families = ["diffuser", "register", "grille", "linear_diffuser", "mechanical_louver", "architectural_louver", "equipment", "accessory", "other"];
  const statuses = ["new_install", "existing_to_remain", "demolition", "relocated"];
  const depictions = ["physical", "legend", "schedule", "generic_detail", "tag_only", "unknown", "excluded"];
  const label = (value) => ({ type_tag: "Type tag", work_status: "Work scope", neck_size: "Neck / connection size",
    face_size: "Face size", opening_size: "Opening size", assembly_length: "Assembly length", slot_count: "Slots",
    new_install: "New installation", existing_to_remain: "Existing to remain", demolition: "Demolition / removal",
    relocated: "Relocated", not_supplied: "Not supplied", not_applicable: "Not applicable", tag_only: "Tag without physical symbol",
    generic_detail: "Generic detail illustration", physical: "Physical assembly", include: "Include", exclude: "Exclude",
    unresolved: "Needs review" })[value] || String(value || "Unknown").replaceAll("_", " ").replace(/^\w/, (c) => c.toUpperCase());
  function issueText(issue) {
    const code = typeof issue === "string" ? issue : issue.code;
    const message = typeof issue === "object" && issue ? issue.message : null;
    return ({ coverage_basis_stale: "Review drawing coverage after the latest correction.",
      coverage_state_not_complete: "Drawing coverage still needs review." })[code] ||
      (message && message !== code ? message : label(code));
  }
  const count = (value) => Number.isSafeInteger(value) && value >= 0 ? value + " each" : "Unknown";
  function dimensionText(value) {
    if (typeof value === "string") return value;
    if (value && typeof value.n === "string" && typeof value.d === "string" &&
        /^[0-9]+$/.test(value.n) && /^[1-9][0-9]*$/.test(value.d))
      return value.d === "1" ? value.n : value.n + "/" + value.d;
    return "Unknown";
  }
  function attributeText(attribute) {
    if (!attribute || attribute.state !== "known") return label(attribute?.state || "unknown");
    const value = attribute.value;
    if (value && typeof value === "object") {
      if (Array.isArray(value.dimensions)) return label(value.shape) + " · " + value.dimensions.map(dimensionText).join(" × ") + " " + value.unit;
      if (value.value != null) return dimensionText(value.value) + " " + value.unit;
      return "Unknown";
    }
    return typeof value === "string" ? label(value) : String(value);
  }
  function createController(env) {
    const attempted = new Set(), pending = new Set(), failures = new Map();
    const data = () => env.getView()?.air_device_takeoff || {};
    const jobs = () => list(env.getView()?.air_device_producer?.jobs);
    const observed = (entry) => entry?.observation || entry;
    function snapshot(entry) {
      return { generation: data().generation, fingerprint: data().fingerprint, ...(entry ? { observation_id: observed(entry).id,
        observation_sha256: entry.observation_sha256 } : {}) };
    }
    function current(draft, allowStale = false) {
      const now = data();
      const entry = draft.observation_id ? list(now.observations).find((item) => observed(item)?.id === draft.observation_id) : null;
      if (!draft.generation || now.generation !== draft.generation || now.fingerprint !== draft.fingerprint ||
          (!allowStale && now.stale === true) ||
          (draft.observation_id && (!draft.observation_sha256 || !entry || entry.observation_sha256 !== draft.observation_sha256)))
        throw new Error("The air-device results changed. Reopen this review using the current drawing results.");
      return entry;
    }
    const identity = (draft) => ({ generation: draft.generation, observation_id: draft.observation_id, observation_sha256: draft.observation_sha256 });
    function reason(value) {
      if (typeof value !== "string" || !value.trim()) throw new Error("Explain the source evidence for this review.");
      return value.trim();
    }
    async function prepare(id, retry = false) {
      if (env.isBusy?.() || !env.getActor?.()?.trim()) return false;
      if (jobs().find((job) => job.id === id)?.state !== "completed" || pending.has(id) ||
          list(data().prepared_reading_ids).includes(id) || (!retry && attempted.has(id))) return false;
      attempted.add(id); pending.add(id); failures.delete(id);
      try {
        const saved = await env.save("air_device_prepare", { reading_id: id }, "Calculated air-device counts from the completed drawing reading");
        if (!saved) attempted.delete(id);
        return Boolean(saved);
      } catch (error) { failures.set(id, error.message); throw error; }
      finally { pending.delete(id); }
    }
    return { snapshot, current, failures,
      active: () => jobs().some((job) => ["queued", "running"].includes(job.state)),
      retry: (id) => prepare(id, true),
      async reconcile() {
        let changed = false, failure = null;
        for (const job of jobs()) if (job.state === "completed") {
          try { changed = await prepare(job.id) || changed; }
          catch (error) { failure ||= error; }
        }
        if (failure) throw failure;
        return changed;
      },
      start(source) { return env.save("air_device_find", { source: clone(source) }, "Read air-device assemblies on the selected drawing with the configured local model"); },
      cancel(jobId) { return env.save("air_device_find_cancel", { job_id: jobId }, "Stopped the air-device drawing reading"); },
      review(draft, action, explanation) {
        current(draft);
        if (!["include", "exclude", "unresolved"].includes(action)) throw new Error("Choose how to review this occurrence.");
        return env.save("air_device_review", { ...identity(draft), action }, reason(explanation));
      },
      correct(draft, attributes, depiction, explanation, issues) {
        current(draft);
        if (!depictions.includes(depiction)) throw new Error("Choose what this drawing depicts.");
        return env.save("air_device_correct", { ...identity(draft), attributes: clone(attributes), depiction,
          ...(issues === undefined ? {} : { issues: clone(issues) }) }, reason(explanation));
      },
      scope(draft, values, explanation) {
        current(draft, true);
        if (!list(values.source_keys).length || !list(values.group_by).length || !list(values.work_statuses).length ||
            list(values.required_fields).some((field) => !values.group_by.includes(field)))
          throw new Error("Choose drawings, grouping fields and work scope; required fields must also be grouped.");
        return env.save("air_device_scope", { generation: draft.generation, ...clone(values) }, reason(explanation));
      },
      coverage(draft, state, explanation) {
        current(draft);
        if (!["complete", "partial", "unknown"].includes(state)) throw new Error("Choose the reviewed drawing coverage.");
        return env.save("air_device_coverage", { generation: draft.generation, state }, reason(explanation));
      },
      recalculate(draft) {
        current(draft, true);
        return env.save("air_device_recalculate", { generation: draft.generation }, "Recalculated air-device counts from current source evidence and saved reviews");
      },
      intake(draft, reading, action, explanation) {
        current(draft, true);
        const now = list(data().proposals?.readings).find((item) => item.reading_id === reading.reading_id);
        if (!now || now.reading_sha256 !== reading.reading_sha256 || now.state !== "pending")
          throw new Error("This saved reading changed. Reopen its current source review.");
        if (!(["discard", now.same_page ? "replace" : "add"].includes(action))) throw new Error("Choose how to use this drawing reading.");
        return env.save("air_device_intake", { generation: draft.generation, reading_id: reading.reading_id,
          reading_sha256: reading.reading_sha256, action }, reason(explanation));
      },
      proposal(draft, proposal, action, memberIds, explanation) {
        current(draft);
        const now = ["correspondences", "multiplicities", "relocations", "schedules"].flatMap((key) => list(data().proposals?.[key]))
          .find((item) => item.id === proposal.id);
        if (!now || now.sha256 !== proposal.sha256 || now.state !== "pending")
          throw new Error("This proposed relationship changed. Reopen its current source review.");
        if (!["accept", "reject"].includes(action)) throw new Error("Choose whether the cited relationship is supported.");
        return env.save("air_device_proposal", { generation: draft.generation, proposal_id: proposal.id,
          proposal_sha256: proposal.sha256, action, member_ids: clone(memberIds) }, reason(explanation));
      },
      relation(draft, kind, relation, record, explanation) {
        current(draft);
        const collection = { correspondence: "correspondences", multiplicity: "multiplicities", relocation: "relocations", schedule: "schedules" }[kind];
        if (!collection) throw new Error("Choose a supported source relationship.");
        if (relation.sha256 && list(data().relationships?.[collection]).find((entry) => entry.id === relation.id)?.sha256 !== relation.sha256)
          throw new Error("This saved relationship changed. Reopen its current review.");
        return env.save("air_device_relation", { generation: draft.generation, kind, relation_id: relation.id,
          relation_sha256: relation.sha256 || null, action: record ? "save" : "remove", record: record ? clone(record) : null }, reason(explanation));
      },
    };
  }

  function createPanel(env) {
    const doc = env.document, controller = createController(env);
    let editing = null, scopeDraft = null, coverageDraft = null, relationDraft = null;
    const proposalDrafts = new Map();
    const node = (tag, value = "", className = "") => {
      const element = doc.createElement(tag); element.textContent = value; element.className = className; return element;
    };
    function button(title, action, disabled = false) {
      const element = node("button", title, "button button-outline"); element.type = "button";
      element.disabled = disabled || Boolean(env.isBusy());
      element.addEventListener("click", async () => {
        if (element.disabled || env.isBusy()) return;
        try { await action(); } catch (error) { env.error(error.message); }
      });
      return element;
    }
    async function refresh(action) { if (await action) env.render(); }
    function details(target, title, open = false) {
      const element = node("details", "", "workflow-record"); element.open = open;
      element.append(node("summary", title)); target.append(element); return element;
    }
    function field(target, title, name, value, change, options) {
      const wrapper = node("label", title, "field-label"), input = doc.createElement(options ? "select" : "input");
      input.name = name;
      if (options) for (const entry of options) {
        const option = node("option", Array.isArray(entry) ? entry[1] : label(entry));
        option.value = Array.isArray(entry) ? entry[0] : entry; input.append(option);
      }
      else input.maxLength = 1000;
      input.value = value ?? "";
      input.addEventListener(options ? "change" : "input", () => change(input.value));
      wrapper.append(input); target.append(wrapper); return input;
    }
    function choices(target, title, name, options, selected, change) {
      const set = node("fieldset"), legend = node("legend", title); set.append(legend);
      for (const [value, title] of options) {
        const wrapper = node("label", title, "field-label"), input = doc.createElement("input");
        input.type = "checkbox"; input.name = name; input.value = value; input.checked = selected.includes(value);
        input.addEventListener("change", () => {
          const next = input.checked ? [...selected.filter((id) => id !== value), value] : selected.filter((id) => id !== value);
          selected.splice(0, selected.length, ...next); change(selected);
        });
        wrapper.append(input); set.append(wrapper);
      }
      target.append(set);
    }
    function showEvidence(target, evidence) {
      for (const entry of list(evidence)) {
        target.append(node("p", entry.text || "Drawing graphic", "source-excerpt"));
        if (entry.source) target.append(button(entry.text ? "Show source text" : "Show source graphic",
          () => env.showSource({ ...clone(entry.source), bbox: clone(entry.bbox) })));
      }
    }
    function relationshipSummary(target, kind, record, data) {
      if (!record) return;
      target.append(node("p", kind === "correspondences" ? "Physical correspondence: " + label(record.state) :
        kind === "multiplicities" ? "Supported group total: " + count(record.each) + " · " + (record.scope_text || "Scope unresolved") :
        kind === "relocations" ? "Proposed reuse with separate removal and reinstallation operations" :
        "Schedule-declared assemblies: " + count(record.declared_each)));
      for (const member of list(record.members)) {
        const id = typeof member === "string" ? member : member.observation_id;
        const wrapper = list(data.observations).find((entry) => (entry.observation || entry).id === id);
        const observation = wrapper?.observation || wrapper;
        target.append(node("p", "Named occurrence: " + id));
        if (observation?.source) target.append(button("Show relationship member · " + id,
          () => env.showSource({ ...clone(observation.source), bbox: clone(observation.bbox) })));
      }
      if (record.attributes) attributes(target, record.attributes);
      showEvidence(target, list(data.evidence).filter((entry) => list(record.evidence_ids).includes(entry.id)));
    }
    function proposals(target, data) {
      for (const reading of list(data.proposals?.readings)) {
        if (reading.state !== "pending") continue;
        const key = "reading:" + reading.reading_id;
        if (!proposalDrafts.has(key)) proposalDrafts.set(key, { pin: controller.snapshot(), record: clone(reading), reason: "", action: "" });
        const draft = proposalDrafts.get(key), panel = details(target, "Review saved air-device reading", true);
        const stale = draft.pin.generation !== data.generation || draft.pin.fingerprint !== data.fingerprint || draft.record.reading_sha256 !== reading.reading_sha256;
        if (stale) panel.append(node("p", "Saved results changed. Reopen this reading review before applying it.", "error"),
          button("Reopen reading review · " + reading.reading_id, () => { proposalDrafts.delete(key); env.render(); }));
        panel.append(node("p", reading.same_page ?
          "This reading covers an already retained page. Replacing it preserves the prior calculation in history and requires renewed source review." :
          "This reading adds a distinct drawing page. Confirm that it belongs in the takeoff scope.", "section-hint"));
        if (reading.source) panel.append(button("Show incoming air-device drawing", () => env.showSource(clone(reading.source))));
        field(panel, "Use this reading", "air-device-intake-action-" + reading.reading_id, draft.action, (value) => { draft.action = value; },
          [["", "Choose after reviewing the source"], [reading.same_page ? "replace" : "add", reading.same_page ? "Replace retained page reading" : "Add this drawing page"], ["discard", "Keep existing results; discard this reading"]]);
        field(panel, "Drawing review reason", "air-device-intake-reason-" + reading.reading_id, draft.reason, (value) => { draft.reason = value; });
        panel.append(button("Save reading review · " + reading.reading_id, () => refresh(controller.intake(draft.pin, draft.record, draft.action, draft.reason)), stale));
      }
      for (const kind of ["correspondences", "multiplicities", "relocations", "schedules"]) for (const proposal of list(data.proposals?.[kind])) {
        const panel = details(target, label(kind) + " · " + label(proposal.state), proposal.state === "pending");
        relationshipSummary(panel, kind, proposal.record, data);
        if (proposal.state !== "pending") continue;
        panel.append(node("p", "This model relationship is a proposal. Review the cited drawing evidence before accepting; matching tags or graphics alone do not establish physical identity.", "section-hint"));
        const key = "proposal:" + proposal.id;
        if (!proposalDrafts.has(key)) proposalDrafts.set(key, { pin: controller.snapshot(), record: clone(proposal), member_ids: [], reason: "", action: "" });
        const draft = proposalDrafts.get(key);
        const stale = draft.pin.generation !== data.generation || draft.pin.fingerprint !== data.fingerprint || draft.record.sha256 !== proposal.sha256;
        if (stale) panel.append(node("p", "Saved results changed. Reopen this relationship review before applying it.", "error"),
          button("Reopen relationship review · " + proposal.id, () => { proposalDrafts.delete(key); env.render(); }));
        if (kind === "schedules") choices(panel, "Physical occurrences supported by this schedule row", "air-device-schedule-members-" + proposal.id,
          list(data.observations).filter((entry) => (entry.observation || entry).depiction === "physical").map((entry) => {
            const value = entry.observation || entry;
            return [value.id, attributeText(value.attributes?.type_tag) + " · page " + (value.source.index + 1) + " · " + value.id];
          }), draft.member_ids, () => {});
        field(panel, "Relationship decision", "air-device-proposal-action-" + proposal.id, draft.action,
          (value) => { draft.action = value; }, [["", "Choose after reviewing evidence"], ["accept", "Accept source-supported relationship"], ["reject", "Reject proposed relationship"]]);
        field(panel, "Relationship review reason", "air-device-proposal-reason-" + proposal.id, draft.reason, (value) => { draft.reason = value; });
        panel.append(button("Save relationship review · " + proposal.id, () => {
          if (kind === "schedules" && draft.action === "accept" && !draft.member_ids.length)
            throw new Error("Select the physical occurrences supported by this schedule row.");
          return refresh(controller.proposal(draft.pin, draft.record, draft.action, draft.member_ids, draft.reason));
        }, stale));
      }
    }
    function beginRelation(kind, saved = null) {
      const record = saved?.record;
      relationDraft = { kind, pin: controller.snapshot(), identity: saved ? { id: saved.id, sha256: saved.sha256 } : { id: env.newId(), sha256: null },
        observations: clone(list(env.getView().air_device_takeoff?.observations)),
        member_ids: list(record?.members).map((member) => member.observation_id), evidence_ids: clone(list(record?.evidence_ids)),
        state: record?.state || "unresolved", each: record?.each == null ? "" : String(record.each),
        scope_text: record?.scope_text || "", representative: record?.representative || "",
        remove_members: clone(list(record?.remove_members)), reinstall_members: clone(list(record?.reinstall_members)),
        remove_operation_id: record?.remove_operation_id || env.newId(), reinstall_operation_id: record?.reinstall_operation_id || env.newId(),
        declared_each: record?.declared_each == null ? "" : String(record.declared_each), attributes: clone(record?.attributes || {}), reason: "" };
      env.render();
    }
    function manualRelations(target, data) {
      const collectionNames = { correspondence: "correspondences", multiplicity: "multiplicities", relocation: "relocations", schedule: "schedules" };
      const panel = details(target, "Source-supported physical relationships");
      panel.append(node("p", "Link named drawing occurrences only when the cited sources establish their relationship. A note's group total is evidence, not a manually entered takeoff total.", "section-hint"));
      for (const [kind, collection] of Object.entries(collectionNames)) {
        panel.append(button(({ correspondence: "Review correspondence between drawings", multiplicity: "Review a scoped quantity note",
          relocation: "Review a relocated assembly", schedule: "Link a schedule declaration" })[kind], () => beginRelation(kind)));
        for (const saved of list(data.relationships?.[collection])) {
          const entry = details(panel, "Saved " + label(kind).toLowerCase() + " · " + saved.id);
          relationshipSummary(entry, collection, saved.record, data);
          entry.append(button("Correct relationship · " + saved.id, () => beginRelation(kind, saved)));
        }
      }
      if (!relationDraft) return;
      const draft = relationDraft, editor = details(target, "Review " + label(draft.kind).toLowerCase() + " evidence", true);
      let stale = false;
      try { controller.current(draft.pin); } catch (error) { stale = true; editor.append(node("p", error.message, "error")); }
      const members = list(draft.observations).map((entry) => entry.observation || entry);
      choices(editor, "Named drawing occurrences", "air-device-relation-member", members.map((entry) =>
        [entry.id, attributeText(entry.attributes?.type_tag) + " · " + label(entry.depiction) + " · page " + (entry.source.index + 1) + " · " + entry.id]),
      draft.member_ids, () => env.render());
      if (draft.kind === "correspondence") field(editor, "Physical relationship", "air-device-relation-state", draft.state,
        (value) => { draft.state = value; }, [["unresolved", "Uncertain; keep affected count unresolved"], ["same", "Depictions of the same assembly"], ["distinct", "Distinct physical assemblies"]]);
      if (draft.kind === "multiplicity") {
        field(editor, "Total explicitly stated by the note", "air-device-relation-each", draft.each, (value) => { draft.each = value; });
        field(editor, "Represented room, view or level scope", "air-device-relation-scope", draft.scope_text, (value) => { draft.scope_text = value; });
        field(editor, "Representative occurrence included in that total", "air-device-relation-representative", draft.representative,
          (value) => { draft.representative = value; }, [["", "Choose a named occurrence"], ...draft.member_ids.map((id) => [id, id])]);
        editor.append(node("p", "The note's total already includes its representative and named drawn members. Bare TYP does not supply a multiplier.", "section-hint"));
      }
      if (draft.kind === "relocation") {
        editor.append(node("p", "Identify old and new depictions of one explicitly reused assembly. This records separate removal and reinstallation operations, with no new purchase inferred.", "section-hint"));
        for (const id of draft.member_ids) field(editor, "Operation for " + id, "air-device-relocation-side-" + id,
          draft.remove_members.includes(id) ? "remove" : draft.reinstall_members.includes(id) ? "reinstall" : "",
          (value) => {
            draft.remove_members = draft.remove_members.filter((member) => member !== id);
            draft.reinstall_members = draft.reinstall_members.filter((member) => member !== id);
            if (value === "remove") draft.remove_members.push(id);
            if (value === "reinstall") draft.reinstall_members.push(id);
          }, [["", "Choose the supported operation"], ["remove", "Old location: remove"], ["reinstall", "New location: reinstall reused assembly"]]);
      }
      if (draft.kind === "schedule") {
        field(editor, "Quantity explicitly stated by the schedule", "air-device-schedule-declared", draft.declared_each,
          (value) => { draft.declared_each = value; });
        attributes(editor, draft.attributes);
        editor.append(node("p", "This declaration is compared with the observed physical assemblies; it never supplies missing instances. Preserved schedule attributes retain their source evidence.", "section-hint"));
      }
      choices(editor, "Evidence establishing this relationship", "air-device-relation-evidence", list(data.evidence).map((entry) =>
        [entry.id, (entry.text || "Drawing graphic") + " · page " + (entry.source.index + 1)]), draft.evidence_ids, () => {});
      showEvidence(editor, list(data.evidence).filter((entry) => draft.evidence_ids.includes(entry.id)));
      field(editor, "Relationship reason", "air-device-relation-reason", draft.reason, (value) => { draft.reason = value; });
      editor.append(button("Save source-supported relationship", async () => {
        const minimum = ["correspondence", "relocation"].includes(draft.kind) ? 2 : 1;
        if (draft.member_ids.length < minimum || !draft.evidence_ids.length) throw new Error("Choose the named occurrences and supporting source evidence.");
        const record = { id: draft.identity.id, members: draft.member_ids.map((id) => {
          const entry = draft.observations.find((value) => (value.observation || value).id === id);
          return { observation_id: id, observation_sha256: entry.observation_sha256 };
        }), evidence_ids: clone(draft.evidence_ids) };
        if (draft.kind === "correspondence") record.state = draft.state;
        if (draft.kind === "multiplicity") {
          if (!/^[1-9][0-9]*$/.test(draft.each) || Number(draft.each) > 1000000 || !draft.scope_text.trim() || !draft.member_ids.includes(draft.representative))
            throw new Error("Use the supported positive whole-number note, its explicit scope and a representative within the named group.");
          Object.assign(record, { each: Number(draft.each), representative: draft.representative, scope_text: draft.scope_text });
        }
        if (draft.kind === "relocation") {
          const remove = draft.remove_members.filter((id) => draft.member_ids.includes(id));
          const reinstall = draft.reinstall_members.filter((id) => draft.member_ids.includes(id));
          if (!remove.length || !reinstall.length || remove.length + reinstall.length !== draft.member_ids.length)
            throw new Error("Assign every named depiction to an old removal or new reinstallation location.");
          Object.assign(record, { remove_members: remove, reinstall_members: reinstall, remove_operation_id: draft.remove_operation_id,
            reinstall_operation_id: draft.reinstall_operation_id, reused: true });
        }
        if (draft.kind === "schedule") {
          if ((draft.declared_each && !/^(0|[1-9][0-9]*)$/.test(draft.declared_each)) || Number(draft.declared_each) > 1000000 ||
              (!draft.declared_each && !Object.keys(draft.attributes).length)) throw new Error("Enter the schedule's supported whole-number declaration or retain its source-backed attributes.");
          Object.assign(record, { declared_each: draft.declared_each === "" ? null : Number(draft.declared_each), attributes: clone(draft.attributes) });
        }
        if (await controller.relation(draft.pin, draft.kind, draft.identity, record, draft.reason)) { relationDraft = null; env.render(); }
      }, stale));
      if (draft.identity.sha256) editor.append(button("Remove saved relationship", async () => {
        if (await controller.relation(draft.pin, draft.kind, draft.identity, null, draft.reason)) { relationDraft = null; env.render(); }
      }, stale));
      editor.append(button("Cancel relationship review", () => { relationDraft = null; env.render(); }));
    }
    function attributes(target, values) {
      const content = node("dl", "", "evidence-details");
      for (const field of fields) if (values?.[field]) content.append(node("dt", label(field)), node("dd", attributeText(values[field])));
      target.append(content);
    }
    function startReview(entry) {
      editing = { pin: controller.snapshot(entry), observation: clone(entry.observation || entry),
        attributes: clone((entry.observation || entry).attributes), issues: clone(list((entry.observation || entry).issues)),
        action: entry.decision?.action || "unresolved", reason: "" };
      env.render();
    }
    function occurrence(target, entry, data) {
      const observation = entry.observation || entry;
      target.append(node("p", attributeText(observation.attributes?.type_tag) + " · " + label(observation.depiction)),
        button("Show on drawing", () => env.showSource({ ...clone(observation.source), bbox: clone(observation.bbox) })),
        button("Review occurrence", () => startReview(entry)));
      for (const issue of list(observation.issues)) target.append(node("p", label(issue), "error"));
      showEvidence(target, list(data.evidence).filter((evidence) => list(observation.evidence_ids).includes(evidence.id)));
    }
    function corrections(target, data) {
      if (!editing) return;
      const draft = editing, panel = details(target, "Review air-device occurrence", true);
      let stale = false;
      try { controller.current(draft.pin); } catch (error) { stale = true; panel.append(node("p", error.message, "error")); }
      panel.append(node("p", "Check the original drawing, correct supported attributes, and explain your decision. A review cannot replace missing physical evidence.", "section-hint"));
      panel.append(button("Show occurrence being reviewed", () => env.showSource({ ...clone(draft.observation.source), bbox: clone(draft.observation.bbox) })));
      field(panel, "What the drawing depicts", "air-device-depiction", draft.observation.depiction,
        (value) => { draft.observation.depiction = value; }, depictions);
      const available = list(data.evidence);
      for (const key of fields) {
        const entry = draft.attributes[key]; if (!entry) continue;
        const section = details(panel, label(key) + " · " + attributeText(entry));
        field(section, "Source support", "air-device-" + key + "-state", entry.state, (value) => {
          entry.state = value;
          if (value !== "known") entry.value = null;
          else if (entry.value == null) entry.value = ["face_size", "neck_size", "opening_size"].includes(key) ?
            { shape: "rectangular", dimensions: ["", ""], unit: "in", original_text: "" } : key === "assembly_length" ?
              { value: "", unit: "ft", original_text: "" } : "";
          env.render();
        }, ["known", "unknown", "not_supplied", "not_applicable"]);
        if (entry.state === "known") {
          if (["face_size", "neck_size", "opening_size"].includes(key)) {
            field(section, "Shape", "air-device-" + key + "-shape", entry.value.shape, (value) => {
              entry.value.shape = value; entry.value.dimensions = value === "round" ? [entry.value.dimensions[0] || ""] :
                [entry.value.dimensions[0] || "", entry.value.dimensions[1] || ""]; env.render();
            }, ["rectangular", "round", "oval"]);
            entry.value.dimensions.forEach((value, index) => field(section, "Dimension " + (index + 1), "air-device-" + key + "-dimension-" + index,
              value, (next) => { entry.value.dimensions[index] = next; }));
          } else if (key === "assembly_length") field(section, "Stated length", "air-device-assembly-length", entry.value.value,
            (value) => { entry.value.value = value; });
          else field(section, label(key), "air-device-" + key + "-value", entry.value, (value) => {
            entry.value = key === "slot_count" && /^[1-9][0-9]*$/.test(value) ? Number(value) : value;
          }, key === "family" ? [["", "Choose a family"], ...families] : key === "work_status" ? [["", "Choose work scope"], ...statuses] : null);
          if (["face_size", "neck_size", "opening_size", "assembly_length"].includes(key)) {
            field(section, "Source unit", "air-device-" + key + "-unit", entry.value.unit, (value) => { entry.value.unit = value; }, ["in", "ft", "mm", "m"]);
            field(section, "Original size or length text", "air-device-" + key + "-text", entry.value.original_text,
              (value) => { entry.value.original_text = value; });
          }
        }
        choices(section, "Cited drawing evidence", "air-device-" + key + "-evidence",
          available.map((item) => [item.id, item.text || "Graphic · " + item.id]), entry.evidence_ids, () => {});
      }
      field(panel, "Occurrence decision", "air-device-review-action", draft.action, (value) => { draft.action = value; }, ["include", "exclude", "unresolved"]);
      if (list(draft.observation.issues).length) {
        panel.append(node("p", "Keep unresolved source issues checked. Clear an issue only after checking its source, then save the supported correction with your reason.", "section-hint"));
        choices(panel, "Source issues still unresolved", "air-device-unresolved-issue", draft.observation.issues.map((issue) =>
          [issue, label(issue)]), draft.issues, () => {});
      }
      field(panel, "Review reason", "air-device-review-reason", draft.reason, (value) => { draft.reason = value; });
      panel.append(button("Save attribute correction", async () => {
        if (await controller.correct(draft.pin, draft.attributes, draft.observation.depiction, draft.reason, draft.issues)) { editing = null; env.render(); }
      }, stale), button("Save occurrence decision", async () => {
        if (await controller.review(draft.pin, draft.action, draft.reason)) { editing = null; env.render(); }
      }, stale), button("Cancel air-device review", () => { editing = null; env.render(); }));
      showEvidence(panel, available);
    }
    function scopeReview(target, data) {
      if (!scopeDraft) return;
      const draft = scopeDraft, panel = details(target, "Review air-device scope", true);
      let stale = false;
      try { controller.current(draft.pin, true); } catch (error) { stale = true; panel.append(node("p", error.message, "error")); }
      choices(panel, "Drawings included in this count", "air-device-scope-source", list(data.sources).filter((entry) =>
        ["plan", "enlarged_plan", "detail"].includes(entry.role) || draft.values.source_keys.includes(entry.key)).map((entry) =>
        [entry.key, (entry.label || label(entry.role)) + " · page " + (entry.source.index + 1) + (entry.current === false ? " · source unavailable" : "")]), draft.values.source_keys, () => {});
      choices(panel, "Group counts by", "air-device-scope-group", fields.map((key) => [key, label(key)]), draft.values.group_by, () => {});
      choices(panel, "Required before a group can be complete", "air-device-scope-required", fields.map((key) => [key, label(key)]), draft.values.required_fields, () => {});
      choices(panel, "Requested work scope", "air-device-scope-status", statuses.map((key) => [key, label(key)]), draft.values.work_statuses, () => {});
      field(panel, "Scope review reason", "air-device-scope-reason", draft.reason, (value) => { draft.reason = value; });
      panel.append(button("Save air-device scope", async () => {
        if (await controller.scope(draft.pin, draft.values, draft.reason)) { scopeDraft = null; env.render(); }
      }, stale), button("Cancel scope review", () => { scopeDraft = null; env.render(); }));
    }
    function coverageReview(target) {
      if (!coverageDraft) return;
      const draft = coverageDraft, panel = details(target, "Review drawing coverage", true);
      let stale = false;
      try { controller.current(draft.pin); } catch (error) { stale = true; panel.append(node("p", error.message, "error")); }
      panel.append(node("p", "Complete means you reviewed every selected drawing and resolved its target instances and requirements. An empty detector response does not establish zero devices.", "section-hint"));
      field(panel, "Drawing coverage", "air-device-coverage-state", draft.state, (value) => { draft.state = value; },
        [["unknown", "Not reviewed"], ["partial", "Some scope remains"], ["complete", "All selected drawing scope reviewed"]]);
      field(panel, "Coverage review reason", "air-device-coverage-reason", draft.reason, (value) => { draft.reason = value; });
      panel.append(button("Save coverage review", async () => {
        if (await controller.coverage(draft.pin, draft.state, draft.reason)) { coverageDraft = null; env.render(); }
      }, stale), button("Cancel coverage review", () => { coverageDraft = null; env.render(); }));
    }
    function render(target) {
      const view = env.getView(), data = view.air_device_takeoff || {}, producer = view.air_device_producer || {};
      target.append(node("h3", "Air-device counts"), node("p", "Read the selected drawing, then review exceptions and their original sources. Counts are physical assemblies in each; sizes, slots and linear lengths remain separate attributes.", "section-hint"));
      const selection = env.getSelection(), selected = list(view.inventory).find((page) => page.revision_id === selection?.revision && page.index === selection?.index);
      const contextReading = ["schedule", "legend"].includes(selected?.assignment?.role);
      const eligible = selected && ["plan", "detail", "schedule", "legend"].includes(selected.assignment?.role) &&
        (!contextReading || Boolean(data.available && data.generation));
      if (!producer.configured) target.append(node("p", "Configure a local drawing model to find air devices. Saved results remain available.", "section-hint"));
      if (contextReading && !data.available) target.append(node("p", "Read a plan or detail drawing first, then read this schedule or legend as supporting evidence.", "section-hint"));
      else if (!eligible) target.append(node("p", "Open a current plan, detail, schedule or legend drawing assigned in Documents.", "section-hint"));
      else target.append(node("p", "Selected drawing: " + (selected.assignment?.label || selected.document_name || "Drawing") + " · page " + (selected.index + 1)));
      if (contextReading) target.append(node("p", "Schedule and legend readings supply supporting attributes and source context. They do not create installed assemblies or certify drawing coverage.", "section-hint"));
      const readTitle = contextReading ? "Read selected " + selected.assignment.role + " for air-device evidence" : "Find air devices on selected drawing";
      target.append(button(readTitle, () => {
        const now = env.getSelection();
        const page = list(env.getView().inventory).find((item) => item.revision_id === now?.revision && item.index === now?.index);
        if (!page || !["plan", "detail", "schedule", "legend"].includes(page.assignment?.role)) throw new Error("Choose a current plan, detail, schedule or legend drawing.");
        if (["schedule", "legend"].includes(page.assignment.role) && !env.getView().air_device_takeoff?.available)
          throw new Error("Read a plan or detail drawing before adding schedule or legend evidence.");
        return refresh(controller.start({ revision_id: page.revision_id, index: page.index }));
      }, !producer.configured || !eligible || controller.active()));
      for (const job of list(producer.jobs).slice().reverse()) {
        const active = ["queued", "running"].includes(job.state);
        const detail = details(target, label(job.state) + " air-device reading · page " + (Number.isInteger(job.source?.index) ? job.source.index + 1 : "Unknown"), active);
        if (job.source) detail.append(button("Show air-device reading source", () => env.showSource(clone(job.source))));
        if (job.error) detail.append(node("p", job.error.message || "The drawing reading could not finish.", "error"));
        if (active) detail.append(button("Stop air-device reading", () => refresh(controller.cancel(job.id))));
        if (controller.failures.has(job.id)) detail.append(node("p", controller.failures.get(job.id), "error"),
          button("Retry counts from saved reading", () => refresh(controller.retry(job.id))));
      }
      const result = data.result;
      proposals(target, data);
      if (!result) { target.append(node("p", "Air-device quantity is unknown until a drawing reading is available and its scope is reviewed.", "section-hint")); return; }
      if (data.stale) target.append(node("p", "Some source or review dependencies changed. Recalculate to save a current result; earlier quantities remain in history.", "error"));
      target.append(node("strong", !data.stale && result.complete && result.total_each != null ? "Selected scope total: " + count(result.total_each) : "Selected scope total: Unknown · incomplete"),
        node("p", "Known subtotal in requested work scope: " + count(result.known_subtotal_each)),
        node("p", "Known physical assemblies across work scopes: " + count(result.physical_known_each)));
      for (const message of new Set([...list(data.issues), ...list(result.issues)].map(issueText)))
        target.append(node("p", message, "error"));
      for (const group of list(result.groups)) {
        const entry = details(target, "Air-device count group", true);
        const key = node("dl", "", "evidence-details");
        for (const value of list(group.key)) key.append(node("dt", label(value.field)), node("dd", attributeText(value)));
        entry.append(key);
        entry.append(node("strong", !data.stale && group.complete && group.total_each != null ? count(group.total_each) :
          count(group.known_subtotal_each) + " known subtotal · final unknown"));
        for (const issue of list(group.issues)) entry.append(node("p", issueText(issue), "error"));
      }
      const rows = details(target, "Physical assemblies and exceptions · " + list(result.rows).length, true);
      const displayed = new Set();
      for (const row of list(result.rows)) {
        const entry = details(rows, "Physical assembly · " + count(row.physical_each));
        attributes(entry, row.imperial_attributes || row.attributes);
        entry.append(node("p", row.requested === true ? "In requested work scope" : row.requested === false ? "Outside requested work scope" : "Work scope unresolved"));
        if (row.operations) entry.append(node("p", "Removal operations: " + count(row.operations.remove) + " · Reinstallation operations: " + count(row.operations.reinstall)));
        if (row.new_purchase_each != null) entry.append(node("p", "New-purchase assemblies: " + count(row.new_purchase_each)));
        for (const issue of list(row.issues)) entry.append(node("p", issueText(issue), "error"));
        for (const id of list(row.member_ids)) {
          const item = list(data.observations).find((candidate) => (candidate.observation || candidate).id === id);
          if (!item) continue;
          displayed.add(id); occurrence(entry, item, data);
        }
      }
      for (const item of list(data.observations)) if (!displayed.has((item.observation || item).id)) {
        const entry = details(rows, "Other source observation · " + (item.observation || item).id);
        occurrence(entry, item, data);
      }
      for (const declaration of list(result.declarations)) {
        const entry = details(target, "Schedule declaration · " + (declaration.id || "Saved declaration"));
        entry.append(node("p", "Observed assemblies: " + count(declaration.observed_each)), node("p", "Schedule-declared assemblies: " + count(declaration.declared_each)),
          node("p", "Schedule comparison: " + label(declaration.state)),
          node("p", "A schedule declaration does not supply physical instances or drawing coverage.", "section-hint"));
        for (const issue of list(declaration.issues)) entry.append(node("p", issueText(issue), "error"));
      }
      const pin = controller.snapshot();
      manualRelations(target, data);
      target.append(button("Review air-device scope", () => {
        scopeDraft = { pin, values: clone({ source_keys: data.scope.source_keys, group_by: data.scope.group_by,
          required_fields: data.scope.required_fields, work_statuses: data.scope.work_statuses }), reason: "" }; env.render();
      }), button("Review drawing coverage", () => {
        coverageDraft = { pin, state: data.coverage?.state || "unknown", reason: "" }; env.render();
      }), button("Recalculate air-device counts", () => refresh(controller.recalculate(pin))));
      corrections(target, data); scopeReview(target, data); coverageReview(target);
      const history = details(target, "Saved air-device calculation history · " + list(data.history).length);
      for (const saved of list(data.history).slice().reverse()) history.append(node("p",
        (saved.reason || "Saved calculation") + " · " + (saved.actor || "Recorded reviewer") + " · " +
        (saved.result?.complete ? count(saved.result.total_each) : "Final unknown") + " · known subtotal " + count(saved.result?.known_subtotal_each)));
    }
    return { render, reconcile: () => controller.reconcile(), active: () => controller.active(), controller };
  }
  return { createPanel, createController, attributeText };
});
