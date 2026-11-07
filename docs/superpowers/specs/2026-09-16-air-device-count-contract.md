# Source-bound air-device calculation contract

AIR-DEVICE-COUNT-1 engineering contract under approved A01–A12. This file defines
implementation shapes, not new counting policy. The approved packet/receipt and
literal examples remain authoritative. The first kernel implements deterministic
calculation; source capture, persisted append-only workflow history, UI and export
are subsequent connected work and remain outstanding until separately verified.

## Pure kernel

`scripts/air_device_calculation.py`, Python 3.9+ standard library, may import only
the local `air_device_attributes.py` and `air_device_rules.py` using an explicit
sibling-path loader. No scale module, inference, network, database or source-image
read. Policy verification through `air_device_rules.load_binding()` is required.

Public API:

- `AirDeviceCalculationError(ValueError)` with readable message and string code.
- `VERSION = "air-device-calculation-1"`.
- `fingerprint(value)` canonical SHA-256 of sorted-key, compact, UTF-8 finite JSON;
  reject non-JSON values, non-string keys, Unicode surrogates, excessive nesting
  (>24), oversized collections (>20000), strings (>32768), integers beyond 64-bit
  signed range and payloads above 32 MiB. Do not use arbitrary object coercion.
- `validate_observation(value)` validates a proposal and returns a deep copy.
- `coverage_basis(request)` returns the fingerprint of the semantically ordered
  request excluding `coverage`; it does not certify completeness. No dependency
  includes scale. It accepts the same otherwise-valid request with coverage
  omitted so a caller can construct the decision.
- `calculate(request)` validates all inputs and returns the deterministic result
  below without mutating the request. Caller persists the exact request/result.

All records use exact key sets. IDs are unique within each namespace, nonempty
plain text up to 160 characters; no control characters or surrogates. Hashes are
64 lowercase hex. Collections are lists, normally at most 2000, relationship
members/evidence refs at most 128. No duplicate IDs or references. Quantities are
integers, never bool, at most 1000000. Bounding boxes are lists of four finite
JSON numbers in normalized coordinates `[x0,y0,x1,y1]`, strictly positive area
inside `[0,1]`; bool is invalid. Source tuples have exactly:

```
{revision_id: SHA, index: nonnegative integer <= 4294967295,
 sheet_id: SHA, geometry_fingerprint: SHA}
```

Request:

```
{schema: "air-device-calculation-request-1", binding,
 scope, sources, evidence, observations, decisions,
 correspondences, multiplicities, relocations, schedules, coverage}
```

`binding` must equal the verified `air_device_rules.load_binding()` output.
`sources` holds currently available source contexts, each exactly
`{source, artifact_sha256, role}`. Role is plan, enlarged_plan, detail, legend,
schedule, specification, catalog or other. `fingerprint(source)` is its key.
Duplicate source keys are invalid. Observation sources absent from this current
set are retained as stale and cannot contribute current quantities.

`scope` is exactly `{id, version, source_keys, group_by, required_fields,
work_statuses}`. Version is positive integer; source_keys is a nonempty unique
list selecting exact source identities. Missing selected contexts are retained
as stale scope, never dropped or automatically replaced by matching sheet/index.
Present selected contexts must have plan/enlarged_plan/detail roles. Unavailable
selected sources make final coverage incomplete even with zero observations.
group_by is a nonempty
unique list drawn from the attribute helper's FIELDS; required_fields is a unique
subset of group_by. work_statuses is a nonempty unique list from new_install,
existing_to_remain, demolition and relocated. Missing status is not a requested
status and cannot silently enter or leave the requested total. Scope is versioned
in history. Default UI grouping will require supported family, qualified size
roles actually requested, and work_status, without universally requiring a tag
or system. A requested system breakdown explicitly requires system.

Evidence record:

```
{id, source, bbox, kind: "graphic"|"text", text, artifact_sha256}
```

Text is null for graphic evidence, nonempty plain text <=4000 for text evidence.
Evidence can refer to retained stale sources, but is current only if its exact
source and artifact hash match a current context. It never establishes physical
identity merely through matching pixels. The project adapter verifies original
PDF/image retention and geometry before calling the kernel.

Observation:

```
{schema: "air-device-observation-1", id, source, bbox,
 depiction: "physical"|"legend"|"schedule"|"generic_detail"|"tag_only"|
            "unknown"|"excluded",
 attributes, evidence_ids, issues}
```

