# DUCT-CURVE-DISTANCE-1: certified point-to-circular-span distance

This is the first dependency of the outstanding circular/span evaluation work.
It is not whole-path matching, topology scoring or takeoff acceptance.

## Exact-base dispatch prerequisites

The owner completed Xcode's license flow; the pinned locked/offline runner build
now succeeds. The controller's explicit owner-authorized internal submission is
bound to this task's exact raw bytes using `--approved-internal-task-sha256`.
Require the reviewed authorization implementation, strict protocol validation,
macos_seatbelt and a fresh logged-in check before dispatch. Preserve the original
rejected preflight and failed build records. No PUBLIC relabeling or local_only
claim is permitted for this INTERNAL external task.

## Assignment and authority

Claude Code is the sole implementation writer for exactly:
- `scripts/duct_curve_distance.py`
- `tests/drawing-workspace/test_duct_curve_distance.py`

Codex independently owns reference fixtures, source verification, review, checks,
integration, status and all commits. You are not alone in the codebase. Preserve
other work. Do not edit any other path or revert another worker's edits. Return
your report in the final provider JSON, not another file. No commits, shell,
subagents, external tools, browser, credentials or new dependencies. The current
owner's bounded headless assignment is the authorized workflow; no Workflow or
Agent tool is exposed. Read/Glob/Grep/Edit/Write are available; Codex runs checks.
Do not claim to execute tests. Start by writing meaningful tests.

Input is INTERNAL source and project instructions under the owner's explicit
Claude delegation request, plus PUBLIC source-backed research. No confidential
project files or secrets are admitted. Use at most 80 tool actions and 900 seconds;
the runner enforces elapsed time, not internal action count. Planning token ceiling:
30000 total proposed work tokens (advisory; CLI may not attest it). No model override:
use configured default and retain actual provider model from result if reported.

## Research packet

Read `docs/research/notebooklm/duct-curve-distance-findings-2026-09-15.json`.
Its passages were checked against indexed public source text by Codex. The
point-to-span algorithm below is a local mathematical derivation, not a claim
that a published source supplies this algorithm or an HVAC tolerance. Source
geometry, connectivity, labels and quantity are distinct. D01-D10 remain
unchanged; this task cannot establish quantity or alter approved rules.

## API and behavior

Implement a pure standard-library module, compatible with Python 3.9+:

`point_arc_distance(point, *, controls, start_ray=None, end_ray=None,
precision_bits=80, operation_limit=20000, bit_limit=16384)`

- `controls` are the three canonical integer PDF micropoint controls accepted by
  the existing `scripts/duct_arc_geometry.py`. Load that sibling reliably even
  through importlib from another working directory; never modify it.
- Reconstruct a validated circle with `make_arc`. If both rays are absent, use
  its entire `root_span`. Otherwise require both and validate with `make_span`.
  Rays are primitive bounded integer pairs selecting a positive interval inside
  the root. Accept no duck-typed/forged precomputed circle or span.
- `point` is a list/tuple of two exact `int` or `Fraction` coordinates, excluding
  bool, float, Decimal, strings and other numeric types. Bound numerator and
  denominator sizes before derived arithmetic. The coordinate unit is unchanged.
- Return exactly `{"lower": Fraction, "upper": Fraction,
  "method": "point_arc_distance_rational_v1"}`. Enclose the continuous Euclidean
  minimum distance to the selected closed curve, with 0 <= lower <= upper and
  upper-lower <= 2**(-precision_bits). Never silently return a coarse certificate.
- precision_bits is an exact integer in [32,256]; bit_limit in [64,262144];
  operation_limit in [1,1000000], rejecting bool. Defaults as above. No I/O or
  network during calculation. Loading the sibling source at import is allowed.
- Export `CurveDistanceError(ValueError)` with stable `.code` values
  `curve_distance_invalid` and `curve_distance_resource_indeterminate`.
  Translate underlying ArcError appropriately. Invalid user types/geometry/limits
  must not leak TypeError/ZeroDivisionError. On resource exhaustion, fail explicitly.
- The custom budget governs this module's rational calculations/refinement;
  canonical constructors retain their existing bounded guards. Guard intermediate
  products, cross/dot comparisons, shifts and sqrt integer work before allocation.
  Account for arithmetic and each refinement in the operation budget.

## Mathematical contract

For center c, radius squared R2, p, and v=p-c:
1. If v=0, all arc points are equidistant: sqrt(R2).
2. Otherwise decide exact angular membership using directed cross-product signs.
   For minor/semicircle require both closed half-planes; for major require either.
   If the radial direction belongs to the span, distance is
   abs(sqrt(dot(v,v))-sqrt(R2)).
3. Otherwise the minimum is at one of the two endpoints. For endpoint ray u,
   squared distance is dot(v,v)+R2-2*dot(v,u)*sqrt(R2/dot(u,u)).
   Use outward bounds, respecting the sign of the dot product; take interval
   minima and outward square roots. Endpoint coordinates may be irrational.
4. Use exact rational arithmetic and integer-square-root outward enclosures;
   refine under the resource budget until the requested certificate width holds.
   No trig floats, epsilon membership, rounded endpoint replacement, sampled
   distance, chord substitution or approximate success.

## Meaningful checks and handoff

Tests must cover center/on-curve, inside/outside radius, each nearest endpoint,
minor/major/semicircle in both directions, irrational ray endpoints, nearly-boundary
rational points, translated/scaled curves, rational circle centers, false chord
matches, invalid controls/rays/input types/limits and exhausted resources. Include
closed-form expected values or independent rational inequalities, not results
computed by the implementation under test. No imports from unrelated product files.
Codex separately constructs and runs independent reference cases.

Codex acceptance commands (run from retained candidate, bytecode disabled):
1. `/opt/homebrew/opt/python@3.14/bin/python3.14 -B -m unittest discover -s tests/drawing-workspace -p 'test_duct_curve_distance.py' -v`
2. `/opt/homebrew/opt/python@3.14/bin/python3.14 -B -m unittest discover -s tests/drawing-workspace -p 'test_duct_arc_geometry.py' -q`
3. `/Users/bekim/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3.12 -B -m unittest discover -s tests/drawing-workspace -p 'test_duct_curve_distance.py' -v`
4. `git diff --check`

Return implementation summary, exact changed paths, tests authored (not run),
mathematical reasoning, source applicability and any unresolved issues. Stop if
scope or required authority is missing. Do not widen paths to resolve a problem.
