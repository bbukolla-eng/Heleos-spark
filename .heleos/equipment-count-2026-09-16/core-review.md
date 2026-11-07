# EQUIPMENT-COUNT-1 core independent review

Reviewer: `/root/equipment_core_review`; readonly implementation review of EQ01–EQ04. Base: `05c55645fdf01a3238c13097c2f100dfa653799a`. **Final disposition: accepted for the bounded EQ01–EQ04 kernel/adapter scope at the hashes below. All five findings resolved and independently rechecked.** UI, package, connected application and whole-product acceptance remain coordinator/other review responsibilities.

Approved authority: E01–E12 and EC01–EC20 named and hash-pinned by `contract.md`; interface: `API.md`. No external calls or changes to implementation, tests, approved rules or source material by this reviewer.

## Findings and accepted corrections

1. **CR01 — E05 / EQ01: procurement relationship uncertainty erased supported physical counts.** Starting from EC01, add `package` relation over `p1,p2` with `each:null` and no evidence. Observed physical known subtotal 0 with both physical rows null. Expected existing physical counts remain 2 while procurement remains unknown. Relationship errors must affect the supported channel rather than turn supported physical identity into an unknown.
2. **CR02 — EQ03 / E11: a reviewed save could not clear implementation drift.** After a module reload with a changed implementation fingerprint, save the unchanged current request. The adapter returned the prior generation because it compared request equality only, leaving the reviewed view stale indefinitely. Expected a new generation bound to the current implementation; prior history remains immutable.
3. **CR03 — E02/E05 / EQ01: raw package summation ignored overlap and correction.** Two package records, each quantity 1, bound to the same physical member returned 2 packages. Excluding the sole member of one package retained package count 1 even though its row procurement became 0. Expected overlapping physical membership rejected or unresolved, and a corrected false positive not retained as a current purchase.
4. **CR04 — E10 / EQ01: a system total silently coerced unknown physical members to zero.** EC18 with the outdoor unit identity unresolved yielded `systems[0].physical_each == 2`. Expected unknown system total, with any supported subtotal explicitly labelled separately.
5. **CR05 — EC02 / EQ01: executable fixture did not cover its approved evidence roles.** The initial EC02 executable fixture used three identical plan/graphic observations with no schedule declaration. It counted three member IDs as a surrogate for retained plan/detail/schedule references. Expected one physical AHU supported by plan/detail correspondence and a nonphysical schedule declaration, with all three source references retained.

The final candidate resolves CR01 with channel-specific relationship issues; CR02 with implementation-aware no-op detection; CR03 with raw and normalized physical package overlap checks and exclusion-aware package projection; CR04 with explicit null system totals for unknown members; CR05 with distinct plan/detail/schedule contexts, two physical graphic observations and a linked schedule declaration. The original findings above remain as the review history.

## Final independent verification

The initial green suite (calculation 18 / adapter 7 tests) did not cover the reported failures. After the writer declared stable files, the reviewer independently ran:

- `python3 -m unittest discover -s tests/drawing-workspace -p 'test_equipment_calculation.py' -v` — 20 tests passed, exit 0.
- `python3 -m unittest discover -s tests/drawing-workspace -p 'test_project_equipment_takeoff.py' -v` — 8 tests passed, exit 0.
- Standalone assertions reproducing CR01–CR05 — all pass, exit 0. These include package alias overlap after `same` correspondence, unchanged-request save after implementation reload, exact EC02 source roles and retained declaration evidence.
- Standalone relation-only source-drift assertion — affected correspondence becomes incomplete while the unrelated AHU row remains exactly equal and its group total remains 1, exit 0.

No unresolved concrete defects remain against the assigned EQ01–EQ04 criteria. Existing approved literal examples are retained; no expected quantity was derived from calculation output. Rule/source authority remains explicit and recognition remains outside this milestone.

| Reviewed path | SHA-256 |
| --- | --- |
| `scripts/equipment_count_rules.py` | `c7640e217dcbfdfceff048f7bcfb7bab5e097970055cf12f81b41139405abb48` |
| `scripts/equipment_calculation.py` | `d9d237eeb8155b3af607e31b0040a23150db1f85d70dd2bcb77258063a89e551` |
| `scripts/project_equipment_takeoff.py` | `531bb8006d5a0ebb3096173b3da95904cba5545592ab217e17254f0934a4569f` |
| `tests/drawing-workspace/test_equipment_calculation.py` | `1b34308a88c4c68f5af95c6f2461a4401bb1bff9a48436e71ce214b13c98bac2` |
| `tests/drawing-workspace/test_project_equipment_takeoff.py` | `b9ff59aea2268d3a39ebc29c828bc422b973cb04709b4233f53a2f3add65ed2d` |
| `tests/fixtures/equipment-takeoff/connected-equipment-cases.json` | `aab8be464018df6f319ce436795e53a3027019b28d175a51b74a2172ebe125c9` |

Reviewer wrote only this report. No implementation/test edits, commits, network requests or active processes remain. Coordinator retains final integration authority and independent connected checks.
