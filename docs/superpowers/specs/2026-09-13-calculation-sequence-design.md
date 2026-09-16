# Mechanical calculation sequence and dataset use

Planning checkpoint: 2026-09-13, CALCULATION-STEPS-1. This document records owner
choices and the proposed calculation, fixture and dataset work. It does not claim
that the quantity engines, model training or acceptance work have been completed.
The [roadmap](../../roadmap.md) remains the full product sequence; the
[implementation continuity plan](../plans/2026-09-10-equipment-workflow.md) remains
the build entrypoint.

## Current execution clarification — 2026-09-16

The owner has explicitly rejected making duct or air-device completion a
bottleneck for the rest of Division 23. The initial duct → air-device → equipment
order is a delivery priority, **not a dependency requiring complete recognition,
every edge case or native acceptance of one class before another can advance**.
Equipment, piping, fittings/accessories, insulation, common work, controls and
service obligations may proceed against their own real inputs and rule decisions.
Approved D01–D10/A01–A12 and imperial outputs are unchanged. Missing class-specific
approval gates only that class's quantity behavior; preparation and independent
implementation continue.

The capability descriptions and “first/next” language below are the 2026-09-13
planning snapshot. They must not restart completed duct or air-device software.
Use root `CURRENT_STATUS.md` and current task evidence for execution. Preserve
unqualified recognition and native acceptance as open conditions with bounded
experiments, not universal prerequisites for other mechanical categories.

## Recorded owner choices

The owner answered "All in order" to duct lengths by size, air-device counts and
equipment counts, and selected "Imperial". The confirmed execution order is:

1. Duct lengths by size, reported in feet, with duct dimensions in inches.
2. Air-device counts, reported in each, grouped by supported device type/size.
3. Equipment counts, reported in each, grouped by supported equipment type/tag.

The owner also approved drawn centerline lengths, vertical lengths only when
supported by dimensions or elevations, separate fitting counts, and separation
of waste/allowances from measured totals. Counts are dimensionless; their size
attributes use imperial units. Original source units and text remain preserved.

This resolved the earlier class/unit choice. Each class has its own rules,
checked examples and acceptance evidence. Duct length was the initial 0.5
calculation. The execution clarification above governs continuation; prioritizing
these first deliveries does not gate the rest of Division 23 or admit unapproved
quantity rules.

## Historical capability snapshot and initial finish line (2026-09-13)

Existing code supplies document/revision identities, normalized sheet geometry,
verified view scales, Decimal path measurement, schedule/plan reconciliation,
saved review decisions and affected-result invalidation. Reuse these components.
Automatic duct centerlines, size changes, connections and physical-instance counts
still need implementation and evaluation. A reviewed item per tag is not a general
physical counting rule.

The first finish line is a saved drawing set producing feet of duct by size, with
clickable source overlays, separately visible supported vertical lengths, fittings
and unresolved portions, plus correct replay and correction behavior. Recognition
must propose the geometry from drawings; manually entered totals are not the normal
workflow. Checked synthetic geometry can establish the arithmetic before live model
performance is proven, but does not establish automatic takeoff accuracy.

## Calculation steps

| Step | Work | Output and condition for proceeding |
| --- | --- | --- |
| 1. Freeze scope | Select source revisions, sheets/views, systems, work status and rules. Distinguish new, existing-to-remain and demolition rather than silently combining them. | Versioned scope/rule record; unresolved contractual scope remains visible. |
| 2. Resolve coordinates and scale | Use original page crop/rotation/transform identity. Verify scale per view using the existing lifecycle. Preserve explicit dimensions and elevations as separate evidence. | Bound coordinates and verified scale for every measured path. Counts do not require drawing scale, but still require source and instance identity. |
| 3. Read drawing content | Propose ducts, device/equipment instances, size text, tags, reference markers and architectural context. Retain original labels, geometry and model/reader identities. | Reviewable observations. Model confidence alone cannot verify a scale, identify a physical instance or authorize a quantity. |
| 4. Resolve geometry and identity | Build duct paths, locate size changes/branches/fittings and link supported continuations. Reconcile countable instances with tags and schedules. | Each segment/instance has traceable evidence. Crossings are not connections without supporting evidence; sheet/tile boundaries are not physical endpoints. |
| 5. Calculate | Apply the selected class rules below using deterministic arithmetic. | Measured length/count plus inputs, rule version and dependency identities. Missing inputs produce an unresolved result, never an invented zero. |
| 6. Group and reconcile | Group lengths by system, size, shape, material/status when supported; group counts by type/size and supported identity. Compare schedule declarations without replacing physical observations. | A known subtotal, completeness state and explicit unresolved coverage. Repeated crops, views, revisions and tags cannot silently multiply quantities. |
| 7. Review and correct | Show overlays, omissions, conflicts and the effect of a correction. Reuse append-only history and invalidate only affected results. | Replacement results cite their predecessors and current evidence; unrelated quantities remain unchanged. |
| 8. Export and verify | Save a reproducible result/evidence manifest; build formula Excel and evidence PDF at roadmap 0.6. | Core and export agree in declared units/rounding; material unresolved items prevent the affected final output from appearing complete. |

