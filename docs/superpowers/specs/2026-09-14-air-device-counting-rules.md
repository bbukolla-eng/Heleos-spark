# Air-device counting rules and checked examples

Task AIR-DEVICE-PREPARATION-1. **A01–A12 and AC01–AC17 approved by the owner;
implementation remains outstanding.** See the [exact approval record](../../../tests/fixtures/air-device-takeoff/2026-09-15-owner-decision.json). The approved sequence already selects air-device counts next,
reported in each with imperial size attributes. The separate approval records these class definitions and examples without
changing approved duct rules D01-D10.

The [calculation sequence](2026-09-13-calculation-sequence-design.md) requires
device definitions and inclusion rules to be frozen before implementation. This
packet makes that decision concrete without reopening class order or units.

## Source basis and limits

The [NotebookLM review](../../research/notebooklm/air-device-findings-2026-09-14.json)
reused the inventory and completed one new N11 question. Its nonempty sources
support using legends to interpret symbols, distinguishing enlarged views and
details, and preserving schedule attributes. FastDUCT describes configurable
each/linear-foot assembly units; that is software behavior, not a measurement
standard. Price and Titus entries returned empty bodies and supply no evidence.
The review explicitly identifies unsupported mechanical counting topics.

Accordingly, the rules below are approved project policy,
not quotations from an external standard. Supporting source identities and indexed
text locations remain in the research record. Rule approval is recorded separately from implementation and production acceptance.

## Approved rules

| ID | Approved basis for the connected air-device calculation |
| --- | --- |
| A01 | Count discrete physical air-distribution assemblies: diffusers, registers, grilles, linear diffuser assemblies and explicitly mechanical louvers. Establish the family and contractual role from attributable project legend/plan/schedule/specification evidence. Architectural louvers are outside this mechanical count. VAV/fan-powered terminals and fans retain equipment routing; separate dampers, plenums and accessories retain their own categories. Integral factory parts do not create additional assembly counts. |
| A02 | A physical occurrence contributes one each unless a supported scoped multiplicity applies. The graphic and its attached tag/leader describe one occurrence. Distinct devices sharing a type tag remain distinct. A text tag without a supported physical occurrence remains unresolved; a missing tag does not by itself prevent counting a clearly identified physical assembly. |
| A03 | Group by supported family/type, qualified size, system/service and work scope as requested. Retain supported system identities separately from service descriptions such as supply/return; a service name does not identify an AHU system. Preserve face size, neck/connection size, opening size, assembly length and slot count as distinct attributes. Retain original units/text and normalize compatible dimensions for imperial presentation without rounding-based equivalence. The versioned scope declares required grouping fields; unknown, not-supplied and explicitly not-applicable attributes remain distinct. A missing required group field blocks that final group total while retaining a known physical subtotal. An unrequested system breakdown does not invent a system or invalidate an otherwise resolved type/size count. |
| A04 | Count a physical assembly once across model duplicates, tiles, enlarged views, details and revisions. Preserve source-bound physical correspondence and all supporting depictions. Matching tags, overlapping boxes, identical raster bytes on different sheets or model response IDs alone do not establish identity. Uncertain correspondence blocks the affected final count. Confirmed legends, generic detail/catalog illustrations, schedules, walls and unrelated symbols do not contribute installed instances. |
| A05 | An explicit positive integer multiplicity may represent a source-defined group. Retain the note, represented items, room/view/level scope and its applicability. A representative symbol plus a note saying four total contributes four, not five. Bare TYP, repeated-floor assumptions and unclear note scope do not supply a multiplier. Explicitly drawn members already covered by that group must not be added again. Fractional/negative device multiplicities are invalid. |
| A06 | Use current, unambiguous schedule/type correspondence to support attributes. A schedule's quantity remains a separate declaration. It does not manufacture missing plan instances, overwrite an observed count or prove that all scope was found. Conflicts remain visible with observed and declared quantities; the affected final takeoff stays incomplete until resolved with evidence. |
| A07 | A linear diffuser shown as one installed assembly contributes one each; slots, blades, necks and integral parts are attributes. Distinct source-supported assemblies/modules may be counted separately when explicitly identified. Keep stated linear length as a separate qualified attribute. This count does not establish measured linear feet, fabrication sections, cut lengths or accessory quantities; those required outputs remain separate work in the full takeoff goal. |
| A08 | Counts are nonnegative integers. Supported geometry/instance identity and current original-source bindings are required, but drawing scale is not a dependency for an each count. NTS drawings may supply physical counts. A scale-only withdrawal/replacement leaves the same source-bound each counts intact; a changed crop/transform/source identity still requires valid correspondence. |
| A09 | Keep new installation, existing-to-remain and demolition/removal in separate groups with their source evidence. Do not net demolition against new work. An unestablished work status remains unknown. Requested scope determines the displayed scoped total while other supported status quantities remain visible. |
| A10 | A verified relocated assembly remains one physical assembly, with separately supported removal and reinstallation operations. Do not infer a new purchase or merge old/new locations from a matching tag alone. Unresolved relocation, phase or alternate relationships remain exceptions; their operation identities must be represented before claiming a complete affected scope. |
| A11 | Show known subtotals and incomplete coverage separately. An empty or successful detector response does not establish zero devices. A final zero is permitted only for a current, explicitly complete selected scope with no unresolved target instances or requirements. Unknown portions are never converted to zero; scope-completeness decisions retain their own evidence and dependencies. |
| A12 | Save source observations, rule binding, admissions, correspondence, calculations and corrections with append-only history. Replaying a command or reusing a saved producer result does not add quantities. A correction/revision invalidates or replaces the affected instance/correspondence/schedule/scope results while preserving unrelated current counts. Final each quantities come from these deterministic records, not arbitrary manually entered totals or model-provided final counts. |

