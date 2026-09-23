# Air-device counting implementation

Task AIR-DEVICE-COUNT-1, 2026-09-16. Root `/Users/bekim/Heleos-spark`,
main, preparation base `e252ebd0d0c28bbc20dc1dad50c0ee5d38760c15`.
Owner approval settles A01–A12 and AC01–AC17, in the existing duct → air-device
→ equipment order with imperial outputs. No additional class decision is needed.

## Authority and research

The immutable `tests/fixtures/air-device-takeoff/approved-rule-packet.md` matches
the pre-approval packet hash in `2026-09-15-owner-decision.json`. The adjacent
`2026-09-16-rule-binding.json` binds the receipt, original packet and frozen
examples. Receipt approval supersedes the archived draft/pending headings.
Presentation-only approval headings in the live spec are not quantity authority.

Use `docs/research/notebooklm/air-device-build-findings-2026-09-16.json`.
Codex and a separate read-only agent verified both retained nonempty source
bodies and six passages. Reuse the previous NotebookLM query; no new mechanical
claim requires repeating it. Legends, view purposes, schedule attributes and
separate assembly units provide context. Counting, correspondence, multiplicity,
relocation and completeness policy comes from approved A01–A12. Empty
manufacturer bodies supply no authority. No private documents are authorized
for NotebookLM upload.

## Dependency order and ownership

1. Freeze approved policy binding and qualified attribute semantics. Claude owns
   only `scripts/air_device_attributes.py` and
   `tests/drawing-workspace/test_air_device_attributes.py` in its isolated runner
   candidate. Codex independently builds policy verification, count engine,
   source integration and its tests on other paths.
2. Build deterministic physical count engine covering all 17 approved cases,
   then source-bound local-reader proposals, evidence capture, admissions,
   multiplicity, correspondence, scope/coverage and schedule reconciliation.
3. Connect atomic workflow commands, exception review, append-only correction,
   current/stale dependencies, result navigation and exports. Generic manually
   entered air-device items cannot satisfy authoritative count readiness.
4. Verify original source fixtures, history/reopen, source changes, scale-only
   changes, package relocation and runtime compatibility. Representative projects
   and native Windows remain separate outstanding acceptance gates.

Each prerequisite is a scoped implementation checkpoint, not completion of the
connected capability. Codex alone reviews, integrates and commits. Preserve all
existing uncommitted work and original candidate/failure evidence.

## Claude assignment: qualified attributes only

Use Python 3.9+ standard library, no local imports, I/O, model calls or quantities.
Sole writer on the two named paths. Other agents are working independently;
do not revert their changes. Read the pinned research packet and this plan.
No shell, network, subagents, Git mutation, extra reports or edits elsewhere.
Codex runs tests; do not claim they ran inside this tool-restricted task.

Public API:

- `AirDeviceAttributeError(ValueError)`, with string `code` and readable message.
- `VERSION = "air-device-attributes-1"`.
- `FIELDS`: family, type_tag, system, service, work_status, face_size, neck_size,
  opening_size, assembly_length, slot_count (in that order).
- `normalize_attributes(attributes, evidence_ids)`: validate and return a deep
  copy with source values preserved. `evidence_ids` is a list of at most 2000
  unique nonempty strings, max 160 characters. IDs contain no control characters.
- `canonical_attributes(attributes)`: revalidate structure using the union of
  contained evidence IDs; callers separately bind evidence to actual sources.
  Return all fields as `{state, value}`, omitting evidence and original text.
  Exact semantic grouping, never display rounding or physical identity.
- `imperial_attributes(attributes)`: revalidate similarly; return deep attribute
  copy with known dimensional values converted to inches, retaining evidence IDs
  and original_text. Decimal strings use exactly six places, half-even, **display
  only**. Non-dimensional fields unchanged. Never group from this display result.

Attributes are an exact dictionary of all ten fields. Each field is exactly
`{state, value, evidence_ids}`. State is known, unknown, not_supplied or
not_applicable. Non-known states require null value. Known and not_applicable
require 1–128 unique references to supplied evidence; unknown/not_supplied may
have zero. Reject extra keys, unsupported states, malformed IDs and mutations.

