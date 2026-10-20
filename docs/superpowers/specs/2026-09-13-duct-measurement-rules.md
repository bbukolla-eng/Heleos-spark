# First duct calculation: measurement rules and checked examples

Task: DUCT-CALCULATION-1. Status: owner-approved implementation basis on
2026-09-14, following the reply "Duct rules approved." The submitted rules and
checked synthetic examples govern the first calculation. This packet extends
the [approved calculation sequence](2026-09-13-calculation-sequence-design.md).
It does not claim production rule admission or representative-project acceptance.

## Already approved

Duct lengths by size come first, then air-device counts, then equipment counts.
Outputs are imperial. Use drawn centerlines; include vertical lengths only with
supporting dimensions/elevations; count fittings separately; keep waste and
allowances separate from measured totals. These choices are not being reopened.

## Approved rules

| ID | Rule for the first connected calculation |
| --- | --- |
| D01 | Measure the continuous drawn centerline, including its supported path through fittings. Do not subtract fitting bodies, add equivalent lengths, or claim fabrication cut lengths. Count physically supported fittings separately. |
| D02 | Keep new-install, existing-to-remain and demolition quantities in separate groups. An unresolved work status remains a visible unknown group and blocks a complete scope claim. |
| D03 | Initially calculate supported straight/polyline, vertical and sloped segments. A true curve requires supported curve geometry and a validated measurement method; ambiguous curves, flex routing, hidden portions and unsupported transitions remain visible unresolved work. They are not assigned straight-line substitutes. These cases remain part of the full goal. |
| D04 | Separate planar and vertical segments only when they represent different physical portions. For a straight sloped portion with supported projection and same-datum elevation difference, use the hypotenuse; never add projection and rise as separate physical lengths. |
| D05 | Explicit dimensions and elevations retain their own evidence and unit/datum. Where dimensions conflict materially with scaled geometry, flag the conflict; do not silently choose contractual precedence. |
| D06 | Split length at each supported size or grouping-property change. Retain rectangular, round and oval shape and original dimensions. Unresolved size/material/system does not acquire a guessed value. Known length can be shown as a partial subtotal with unresolved grouping. |
| D07 | Sum stored measurements before display rounding. Display feet to two decimals using the existing half-even convention; retain original source values and the current six-decimal-meter measurement boundary. Counts remain integers. Display precision is not an accuracy claim. |
| D08 | Count each physical segment once. Shared tiles, enlarged details, revisions and repeated references do not add another run. Crossings connect only with supporting evidence; uncertain continuations remain unresolved. |
| D09 | Exclude confirmed legend illustrations, dimension/leader/grid/wall lines and unrelated services from duct quantities. Preserve their evidence/role decisions. Missing target annotations do not establish target absence. |
| D10 | Show measured base totals, dimension-supported derived lengths, allowances and accepted estimates separately. No default waste factor, fitting multiplier, guessed rise, or unknown-as-zero conversion. |

An incomplete drawing/region produces a known subtotal and explicit incomplete
coverage. A complete final quantity requires all material portions in the selected
scope to be resolved. Source revisions, scale decisions, rules and geometry changes
invalidate their dependent results; recalculation appends a replacement and retains
the original history.

## Checked synthetic expected answers

These answers are specified independently of the new quantity engine. The
[machine-readable examples](../../../tests/fixtures/duct-takeoff/2026-09-13-rule-examples.json)
are mathematical/semantic specifications; they are not ingested PDF or live-model
results. Actual source-bound PDF fixtures and representative drawings are a separate
required verification step.

| Example | Given | Expected |
| --- | --- | --- |
| E01: size-changing L | At 1/4 inch = 1 foot: 2 paper inches of 24x12 duct followed by 1 paper inch of 18x12 duct. | 8.00 ft and 4.00 ft in separate size groups; 12.00 ft total. No automatic fitting length addition. |
| E02: supported rise | E01 plus a separate documented 5-foot rise. | 17.00 ft, with the planar and vertical evidence retained separately. |
| E03: unknown rise | E01 plus an indicated rise with no usable height. | Known subtotal 12.00 ft; total unknown/incomplete; one unresolved vertical portion. |
| E04: sloped segment | One straight portion projects 3 feet and rises 4 feet between supported same-datum endpoints. | 5.00 ft for that portion; no separate 3-foot and 4-foot addition. |
| E05: unconnected crossing | A 4-foot duct and 6-foot duct cross without evidence of a junction; a nearby wall is 20 feet. | 10.00 ft of duct, zero invented junctions, no wall length in the duct total. |
| E06: repeat depiction | A single 8-foot physical run appears on the plan and an enlarged detail, with a verified correspondence. | 8.00 ft, retaining both references; no duplicated physical run. |
| E07: scale correction | A 2-paper-inch run changes from verified 1/4 inch = 1 foot to verified 1/8 inch = 1 foot. | Old current length becomes unavailable, then explicit recalculation creates 16.00 ft replacing the historical 8.00 ft. |
| E08: split work status | New-install 8 feet, existing-to-remain 4 feet and demolition 3 feet. | Separate 8.00, 4.00 and 3.00 ft groups; no single 15-foot install quantity. |
| E09: round after summing | Two supported stored lengths are each 0.001524 meters, exactly 0.005 feet. | Group total 0.01 ft; individually displayed values are not the summation inputs. |

The exact arithmetic answers above have zero discretion once these inputs/rules
are accepted. Drawing extraction tolerances, class completeness and model accuracy
thresholds must be set against the frozen target-project truth set before model
evaluation; the examples do not establish those thresholds.

## Authorized first implementation deliverables

1. A strict, source-bound duct observation/segment contract carrying size, work
   status, supported points/elevation evidence and all unresolved fields explicitly.
2. Deterministic calculation and grouping using existing geometry and scale APIs,
   with no authority delegated to model-produced totals.
3. Connected saved observations, generated quantities, review overlays and
   affected-only replacement/replay behavior in the existing drawing workflow.
4. Original source-bound PDF acceptance fixtures followed by measured evaluation
   on representative authorized drawings. Synthetic injected observations establish
   the calculation contract only; automatic extraction must be proven separately.

The first supported geometry delivery is an incremental product result. Curves,
flex, transitions, all remaining mechanical classes, estimator exports and native
Mac/Windows verification remain outstanding until individually completed.