## Class 1: duct lengths by size

1. Read shape and size from attributable drawing text/legend evidence. Retain the
   original notation, including rectangular, round and oval distinctions. Split
   a route when its size or other grouping property changes. An unresolved size
   can retain its known length but cannot enter an assumed size group.
2. Construct centerline segments in the original sheet coordinate system. Merge
   duplicated proposals by source/physical correspondence, not by matching length.
   Keep actual branches separate. Curves need supported curve geometry or a declared
   approximation and error tolerance; boxes alone do not establish centerlines.
3. Resolve the verified scale for each segment/view. For a polyline with paper
   points p0...pn and uniform verified scale s in meters per paper point:

   `L_m = s * sum(distance(p[i], p[i+1]))`

   `L_ft = L_m / 0.3048`

   Reuse `sheet_geometry.paper_length`, `sheet_scale.resolve` and
   `sheet_scale.measure_path`; preserve their existing Decimal precision and
   measurement contract. Imperial presentation does not require replacing the
   stored meter-based scale facts. Record calculation versus display rounding;
   do not round each displayed segment and then treat that sum as the core result.
   The current measurement API stores meters to six decimal places. Preserve and
   test that boundary explicitly; a more precise display cannot restore discarded
   precision. Size fields currently contain free text and need validated dimensional
   interpretation before automatic grouping.
4. Record a separate vertical segment only when linked dimensions/elevations support
   it. A same-datum top/bottom elevation difference gives its magnitude. An unknown
   rise remains unresolved. A supported sloped segment uses its actual geometry;
   do not add its horizontal projection and rise as two physical lengths.
5. Sum each physical segment once per group. Fitting counts remain separate and
   do not add a default length. Counting fittings separately does not, by itself,
   specify a deduction of their body lengths or yield fabrication cut lengths.
   The precise fitting-boundary treatment must be frozen in the example/rule packet.
6. Retain measured base quantities separately from any later explicit allowance or
   accepted estimate. No default waste factor, hidden run or routing multiplier.

Before implementation, the first fixture/rule packet must settle fitting boundaries,
supported curved/flexible/sloped representations, treatment of transition portions,
scope by work status, dimension-versus-scale conflicts and presentation rounding.
These are named open decisions, not permission to select silent defaults.

## Class 2: air-device counts

1. Establish device definitions from the supported drawing legends and reviewed
   class mapping; retain original symbol/type/size text.
2. Locate actual physical instances. Exclude legend illustrations and repeated
   detail depictions from physical counts after their roles are established.
3. Link repeated views of the same instance, and keep distinct devices sharing a
   type tag separate. Explicit multiplicity needs its own scope and evidence.
4. Calculate `count = number of included, distinct physical instances` for each
   type/size/system/status group. Unknown identity or multiplicity remains unresolved.
5. Compare with schedule declarations and preserve mismatches for review. Reuse the
   correction, replay and export contract from duct lengths.

## Class 3: equipment counts

1. Identify physical equipment instances on current plans and link their supported
   tags to the existing bidirectional reconciliation graph.
2. Resolve repeated references, alternate views, grouped tags, untagged units and
   schedule-only entries without equating tag count or graph-edge count to quantity.
3. Count included distinct instances in each equipment group; keep observed count,
   schedule declaration and unresolved scope separate.
4. Apply the same dependency, history, correction and evidence-export contract.

Device/equipment class definitions and inclusion rules will be frozen before their
respective implementation; the approved order does not resolve their detailed rules.