Known values:

- family: diffuser, register, grille, linear_diffuser, mechanical_louver,
  architectural_louver, equipment, accessory or other. Classification does not
  establish inclusion by itself; the count engine applies A01.
- type_tag, system, service: nonempty strings, at most 256 characters, no control
  characters. Normalization preserves source text. Canonical grouping collapses
  whitespace and casefolds; system and service remain separate fields.
- work_status: new_install, existing_to_remain, demolition or relocated. Unknown
  uses state=unknown, not an extra work_status value.
- face_size, neck_size, opening_size:
  `{shape, dimensions, unit, original_text}`. Shape rectangular or oval requires
  two ordered dimensions; round requires one. Units in, ft, mm, m. Preserve
  dimension ordering and qualified roles. original_text is nonempty max 512
  characters with no controls.
- assembly_length: `{value, unit, original_text}`, using the same rules.
- slot_count: positive JSON integer at most 1000000, never boolean.

Dimension values are positive decimal **strings**, ASCII syntax
`[0-9]+(?:\.[0-9]+)?`, at most 40 characters, at most 18 fractional places,
value at most 1000000000000. Reject floats, exponents, signs, infinities, zero,
negative values, bools and oversized inputs. No implicit coercion.

Canonical dimensional values use exact rational inches. Factors are in=1,
ft=12, mm=5/127, m=5000/127. Encode each reduced fraction as
`{n: decimal_integer_string, d: positive_decimal_integer_string}`. Size becomes
`{shape, dimensions: [fraction, ...], unit: "in"}`; assembly length becomes
`{value: fraction, unit: "in"}`. No axis sorting, qualifier merging, rounding,
global Decimal-context dependency, or conversion of length/slots into each.

Tests must demonstrate exact equivalent units (25.4 mm = 1 in, 0.3048 m = 1 ft),
unequal inputs that display identically, separate size roles and dimension order,
system versus service, all four missingness states, evidence validation, caller
immutability, finite/bounded inputs, hostile nested/incorrect types and stable
results under altered Decimal precision/rounding/traps. Keep tests meaningful;
do not just mirror implementation branches.

Independent acceptance commands, run from the candidate by Codex:

```
/usr/bin/python3 -B -m unittest discover -s tests/drawing-workspace -p 'test_air_device_attributes.py' -v
/Users/bekim/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3.12 -B -m unittest discover -s tests/drawing-workspace -p 'test_air_device_attributes.py' -v
/opt/homebrew/opt/python@3.14/bin/python3.14 -B -m unittest discover -s tests/drawing-workspace -p 'test_air_device_attributes.py' -v
git diff --check
```

Containment macos_seatbelt; configured default Claude model; INTERNAL data,
owner's standing bounded implementation approval. No private drawings or secrets.
Read/Glob/Grep/Edit/Write only; max 60 actions, 1200 seconds, advisory 20000
output tokens (not a provider-enforced billing/token guarantee). Stop on missing
inputs, unauthorized path needs or inability to meet the contract. Return a
concise candidate report; only Codex may accept it.

## Count contract constraints for subsequent integration

Source identity is exact revision/page/sheet/geometry plus retained input hash.
No scale or calibration is part of each-count dependencies. Tags, overlap,
matching images or model-local IDs never imply physical correspondence. Keep
repeated type tags as distinct observations; untagged clear assemblies may count.
Represent legend/schedule/generic detail/tag-only/unknown depictions explicitly.

Versioned scope declares grouping and required fields. Missing required values
block affected final groups, retaining supported physical subtotals. A field that
is not required does not create a new requirement. Keep physical counts,
remove/reinstall operations, new purchase, declared schedule quantities, unknown
coverage and explicit complete zero distinct. Required scopes/statuses are not
netted. Multiplicity covers explicit represented members and must not double
count them. Relocation has one physical identity and separate operations.

Append-only corrections must bind the exact reviewed generation/source and
propagate through old/new correspondence, multiplicity, schedule and relocation
groups. Unrelated current rows retain their identities. Replaying a producer
does not resurrect retired observations. Model output cannot certify complete
scope or authoritative totals. All accepted results retain source references.