The full Division 23 scope remains intact. Linear-foot requirements, equipment,
accessories, controls, insulation and other unfinished categories remain visible
work. The initial air-device implementation must support all admitted rules above,
including scoped multiplicity and relocation operations, or report those rules as
unfinished; passing basic symbol-count examples alone is not completion.

## Approved literal answers

The [separate answer records](../../../tests/fixtures/air-device-takeoff/2026-09-14-rule-examples.json)
contain frozen literal inputs, expected results and source-coverage limits. These
are approved software examples; they are not representative-project truth or
accepted recognition thresholds. Answer records must never enter model inputs.

| Case | Source/observation condition | Approved result |
| --- | --- | --- |
| AC01 | Three separate SD-1 plan symbols and one SD-1 legend illustration; the selected plan scope is fully resolved. | 3 each; legend contributes no installed instance. |
| AC02 | One SD-2 appears in a plan and an explicitly linked enlarged view of the same terminal. | 1 each, both references retained. |
| AC03 | Those two observations lack a usable physical-correspondence decision. | Affected final count unknown; no silent choice of one or two. The drawing contains the relationship, but the observation/decision is incomplete. |
| AC04 | One representative RG-1 symbol; a room-bound note explicitly requires four total in that room. | 4 each for the group, with no extra representative-symbol count. |
| AC05 | One new, one existing-to-remain and one removal SD-3; requested total is new work. | New total 1; existing 1 and removal 1 retained separately. |
| AC06 | Three SD-4 plan instances; a current applicable schedule declares four. | Observed subtotal 3; declared 4; affected final count unknown. |
| AC07 | One installed LD-1 assembly, two slots, stated length 6 ft. | 1 each; slots 2 and length 6 ft remain attributes. |
| AC08 | One confirmed new physical diffuser with unresolved size/type grouping. | Known subtotal 1; affected final type/size total unknown. |
| AC09 | One confirmed new untagged diffuser; family and qualified size are source-supported. | 1 each in its supported family/size group; no invented tag. |
| AC10 | The three AC01 instances are on an NTS page; a calibration bound to that same original view is withdrawn/replaced while its geometry and instance evidence stay identical. | 3 each before and after; no count-scale dependency. |
| AC11 | One physical symbol produced twice, with verified source-supported correspondence. | 1 each; both proposal identities retained. |
| AC12 | Old and new locations are verified as one relocated assembly. | Physical assemblies 1; remove 1 and reinstall 1 as operations; new-purchase quantity 0 for this explicitly reused assembly. |
| AC13 | A detector returns no observations with unknown coverage; a separate explicitly verified empty selected scope has no unresolved requirements. | First final count unknown; second final count 0. These are separate scenarios. |
| AC14 | A source-supported correction excludes one of three false-positive-inclusive observations; an unrelated group has one device. | Affected count changes 3 to 2, original 3 retained; unrelated 1 unchanged; replay remains 2. |
| AC15 | Only a type-tag text box is read; its physical symbol/occurrence has not been established. | Affected final count unknown; tag text does not contribute one each. |
| AC16 | One new explicitly mechanical louver and one confirmed architectural louver are fully classified. | Mechanical air-device count 1; architectural item retained with its excluded role. |
| AC17 | A requested system breakdown has one confirmed device whose system identity is unknown; a later source-supported correction supplies AHU-1. | Known subtotal 1 and unknown system-group total initially; then 1 each in AHU-1. A service description alone cannot supply that system identity. |

Original source pages accompany AC01-AC07 and AC10. The remaining cases are literal
semantic/lifecycle examples; original PDF, scanned-source and revision-pair coverage
for them is still outstanding. AC03 deliberately omits a decision about an explicit
source relationship. AC10 tests count independence from scale; it does not alter
the geometry authority. The fixture is synthetic and cannot establish model accuracy.
Unless a case explicitly requests a system breakdown, its literal total is by
supported family/type, qualified size and work status. No complete-by-system
claim is made for source pages that do not name a system. AC17 covers that required
grouping distinction separately.

## Implementation under the recorded decision

Use a separate source-bound air-device observation/count contract. The existing
equipment reconciler assumes unique equipment tags, and the broad vision adapter
preserves duplicate observations; neither output is a physical air-device count.
Reuse source capture, original image retention, atomic history, selective
recalculation and source overlays from the connected duct workflow without adding
scale dependencies to each counts. Preserve reviewed type/schedule correspondences
separately from physical-instance identity.

Meaningful checks must cover every A01-A12 and AC01-AC17 requirement, source roles,
source/response changes, unknown coverage, integer multiplicity, duplicate readings,
scope/status, preserved history and replay. Native original-source integration and
independent review follow the pure calculation and connected UI checks. Actual
configured model execution, representative-project answers/thresholds and native
Windows verification remain separate acceptance work.
