# Equipment count API 1

`equipment_count_save` values are exactly `{generation: null|string, request: <below>}`. Workflow envelope supplies version, actor and reason. Save explicitly reviews supplied source regions; no legacy tag becomes a physical assembly automatically. Each changed observation is reviewed in this atomic draft. No numeric total field is accepted.

```json
{
  "schema": "equipment-calculation-request-1",
  "binding": "copy opaque object from view.request.binding",
  "sources": [{"source":{"revision_id":"64hex","index":0,"sheet_id":"64hex","geometry_fingerprint":"64hex"},"role":"plan","artifact_sha256":"same as revision_id"}],
  "evidence": [{"id":"e1","source":"same full source object","bbox":[0.1,0.1,0.2,0.2],"kind":"graphic","text":"Source review description","artifact_sha256":"same as revision_id"}],
  "observations": [{"id":"o1","source":"same full source object","bbox":[0.1,0.1,0.2,0.2],"kind":"assembly","family":"pump","tag":"P-1","work_status":"new","disposition":"include","identity":"established","procurement":"unknown","installation":"unknown","parent_id":null,"system_id":null,"attributes":{},"evidence_ids":["e1"],"issues":[]}],
  "relations": [], "schedules": [],
  "scope":{"source_keys":[],"work_statuses":["new","existing","demolition","relocation","spare","unknown"],"required_attributes":[]},
  "coverage":{"state":"unknown","evidence_ids":[],"unresolved_requirements":[]}
}
```

Sources above are objects, not the illustrative string placeholders. Use `view.current_source_contexts` entries `{source,role,artifact_sha256,source_key}`; request contexts omit source_key. Add its source_key explicitly to scope.source_keys. Bbox is normalized displayed-page coordinates, x0< x1, y0< y1, all finite 0..1. Each evidence and observation must match a listed exact source context. Evidence kind: graphic, requirement, schedule, relationship, coverage, tag. Plan/detail/riser graphic evidence supports physical review; tag/schedule text does not. Source roles: plan, enlarged_plan, detail, riser, schedule, specification, legend, addendum, other.

Observation enums:
- kind: assembly, component, shipping, accessory, spare
- work_status: new, existing, demolition, relocation, spare, unknown
- disposition: include, exclude, unresolved
- identity: established, unresolved
- procurement: separate, included, none, unknown
- installation: field, factory, none, unknown
- family is required free text; tag/parent_id/system_id nullable text; attributes is bounded string:string map; issues and evidence_ids are string lists.

Relation shape is exactly `{id,kind,member_ids,each,evidence_ids,scope_text}`. Kinds same, distinct, multiplicity, relocation, system, package, tag_reuse. `each` is null except multiplicity/package explicit positive integer. Other kinds require at least two members except system/package may cover one. Multiplicity states total INCLUDING representative and all members; it must be at least the represented physical member count, no overlapping multiplicity. same/relocation deduplicate only with relationship/requirement/graphic supporting evidence. Scope text is required nonblank for multiplicity. Identity remains unresolved until explicitly established; distinct does not silently resolve tag conflict; tag_reuse is a separately evidence-backed tag resolution. Parent links retain child channels and never add child quantities to parent equipment count.

Schedule shape exactly `{id,family,work_status,member_ids,declared_each,evidence_ids}`; declared_each is nonnegative integer; members may be empty for schedule-only obligations; evidence must include kind schedule. Match to family/work status; wrong declared quantity cannot alter observed counts.

View: `{available,generation,fingerprint,stale,request,result,observations:[{observation,observation_sha256}],evidence,sources,current_source_contexts,history,decisions,issues}`. Even unavailable has editable empty request with binding; result null. Source verification/review failures use coded error messages. View does no model/render calls. Current rows carry `row_id,member_ids,family,tag,kind,work_status,system_id,attributes,physical_each,requested,procurement_each,installation_each,remove_each,reinstall_each,component_each,issues,dependency_sha256,source_keys`. Result groups carry `group_id,key:{family,work_status,system_id,attributes},row_ids,known_subtotal_each,total_each,complete,issues`; overall `known_subtotal_each,total_each,complete,declarations,issues,rows,groups,systems,procurement_packages`. Unknown channels are null; physical totals exclude components and uninstalled spares. No input means unavailable, not zero. Group summaries can remain current independently while overall total is incomplete.

Only save action is required for this milestone; adding/removing/correcting observations, links, schedules and coverage submits one full atomic draft. Persisted prior requests/results and review events remain immutable.

Scope subtotals are nullable when different explicit `attributes.alternate` choices would be incorrectly summed; each alternative keeps its own group. `phase` and `alternate` are always grouping attributes when present. Included-child procurement requires relationship/requirement evidence on the child; absent proof yields unknown procurement without discarding a supported parent physical count. Relocation must retain at least two distinct source locations. A system quantity remains unknown if any member's physical quantity or selected-scope membership is unresolved. Overlapping procurement-package members require reconciliation before save.