attributes is the ten-field helper contract. Attribute refs must be a subset of
the observation's nonempty evidence_ids, and all refs must exist in the request.
issues is a unique list of bounded strings. A supported physical observation
needs graphic evidence with exactly its source and bbox, current source/image
identity and a selected eligible source role. Nonselected observations are
preserved as outside scope. Legends, schedules, catalog views, generic details
and confirmed excluded items contribute no installed assembly.

Decision record:

```
{observation_id, observation_sha256, action: "include"|"exclude"|"unresolved",
 reason, evidence_ids}
```

At most one current decision per observation. The hash must pin that exact
observation; a mismatch is stale, not approval of new bytes. Missing/unresolved
decision prevents a known contribution. Exclusion requires current evidence and
a nonempty reason; inclusion still cannot override class/source/evidence rules.
Project validation may supply supported initial admissions automatically; the
user reviews exceptions, rather than approving every physical instance manually.

Known eligible families are diffuser, register, grille, linear_diffuser and
mechanical_louver. Architectural louvers, equipment, accessories and other have
explicit excluded/routed status. Unknown family/depiction/tag-only is unresolved.
A missing type_tag by itself never prevents a supported family/size count.
Length and slots remain attributes and never become quantities.

## Explicit relationships

Every membership is a list of `{observation_id, observation_sha256}` bindings,
never just matching tags. Every record has nonempty evidence_ids. Referenced IDs
must exist; stale member hashes make the affected relationship unresolved and
cannot revive an old admission. Relationship evidence must be current. Invalid
structural input raises an error; supported-but-unresolved meaning yields a
visible issue and incomplete result.

Correspondence: `{id, members, state, evidence_ids}`, two or more members;
state same, distinct or unresolved. Verified same collapses physical depictions;
distinct does not merge. Unresolved/stale correspondence removes all affected
members from the known subtotal. Contradictory same/distinct relations remain
unresolved. Do not infer correspondence from tag, overlap or raster equality.

Multiplicity: `{id, members, representative, each, scope_text, evidence_ids}`,
with at least one member.
representative is a member observation ID; each is positive integer; scope_text
is nonempty plain text <=1000 identifying the represented room/view/level group.
At least one supporting current text evidence is required. The explicitly named
members are covered by the total, including the representative; do not add them
again. First collapse verified same depictions, then compare physical members.
If the total is less than supported distinct members, or active multiplicity
groups overlap, the affected quantity remains unresolved. Bare TYP is never
turned into such a record by the source adapter.

Relocation: `{id, members, remove_members, reinstall_members,
remove_operation_id, reinstall_operation_id, reused, evidence_ids}`. Members
include at least two bound depictions. remove_members and reinstall_members are
nonempty, disjoint lists of member IDs whose union covers members; each operation
has a distinct globally unique operation ID. `reused` must be true for this
verified-reuse record. Old/new locations must be distinct source/bbox identities.
Current supporting evidence and exact membership establish one physical assembly,
remove1, reinstall1 and new_purchase0. Matching type tags alone never creates
this record. Missing/invalid support leaves the affected relationship unresolved.
The verified relocation's work_status is relocated even when removal and
reinstallation depictions have different source statuses; that expected status
difference is not an attribute conflict. Preserve original statuses and operation
sides through the member records. Other conflicting attributes remain exceptions.
Overlapping independent relocation records conflict. A multiplicity/relocation
overlap without explicit operation multiplicity remains unresolved rather than
inventing operation counts. This limitation must stay visible as outstanding
combined-case support; simple A05/A10 are required.

Schedule: `{id, members, declared_each, attributes, evidence_ids}`. declared_each
is null or a nonnegative integer. attributes is a partial mapping of helper
FIELDS to attribute records, with at least one field or a declared quantity.
At least one member is required. An unlinked declaration remains a scope-level
unresolved requirement until applicability is established; it cannot silently
reconcile an empty observed group. All refs must exist and be included in
schedule evidence_ids. Supporting current
text evidence must come from a schedule source. This explicit binding may supply
known attributes to linked observations missing them; it never supplies physical
instances or coverage. Different known physical/schedule values cause a conflict;
retain source values and do not silently choose a winner. Multiple contradictory
schedule links are also incomplete. Canonical exact values decide equivalence.
The declared quantity compares with the distinct supported physical quantity of
the complete named member group (after correspondence/multiplicity); partial
accounting-group membership cannot support a complete reconciliation. Preserve
observed and declared separately. Conflict leaves supported physical subtotal
intact but blocks affected finality. Stale schedule evidence cannot fill attrs.