## Checked-example packet to prepare next

These are proposed synthetic cases, not owner-adjudicated production fixtures or
test results. Freeze source bytes, evidence locations, literal expected answers,
scope, units and tolerances before running the matching acceptance work.

| Case | Independently specified expected result |
| --- | --- |
| Straight and bent centerline | At 1/4 inch = 1 foot, a 144-point leg followed by a 72-point leg gives 12 feet. Fitting count is separate; no extra length appears. |
| Supported and unknown rise | Add a separate documented 5-foot vertical segment to the previous 12 feet: 17 feet. Without that evidence, retain 12 feet as a partial subtotal and an unresolved vertical portion. |
| Supported slope | A segment with a 3-foot horizontal projection and 4-foot rise is 5 feet, not 7 feet. No inference of an unshown rise. |
| Scale/crop/rotation and size change | Equivalent original geometry after supported crop/rotation gives equal length. A known size boundary splits 8 feet and 4 feet into their correct size groups. Missing/conflicting view scale blocks the affected measurement. |
| Crossing, continuation and overlapping tiles | Two unconnected routes crossing remain unconnected. A repeated tile does not add length; a crop edge alone does not end a route. An unsupported continuation remains unresolved. |
| Physical counting | Three distinct device instances sharing one type tag count as three. A legend copy counts as none. Two views of one equipment unit count once; schedule-only evidence stays unresolved. |
| Correction and replay | Changing only a supported 5-foot rise to 6 feet replaces 17 feet with 18 feet for that route; unrelated routes and historical evidence remain intact. Replaying the same command does not duplicate results. |
| Partial coverage | A known route plus an unread or unresolved portion displays its known subtotal and incomplete state, never a complete job total or zero for the missing portion. |

Synthetic formula/geometry checks prove calculation behavior. Separate representative
target drawings are needed to measure missed/extra devices, size classification,
centerline and connectivity errors, length error by size, completeness, latency and
memory. Freeze acceptance thresholds before evaluating candidate models.

## How the nine supplied datasets can contribute

This is a proposed task mapping from the existing public-source inspections. All
nine remain research candidates. Sample observations are not frozen-archive audits
or established dataset-wide error rates. The linked reports retain exact observations.

