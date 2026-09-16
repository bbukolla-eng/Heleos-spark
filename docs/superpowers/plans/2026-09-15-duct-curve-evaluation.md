# DUCT-CURVE-EVALUATION-1

Continue the approved duct evaluation dependency order. Approved takeoff rules,
quantity code, scale evidence and source identities remain authoritative.
This is diagnostic evaluation, not takeoff or release acceptance.

## Ownership and first deliverable

Claude Code is sole candidate writer for only:

- `scripts/duct_arc_cover.py`
- `tests/drawing-workspace/test_duct_arc_cover.py`

Codex owns this plan, research, scorer integration, independent tests, UI/report
integration, review and commits. Workers are not alone and must preserve other
paths. Claude may read the committed canonical geometry and point-distance
modules. No dependency edits, subprocesses, extra report file, credentials,
external calls, subagents or commits. Use Read/Glob/Grep/Edit/Write only. Report
unrun checks honestly. Write the focused test first. Return a concise final report
as soon as the two files are ready; do not wait for a test tool you do not have.

The first deliverable is a pure Python 3.9+ standard-library circular-cover
primitive. Read the hash-pinned research packet
`docs/research/notebooklm/duct-curve-evaluation-findings-2026-09-15.json`.
Research context does not prove the following locally derived algorithm.

## Exact helper contract

`arc_cover(controls, *, start_ray=None, end_ray=None, spacing,
max_samples=20000, operation_limit=1000000, bit_limit=16384)` returns exactly:

- `points`: tuple of rational `(Fraction, Fraction)` approximations, in directed
  order, including both selected endpoints. Every approximation is associated
  with a point on the true closed circular span.
- `point_error`: nonnegative Fraction bounding the Euclidean error of every
  approximation from its associated true point.
- `cover_radius`: nonnegative Fraction bounding, for every point on the true
  span, its distance to at least one returned approximation.
- `method`: `circular_arc_cover_rational_v1`.
- `work_units`: positive integer count of charged budget operations. State the
  exact accounting in the module; canonical fixed-size validation may be outside
  the adaptive budget but must never allow unbounded inputs.

All units are canonical PDF micropoints. Successful `cover_radius <= spacing`
and `point_error <= min(spacing/16, 2**-80)` are required. No chord or rounded ray
endpoint substitutes for a true curve. No floats, trig, Decimal, epsilon, file
I/O during calculation, global cache or unbounded recursion.

Validate three bounded integer controls through the existing `make_arc`.
Both rays omitted selects root; otherwise both must be primitive bounded integer
rays accepted by existing `make_span`. Reject bool, float, Decimal and numeric
subclasses for spacing; exact int or Fraction only, positive. Limits are exact
ints: 2 <= max_samples <= 20000, 1 <= operation_limit <= 1000000,
64 <= bit_limit <= 262144. Stable `ArcCoverError(ValueError)` with `code` and
`message`: `arc_cover_invalid` for invalid inputs, `arc_cover_resource_indeterminate`
for exhausted sample/bit/arithmetic budgets. Never return a coarse success when
resources run out. Import the sibling canonical module reliably independent of
cwd, without mutating sys.path or process-global import hooks.

## Conservative cover derivation

1. Make the canonical root/span. Partition at cardinal rays `(1,0)`, `(0,1)`,
   `(-1,0)`, `(0,-1)` strictly inside the selected interval. Use exact root-relative
   order and the selected start/end, supporting CW, CCW, minor, major and
   semicircle roots and spans. A major span whose endpoints are in the same
   quadrant can have FOUR interior cardinals and FIVE initial leaves. Do not
   assume a four-leaf cap. Do not include duplicate boundary cardinals.
2. For ray u, its true point is center + u*sqrt(radius_squared/dot(u,u)). Bound
   the radical outward with guarded integer square roots and exact rational
   arithmetic. Respect the signs of each ray coordinate. The rational midpoint
   of its coordinate box has Euclidean error at most the sum of the two half
   widths. Refine until the required point-error bound holds.
3. Inside one closed quadrant, circle x and y are monotone. The union of the
   two endpoint coordinate boxes therefore encloses the ENTIRE arc leaf. For
   the start-point approximation q, the sum of its maximum absolute x and y
   differences from this box bounds every true leaf-point distance to q.
4. If this bound exceeds spacing, split with the strict interior ray
   `primitive(u*norm1(v) + v*norm1(u))`. This bisects L1-normalized directions
   and avoids bad progress when primitive endpoint magnitudes differ. Original
   input rays retain the canonical 256-bit limit; internally generated rays
   may exceed it, under the explicit bit budget. They are not new public spans.
5. Use a bounded iterative traversal. Return each leaf start and the final end
   once. The maximum leaf bound is cover_radius; the maximum point box error
   is point_error. Check limits before new stack/cache/list allocation. Budget
   all adaptive arithmetic/comparisons, including preallocation bit growth for
   shifts, products, Fraction operations and comparisons. Fixed canonical
   validation remains bounded. Explain accounting honestly in the docstring.

## Required candidate tests

Cover CW/CCW, minor/major/semicircle, root/selected span, irrational endpoints,
translated circles, exact endpoint error, finer-spacing refinement, same-quadrant
major span with four cardinals, unequal primitive ray magnitudes, invalid controls,
nonprimitive or out-of-root rays, nonexact spacing/limits, sample exhaustion,
operation exhaustion and bit exhaustion. Use algebraic checks for known points
and rational bounds, not float-only assertions or tests mirroring implementation.
Do not claim universal geometry accuracy from these fixtures.

Codex independently runs candidate tests on Python 3.9, 3.12 and 3.14, existing
geometry/distance tests, and a separate oracle before accepting any bytes.

## Connected follow-through owned by Codex

Introduce a versioned circular/span truth contract while retaining old planar
truth and saved report history. Compare saved producer geometry continuously.
For source cover S with point error delta and cover G, directed distance is
bounded below by max(0, max point-target lower - delta), and above by
max point-target upper + G. Use the accepted point-to-arc helper and existing
exact point-to-segment routine. Max both directed intervals. A tolerance crossed
by an interval is indeterminate. Exhausted budgets abort the evaluation with a
typed error and never manufacture a match. Preserve explicit sample/pair/work
limits and accurate dependency identities. Keep dimensions/work status/length
separate, and expose circular coverage in saved reports, exports and UI.

Retain immutable history; scorer byte changes mark old evidence stale without
rewriting its stored report. Verify dataset import, saved result comparison,
reopen/export and provenance invalidation. Update CURRENT_STATUS when each scoped
capability is independently accepted. Explicit topology assertions follow this
geometry capability; intersections alone never establish physical connectivity.
Representative accuracy, remaining categories and native Windows remain open.