## Coverage and result

coverage is exactly `{state, basis_sha256, evidence_ids, unresolved_requirements}`.
state complete, partial or unknown. basis_sha256 is null or a SHA. Complete
requires exact current `coverage_basis(request)`, no unresolved requirements,
and current evidence covering every selected source. It is a separately recorded
scope-review decision; the model cannot issue it. Empty detector results alone
produce unknown final quantity, while current explicitly complete empty scope
produces zero. Coverage invalidity does not discard known physical subtotals.
Here "covering" means at least one current cited witness for each exact selected
source key under that recorded review. It does not mean rectangle-union coverage
or prove the page complete from a graphic's bounds. The adapter records an
explicit reviewed scope-completeness decision with actor/reason and these pins.

Return schema `air-device-calculation-result-1` with VERSION, request_sha256,
binding, scope, rows, groups, declarations, issues, complete, known_subtotal_each,
total_each and physical_known_each. No final total supplied by the caller.

Rows are accounting units containing member_ids, deterministic row_id, exact
dependency_sha256, qualified attributes, source/evidence references, issue codes,
physical_each (integer or null), requested (true/false/null), operations
`{remove, reinstall}` and new_purchase_each (0 for verified reuse, otherwise null).
Related depictions/covered members contribute only once. Unknown physical
identity has null physical_each and contributes nothing to the known subtotal;
it is not a zero-device claim. Known physical identity with missing required
attributes retains its quantity and incomplete grouping. Merge complementary
known attrs only when supported; conflicting values stay unresolved and preserve
original observation IDs for review. Proven relocation uses work_status relocated.

Rows must be stable under input list reordering. Row dependency hashes include
consumed observation/admission/relationship/evidence/source bytes, scope, binding
and helper/kernel implementation identity, excluding unrelated rows and scale.
Changing/withdrawing a relationship invalidates its old and new connected members.
Set-like lists are canonically sorted before coverage/dependency hashing:
records by their IDs (sources by source key, decisions by observation_id), member
bindings by observation_id, evidence/issue refs, source_keys, required_fields,
work_statuses and removal/reinstall member IDs lexically. Keep dimension order,
group_by presentation order and approved-rule/example sequence intact. Canonical
`fingerprint` itself does not reorder arbitrary arrays. Row IDs derive only from
sorted accounting member IDs; attributes and mutable dependencies belong in the
separate dependency_sha256. Implementation identity hashes the exact kernel,
attribute helper and rule-loader bytes; reading these code bytes is permitted.
Repeated calculation of identical inputs returns identical output; a correction
changes affected row dependencies and preserves unrelated ones. The project
adapter stores prior requests/results append-only and deduplicates command replay.

Groups use exact canonical attributes for requested group_by, including distinct
unknown/not_supplied/not_applicable states. Each group exposes member row IDs,
known_subtotal_each, total_each, complete and issues. A required field is satisfied
by known or explicitly evidenced not_applicable; otherwise its final group is
incomplete. Attribute/grouping conflicts preserve supported physical_each; null
physical_each only when physical identity, source support, admission or eligible
class is unresolved. Do not create universal system/tag requirements. Scope coverage affects
final completeness; row dependencies remain independently comparable.

known_subtotal_each sums supported rows in requested known statuses. Other status
quantities stay visible in groups/rows. physical_known_each includes supported
physical assemblies across statuses in the selected source scope. Unknown status
does not masquerade as new/existing/removal, and keeps the requested total
incomplete. total_each is null whenever requested target, relationship, grouping,
schedule or coverage is unresolved. A resolved excluded/out-of-scope row does not
block finality. All sums remain nonnegative integers. No demolition netting.

## Verification boundaries

All AC01–AC17 literal answers must inform semantic kernel tests. AC10 proves no
scale dependency; AC14 proves unchanged unrelated row identity and deterministic
replay. Persisted append-only history and actual source crop/revision changes
require the subsequent project tests; kernel tests do not claim them complete.
Additional tests: stale complete-zero review; uncertain pair beside an unrelated
known device; duplicate depictions within a multiplicity; overlapping totals;
missing relocation evidence; stale/contradictory schedule links; exact unit
equivalence; required versus unrequested system; invalid booleans/fractions;
caller immutability; corrupt authority; canonical reordering; bounded hostile data.

Original PDFs currently cover AC01–07/10 only. Remaining original/scanned/revision
fixtures, actual local model execution, representative-project truth/thresholds,
native Windows and final application acceptance remain outstanding.