| Dataset | Proposed use | Required check or missing supervision |
| --- | --- | --- |
| [BluePrint HVAC](https://universe.roboflow.com/blueprint-hu8pr/hvac-hkgny) | Duct/bend region proposals for the first class. | Audit numeric labels and `vd`; add or verify centerlines, size changes and connectivity. |
| [HVAC project](https://universe.roboflow.com/hvac-project/hvac-vcvog) | Compare alternative duct/bend annotations with BluePrint. | At least one checked source image overlaps; adjudicate the differing annotations and keep the source family together across evaluation splits. |
| [Floor Plans](https://universe.roboflow.com/reviuer/floor-plans-zeb7z) | Wall/room context for testing architectural false traces. | License/source remains unresolved. Preserve original pixels; a wall or room prediction cannot erase mechanical content, and missing MEP labels do not mean absent MEP. |
| [Signs recognition](https://universe.roboflow.com/plan-training/signs-recongnition) | Find detail/section/grid/level markers before text extraction and reference matching. | Check small-source coverage and incomplete annotations; marker location does not establish the referenced sheet or elevation value. |
| [P&ID two classes](https://universe.roboflow.com/pid-dataset/p-id-2-classes-er8vf) | Generic region proposals after its annotation meaning is resolved. | The inspected `tag` group collapses multiple symbol distinctions. Do not map it directly to equipment or OCR tags. |
| [Datset2](https://universe.roboflow.com/symbol-detection-boj78/datset2-bbc8k) | Later valve/equipment/reference-symbol experiments. | Named and numeric classes need a codebook; P&ID transfer to building plans needs separate evidence. |
| [Split Images](https://universe.roboflow.com/pnid-2u2eu/split_images-uib2u) | Later named valve/instrument/component recognition. | Check examples per class, source grouping and target-plan relevance before claiming coverage. |
| [Symbol detection v2](https://universe.roboflow.com/institute-of-management-sciences-eacvm/symbol_detection_v2-dtl7r) | Generic symbol localization. | Its observed single class `0` supplies no typed equipment/device labels. |
| [Tiling method](https://universe.roboflow.com/opop-ri0av/tiling_method-w1e2l) | Hold for a possible narrowly defined symbol task. | Meanings of `box` and `2M` through `8M` remain unresolved; the name does not prove useful duct tracing supervision. |

The current inspections have not established adequate air-device type coverage or
physical equipment-instance truth for the second and third deliveries. Prepare
target-plan examples with checked legends, instance identities and expected counts.
Architectural context is an experiment hypothesis: compare the same held-out
mechanical projects with and without it, including real ducts crossing walls.

## Dataset preparation steps that can run alongside calculation work

1. **Source inventory:** reuse the nine existing candidate JSON records. Record
   selected workspace/project/version, original drawing groups, rights basis,
   annotation format and intended task; preserve unknown fields as unknown.
2. **Acquire exact exports when their use basis is established:** retain original
   images/annotations, class definitions, processing metadata and archive/file
   hashes locally. Prefer a loss-preserving COCO representation for boxes and an
   appropriate segmentation export for polygons, retaining original exports too.
   Roboflow documents the image/category/annotation structure in its
   [COCO JSON format reference](https://roboflow.com/formats/coco-json).
   A converted box never becomes path or segmentation ground truth.
3. **Audit labels and duplication:** resolve numeric/ambiguous classes, inspect
   missing annotations and reconcile competing labels on shared source images.
   Retain source-specific mappings rather than renaming all labels into one pool.
4. **Separate training and evaluation by source:** keep related projects, sheets,
   revisions, tiles and augmentations in one group. If source ancestry is unknown,
   do not claim independent held-out evaluation. Augment only after assigning groups.
5. **Add missing target annotations:** checked duct paths/endpoints/connections,
   size-text associations, supported elevations, device/equipment instances and
   verified architectural distractors. Preserve transforms back to full sheets.
6. **Run bounded recognition experiments:** begin with the HVAC pair after its
   overlap/codebook audit. Use the existing source-pinned model baseline for supported
   box tasks; extend evaluation for paths/topology instead of treating box scores as
   takeoff accuracy. Model/runtime identities and data-use limits remain explicit.
7. **Evaluate and connect:** score saved predictions against frozen target truth,
   admit only supported behavior, and feed observations to deterministic calculations.
   Later corrections become annotation candidates only after review; the frozen
   evaluation set must not silently become training material.

This planning task performs no archive download, training or model admission.
Additional useful preparation is the class mapping, source-group manifest,
checked-example packet, correction/dependency cases and export-column contract.

## Reuse and execution boundaries

| Existing file | Responsibility to reuse |
| --- | --- |
| `scripts/sheet_geometry.py` | Source geometry, coordinate transforms and paper path length. |
| `scripts/sheet_scale.py` | Scale facts/decisions, verified resolution and Decimal measurement. |
| `scripts/takeoff_workflow.py` | Saved project workflow, measurements, review events, invalidation and current/history exports. |
| `scripts/schedule_reconciliation.py` and `scripts/project_schedule_reconciliation.py` | Evidence-bound correspondence and exceptions. |
| `scripts/equipment_takeoff.py` | Existing reader/observation adapter; do not promote one-per-tag output into physical counting. |
| `scripts/mechanical_model_baseline.py` and `scripts/mechanical_model_scoring.py` | Source-pinned experiments and existing bounding-box metrics. |

At the original planning checkpoint, detailed code tasks followed the first duct
rule/fixture packet. That prerequisite has been completed; do not reopen it.
Do not implement unsupported geometry or invent a new parallel quantity store.
Preserve native Mac/Windows scope. Local synthetic tests, existing model-adapter
code and this document do not close formal 0.2-0.4, live-model or native acceptance.
The existing roadmap also includes piping, fittings, accessories, controls,
insulation, demolition, full correction/export and native product parity. These
categories do not wait for complete acceptance of the first three. Each expansion
carries its own rules, actual dependencies and evidence.

Inspection references: [HVAC and four symbol candidates](../../research/engineering/roboflow-six-dataset-comparison-2026-09-13.md),
[floor plans](../../research/engineering/roboflow-floor-plan-context-2026-09-13.md),
[signs](../../research/engineering/roboflow-signs-recognition-2026-09-13.md),
[P&ID two classes](../../research/engineering/roboflow-pid-two-classes-2026-09-13.md).
