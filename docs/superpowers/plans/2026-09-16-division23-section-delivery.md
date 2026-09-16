# Division 23 Section Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver each of the 56 known Division 23 guide sections through its complete declared taxonomy, connected takeoff behavior and fixed Mac acceptance endpoint, with every task and pass criterion visible.

**Architecture:** Each linked section plan defines four reviewable deliveries: source/expected-results packet, section result behavior, connected lifecycle/outputs, and representative section qualification. Dependencies consume exact scoped outputs, allowing accepted shared components and independent section work to advance. The full authoritative CSI catalogue remains a separate explicit extension to this known-guide set.

**Tech Stack:** Existing Rust Foundation, Python/JavaScript drawing workspace, JSON contracts, unittest/Node tests, Excel and evidence PDF; no new scheduler or service.

**Spec:** [Section task and acceptance contracts](../specs/2026-09-16-division23-section-task-contracts.md).

## Global Constraints

- Mac is the current implementation and section acceptance target; Windows delivery is separate.
- Preserve approved D01–D10, A01–A12, E01–E12 and imperial outputs; new quantity policy needs its own recorded decision.
- A section means its complete declared source-backed taxonomy, including physical work, services, exclusions and required children.
- Guide identity and project adoption remain distinct; agency numbers do not establish an unverified CSI hierarchy.
- Reuse accepted evidence for unchanged inputs; new requirements create explicit extension tasks or contract versions.
- Codex is the only commit owner; each worker has exact named write paths and returns a candidate.
- Use verified NotebookLM source passages before knowledge-dependent implementation; log external submissions and preserve source identities.
- The cancelled 4specs adoption remains cancelled; an illustrative example does not select a priority.
- No new hook, scheduler, provider access, push or release is authorized by this plan.

---

## What this plan now contains

**56 section plans, 224 named delivery tasks and 1,400 section-qualified mandatory
criterion bindings.** These are 25 scoped checks per section, not 25 separate approvals or compulsory test runs; existing accepted evidence can satisfy the exact covered checks. Each card contains exact deliverables, output dependencies,
case subjects, pass/fail criteria, check procedure, evidence location and review
responsibility. The prior 448 stage entries remain coverage addresses. The
[maintenance-tool plan](2026-09-16-division23-delivery-maintenance.md) is optional
support and is not the section execution plan.

This is the source-dependent delivery plan. DEFINE is actual bounded work with
its own finite contract; it produces each section's source-qualified code-level
implementation packet and independent numeric oracles before dependent code.
The result/workflow/qualification tasks have specified deliverables and criteria
now; a missing body, rule decision or original fixture remains an explicit input,
not a fabricated source fact or threshold. Do not dispatch an unready numeric or
recognition task simply because its plan exists.

## Acceptance, plainly

A task is accepted when **every mandatory criterion in its fixed contract passes,
independent review accepts the recorded candidate, and integration/evidence are
recorded**. Review findings name their criterion, actual/expected result and
reproducer. Optional improvements get separate work. The owner does not need to
approve routine engineering completion again.

A section is accepted when its **entire declared taxonomy and required children**
have the evidence, applicable rules, deterministic results/obligations,
reconciliation, correction/reopen and Excel/evidence PDF behavior required by
SC01–SC08, and its frozen representative Mac qualification passes. A source
inventory, shared counter or generic workbook does not complete a section.

Definition completion and feature acceptance are separate. DEFINE may identify a
missing referenced source, policy or oracle with an exact resolution action.
That accepted definition output cannot be used to pass the missing feature;
required section gaps still block section closure. Missing **primary** body or
unaccounted-for paragraphs fail DEFINE itself.

## Work order and independent delivery

The next approved product implementation stays **EQUIPMENT-COUNT-1**. Bind its
accepted E01–E12/EC01–EC20 behavior to the actual equipment taxons/section tasks it
supports; no blanket claim that it closes equipment guides. It can proceed while
section DEFINE tasks and EVIDENCE-PDF-1 advance on non-overlapping paths.

Within each section: DEFINE releases precise admitted case/source outputs;
RESULT and evidence-only CONNECT work may proceed by those inputs; CONNECT
consumes numeric outputs only where necessary; QUALIFY aggregates full required
scope. An unqualified model blocks its claimed recognition result and final
qualification, not independently verified calculation or evidence-review work.

There is no new global order among all56 rows. Row order is source order.
Illustrative examples select no priority. At each handoff, the coordinator picks
a ready scoped action under CURRENT_STATUS, assigns exact paths and records the
next action. The complete catalogue task adds verified missing sections with the
same plan/acceptance structure.

## File structure and writer ownership

| File / directory | Purpose |
| --- | --- |
| `docs/plans/division-23-task-contracts.json` | Versioned planned contracts, profile distinctions, section/task/case bindings; coordinator owns changes |
| `docs/superpowers/plans/division23-sections/` | Complete readable section cards linked below |
| `docs/engineering/division23/<registered section directory>/` | Named source/taxonomy/rule/verification artifacts; exact expanded paths appear in each card |
| `tests/fixtures/division23/<registered section directory>/` | Approved/public fixture inputs, independent case oracle and acceptance contract |
| `tests/drawing-workspace/test_section_<section slug>.py` | Section-specific deterministic test file, exact path in each card |
| `tests/drawing-workspace/acceptance_section_<section slug>.py` | Real connected/original-source acceptance driver, exact path in each card |
| Existing calculation, source, workflow, UI and export modules | Reuse/extend only under the exact selected implementation packet; one writer per shared path |
| `CURRENT_STATUS.md`, register and completion matrix | Live queue, covered-scope states and prominent plan navigation; Codex only |

Shared paths are not granted to56 concurrent writers. DEFINE's executable child
packet names the actual changed modules and signatures after examining current
interfaces; it does not grant ownership of every reuse surface. Preserve the
existing architecture and accepted evidence. A new reusable engine must have a
bounded component contract and actual consumers, rather than one duplicate
engine per guide or a generic engine claiming unimplemented categories.

## Existing integration interfaces

| Surface | Actual interface to preserve |
| --- | --- |
| Source requirements | `document_requirements.section_headings(page, lines=None)` and `parse_requirements(page)` |
| Verified readings | `DocumentPipeline.start(selections)`, `verified_result(run_id)`, `build_knowledge(run_id)` |
| Section projection | `mechanical_scope.build_view(reading, reviews)` |
| Schedule correspondence | `project_schedule_reconciliation.refresh(workspace, data, sheets)`, `decide(workspace, data, sheets, values, actor, reason)`, `view(workspace, data, sheets, documents)` |
| Application command | `TakeoffWorkflow.command(action, payload)` with `version`, `actor`, `reason`, `values`; preserve version conflict and history behavior |
| Connected results | `TakeoffWorkflow.view()` and `export()`; add only admitted scoped projections |
| Adapter pattern | `project_air_device_takeoff.initialize(data)`, `apply(workspace, data, sheets, action, values, actor, reason)`, `view(workspace, data, sheets)`, `export_files(workspace, data, sheets)`; reuse persistence pattern, not air-device estimating rules |
| Excel | `takeoff_workbook.workbook_sheets(view)` and `workbook_bytes(view)`; current duct/air-device support is not complete general-section export |

Every new producer/consumer signature must be written in the section's
`implementation-plan.md` before its writer starts; both sides use the same
versioned record schema. Original-source recognition and deterministic result
verification have separate evidence fields. A parser success is not a quantity
or recognition acceptance.

## All section plans and endpoints

Each link opens that row's entire task/acceptance sequence. The four suffixes are
`-DEFINE`, `-RESULT`, `-CONNECT`, `-QUALIFY`; the final delivery closes the existing
`-ACCEPT` coverage endpoint only when its complete criteria pass.

| Guide and edition | Section plan / scope | Task stem |
| --- | --- | --- |
| UFGS 23 01 30.41 (05/22) | [HVAC SYSTEM CLEANING](division23-sections/ufgs-230130-41.md) | `D23-UFGS-230130.41` |
| UFGS 23 03 00 (11/25) | [BASIC MECHANICAL MATERIALS AND METHODS](division23-sections/ufgs-230300.md) | `D23-UFGS-230300` |
| UFGS 23 05 15 (05/22, CHG 2: 08/24) | [COMMON PIPING FOR HVAC](division23-sections/ufgs-230515.md) | `D23-UFGS-230515` |
| UFGS 23 05 48.19 (02/25) | [SEISMIC BRACING FOR MECHANICAL SYSTEMS](division23-sections/ufgs-230548-19.md) | `D23-UFGS-230548.19` |
| UFGS 23 05 93 (05/25) | [TESTING, ADJUSTING, AND BALANCING FOR HVAC](division23-sections/ufgs-230593.md) | `D23-UFGS-230593` |
| UFGS 23 07 00 (08/24) | [THERMAL INSULATION FOR MECHANICAL SYSTEMS](division23-sections/ufgs-230700.md) | `D23-UFGS-230700` |
| UFGS 23 08 00 (05/23, CHG 1: 08/24) | [COMMISSIONING OF MECHANICAL[ AND PLUMBING] SYSTEMS](division23-sections/ufgs-230800.md) | `D23-UFGS-230800` |
| UFGS 23 08 01.00 20 (04/06) | [TESTING INDUSTRIAL VENTILATION SYSTEMS](division23-sections/ufgs-230801-00-20.md) | `D23-UFGS-230801.00-20` |
| UFGS 23 09 00 (08/24, CHG 1: 08/25) | [INSTRUMENTATION AND CONTROL FOR HVAC](division23-sections/ufgs-230900.md) | `D23-UFGS-230900` |
| UFGS 23 09 13 (11/15, CHG 2: 05/21) | [INSTRUMENTATION AND CONTROL DEVICES FOR HVAC](division23-sections/ufgs-230913.md) | `D23-UFGS-230913` |
| UFGS 23 09 23.01 (08/24) | [LONWORKS DIRECT DIGITAL CONTROL FOR HVAC AND OTHER BUILDING CONTROL SYSTEMS](division23-sections/ufgs-230923-01.md) | `D23-UFGS-230923.01` |
| UFGS 23 09 23.02 (08/24) | [BACNET DIRECT DIGITAL CONTROL FOR HVAC AND OTHER BUILDING CONTROL SYSTEMS](division23-sections/ufgs-230923-02.md) | `D23-UFGS-230923.02` |
| UFGS 23 09 53.00 20 (02/10, CHG 3: 08/24) | [SPACE TEMPERATURE CONTROL SYSTEMS](division23-sections/ufgs-230953-00-20.md) | `D23-UFGS-230953.00-20` |
| UFGS 23 09 93 (11/15) | [SEQUENCES OF OPERATION FOR HVAC CONTROL](division23-sections/ufgs-230993.md) | `D23-UFGS-230993` |
| UFGS 23 11 20 (05/20) | [FACILITY GAS PIPING](division23-sections/ufgs-231120.md) | `D23-UFGS-231120` |
| UFGS 23 21 13.00 20 (04/06, CHG 2: 11/19) | [LOW TEMPERATURE WATER (LTW) HEATING SYSTEM](division23-sections/ufgs-232113-00-20.md) | `D23-UFGS-232113.00-20` |
| UFGS 23 21 13.23 20 (07/07, CHG 1: 11/19) | [[HIGH][MEDIUM] TEMPERATURE WATER SYSTEM WITHIN BUILDINGS](division23-sections/ufgs-232113-23-20.md) | `D23-UFGS-232113.23-20` |
| UFGS 23 21 23 (05/25) | [HYDRONIC PUMPS](division23-sections/ufgs-232123.md) | `D23-UFGS-232123` |
| UFGS 23 22 26.00 20 (02/10, CHG 1: 05/15) | [STEAM SYSTEM AND TERMINAL UNITS](division23-sections/ufgs-232226-00-20.md) | `D23-UFGS-232226.00-20` |
| UFGS 23 23 00 (08/21) | [REFRIGERANT PIPING](division23-sections/ufgs-232300.md) | `D23-UFGS-232300` |
| UFGS 23 25 00 (05/21) | [CHEMICAL TREATMENT OF WATER FOR MECHANICAL SYSTEMS](division23-sections/ufgs-232500.md) | `D23-UFGS-232500` |
| UFGS 23 30 00 (02/25) | [HVAC AIR DISTRIBUTION](division23-sections/ufgs-233000.md) | `D23-UFGS-233000` |
| UFGS 23 35 16 (02/25) | [MECHANICAL ENGINE[ AND WELDING FUME] EXHAUST SYSTEMS](division23-sections/ufgs-233516.md) | `D23-UFGS-233516` |
| UFGS 23 35 19.00 20 (02/10, CHG 3: 11/24) | [INDUSTRIAL VENTILATION AND EXHAUST](division23-sections/ufgs-233519-00-20.md) | `D23-UFGS-233519.00-20` |
| UFGS 23 44 00.00 10 (02/16) | [CHEMICAL, BIOLOGICAL, AND RADIOLOGICAL (CBR) AIR FILTRATION SYSTEM](division23-sections/ufgs-234400-00-10.md) | `D23-UFGS-234400.00-10` |
| UFGS 23 50 52 (08/26) | [CENTRAL HIGH TEMPERATURE WATER (HTW) GENERATING PLANTS](division23-sections/ufgs-235052.md) | `D23-UFGS-235052` |
| UFGS 23 52 00.01 (08/26) | [LOW PRESSURE (<260 PSIG) WATER HEATING BOILERS (UNDER 6,000,000 BTU/HR INPUT)](division23-sections/ufgs-235200-01.md) | `D23-UFGS-235200.01` |
| UFGS 23 52 00.02 (08/26) | [LOW PRESSURE (<260 PSIG) WATER HEATING BOILERS (OVER 6,000,000 BTU/HR INPUT)](division23-sections/ufgs-235200-02.md) | `D23-UFGS-235200.02` |
| UFGS 23 52 00.03 (08/26) | [STEAM BOILERS AND EQUIPMENT (400,000 - 6,000,000 BTU/HR INPUT)](division23-sections/ufgs-235200-03.md) | `D23-UFGS-235200.03` |
| UFGS 23 52 00.04 (08/26) | [STEAM BOILERS AND EQUIPMENT (OVER 6,000,000 BTU/HR) INPUT](division23-sections/ufgs-235200-04.md) | `D23-UFGS-235200.04` |
| UFGS 23 52 30 (08/26) | [HEAT RECOVERY BOILERS](division23-sections/ufgs-235230.md) | `D23-UFGS-235230` |
| UFGS 23 52 33.01 (08/26) | [STEAM HEATING PLANT WATERTUBE COAL/OIL OR COAL](division23-sections/ufgs-235233-01.md) | `D23-UFGS-235233.01` |
| UFGS 23 52 33.02 (08/26) | [CENTRAL STEAM GENERATING SYSTEM - COMBINATION GAS AND OIL-FIRED](division23-sections/ufgs-235233-02.md) | `D23-UFGS-235233.02` |
| UFGS 23 54 19 (08/21) | [BUILDING HEATING SYSTEMS, WARM AIR](division23-sections/ufgs-235419.md) | `D23-UFGS-235419` |
| UFGS 23 57 10.00 10 (11/19) | [FORCED HOT WATER HEATING SYSTEMS USING WATER AND STEAM HEAT EXCHANGERS](division23-sections/ufgs-235710-00-10.md) | `D23-UFGS-235710.00-10` |
| UFGS 23 63 00.00 (08/22) | [COLD STORAGE REFRIGERATION SYSTEMS](division23-sections/ufgs-236300-00.md) | `D23-UFGS-236300.00` |
| UFGS 23 64 10 (05/25) | [WATER CHILLERS, VAPOR COMPRESSION TYPE](division23-sections/ufgs-236410.md) | `D23-UFGS-236410` |
| UFGS 23 64 26 (11/25) | [CHILLED, CHILLED-HOT, AND CONDENSER WATER PIPING SYSTEMS](division23-sections/ufgs-236426.md) | `D23-UFGS-236426` |
| UFGS 23 65 00 (05/25) | [COOLING TOWERS AND REMOTE EVAPORATIVELY-COOLED CONDENSERS](division23-sections/ufgs-236500.md) | `D23-UFGS-236500` |
| UFGS 23 71 19 (05/18) | [THERMAL ENERGY STORAGE SYSTEM: ICE-ON-COIL](division23-sections/ufgs-237119.md) | `D23-UFGS-237119` |
| UFGS 23 72 00 (05/24) | [ENERGY RECOVERY SYSTEMS](division23-sections/ufgs-237200.md) | `D23-UFGS-237200` |
| UFGS 23 74 33 (05/24) | [DEDICATED OUTDOOR AIR SYSTEMS (DOAS)](division23-sections/ufgs-237433.md) | `D23-UFGS-237433` |
| UFGS 23 75 15 (02/20, CHG 1: 05/24) | [CUSTOM-PACKAGED, AIRCRAFT PRE-CONDITIONED AIR UNITS](division23-sections/ufgs-237515.md) | `D23-UFGS-237515` |
| UFGS 23 76 00 (08/21) | [EVAPORATIVE COOLING SYSTEMS](division23-sections/ufgs-237600.md) | `D23-UFGS-237600` |
| UFGS 23 80 20.00 10 (05/20) | [GAS-FIRED HEATING EQUIPMENT](division23-sections/ufgs-238020-00-10.md) | `D23-UFGS-238020.00-10` |
| UFGS 23 81 00 (05/24) | [DECENTRALIZED UNITARY HVAC EQUIPMENT](division23-sections/ufgs-238100.md) | `D23-UFGS-238100` |
| UFGS 23 81 23 (11/20) | [COMPUTER ROOM AIR CONDITIONING UNITS](division23-sections/ufgs-238123.md) | `D23-UFGS-238123` |
| UFGS 23 81 29 (02/20) | [VARIABLE REFRIGERANT FLOW HVAC SYSTEMS](division23-sections/ufgs-238129.md) | `D23-UFGS-238129` |
| UFGS 23 81 47 (02/25) | [WATER-LOOP AND GROUND-LOOP HEAT PUMP SYSTEMS](division23-sections/ufgs-238147.md) | `D23-UFGS-238147` |
| UFGS 23 82 00.00 20 (02/16, CHG 1: 08/18) | [TERMINAL HEATING UNITS](division23-sections/ufgs-238200-00-20.md) | `D23-UFGS-238200.00-20` |
| UFGS 23 83 00.00 20 (04/06) | [ELECTRIC SPACE HEATING EQUIPMENT](division23-sections/ufgs-238300-00-20.md) | `D23-UFGS-238300.00-20` |
| UFGS 23 84 19.00 (02/18) | [DESICCANT COOLING SYSTEMS](division23-sections/ufgs-238419-00.md) | `D23-UFGS-238419.00` |
| VA 23 05 93.01 (11-01-21) | [DVA/USACE PROJECTS TESTING, ADJUSTING, AND BALANCING FOR HVAC](division23-sections/va-230593-01.md) | `D23-VA-230593.01` |
| VA 23 09 23 (03-01-23) | [DIRECT-DIGITAL CONTROL SYSTEM FOR HVAC](division23-sections/va-230923.md) | `D23-VA-230923` |
| VA 23 21 13 (03-01-23) | [HYDRONIC PIPING](division23-sections/va-232113.md) | `D23-VA-232113` |
| VA 23 36 00 (03-01-23) | [AIR TERMINAL UNITS](division23-sections/va-233600.md) | `D23-VA-233600` |

## Freeze the expected results before dependent code

Each section's DEFINE packet includes these actual data shapes:

- `source-manifest.json`: primary source edition/body hash, notebook/source IDs or
  official URL, source/page/span locators, project-adoption distinctions.
- `taxonomy.json`: independently checked paragraph inventory, nodes/attributes,
  relationships/children, options/exclusions and services, every source disposition.
- `rule-bindings.json`: behavior/taxon to approved rule version/hash or specific
  missing decision with its dependent scope.
- `cases.json`: stable case ID, exact inputs/hashes, independent expected records,
  source/rule bindings, applicable dimensions and original versus injected origin.
- `acceptance-contract.json`: section/task/version, mandatory case/criterion IDs,
  expected outputs, tolerances/metrics, candidate scope, check commands, reviewer.
- `unresolved-inputs.json`: stable gap ID, missing fact/source/policy, dependent
  output IDs, responsible actor, resolution task ID, resolution pass condition, next action and history.
- `implementation-plan.md`: exact allowed paths, consumed/produced signatures,
  executable failing tests, implementation code steps, checks and handoff. These
  code-level instructions depend on the verified section packet; do not copy a
  generic category implementation across unsupported guide variants.

Every “missing” value creates named work; it cannot silently drop a required
case. Use `<section-id>-INPUT-<gap-id>` and the `input_resolution_contracts`
in the task-contract index. Source gaps close on a verified exact body; rule gaps
close on the applicable decision and checked examples; fixture gaps close on
pinned originals and independent truth; metric gaps close on fixed numerical
criteria before evaluation; interface gaps close on exact code/test steps and
compatible producer/consumer schemas. Each resolution task names its own scope,
output and pass condition, so “needs research” cannot become an indefinite gate. Numerical tolerances are not filled from a failing run. Example arithmetic
below tests the comparison mechanism only; it sets no mechanical tolerance.

**Reference comparison code for section test drivers:**

```python
from decimal import Decimal


def assert_case(expected, actual):
    """Compare a frozen test projection; not an application result schema."""
    assert set(expected) == {"rows", "issues"}
    assert set(actual) == {"rows", "issues"}
    assert expected["issues"] == actual["issues"]
    ids = [row["id"] for row in expected["rows"]]
    actual_ids = [row["id"] for row in actual["rows"]]
    assert len(ids) == len(set(ids))
    assert len(actual_ids) == len(set(actual_ids))
    assert set(ids) == set(actual_ids)
    got = {row["id"]: row for row in actual["rows"]}
    for oracle in expected["rows"]:
        row = got[oracle["id"]]
        for key in ("id", "kind", "unit", "source_ids", "rule_ids", "state"):
            assert row[key] == oracle[key], (oracle["id"], key)
        if oracle["state"] == "unknown":
            assert row["value"] is None
            assert oracle["value"] is None
        elif oracle["kind"] == "count":
            assert type(row["value"]) is int
            assert type(oracle["value"]) is int
            assert row["value"] == oracle["value"]
        elif oracle["kind"] == "measure":
            tolerance = Decimal(oracle["absolute_tolerance"])
            expected_value, actual_value = Decimal(oracle["value"]), Decimal(row["value"])
            assert tolerance.is_finite() and tolerance >= 0
            assert expected_value.is_finite() and actual_value.is_finite()
            assert abs(actual_value - expected_value) <= tolerance
        elif oracle["kind"] == "obligation":
            assert row["value"] == oracle["value"]
        else:
            raise AssertionError("Unsupported projection kind: " + oracle["kind"])
```

**Meaningful tests for that comparison contract:**

```python
import copy
import unittest


class OracleComparisonTests(unittest.TestCase):
    def setUp(self):
        self.expected = {"rows": [{"id": "one", "kind": "count", "unit": "ea",
                         "source_ids": ["source-A"], "rule_ids": ["approved-rule"],
                         "state": "known", "value": 2}], "issues": []}

    def test_exact_count_and_source_identity(self):
        assert_case(self.expected, copy.deepcopy(self.expected))
        wrong = copy.deepcopy(self.expected)
        wrong["rows"][0]["source_ids"] = ["source-B"]
        with self.assertRaises(AssertionError):
            assert_case(self.expected, wrong)

    def test_unknown_cannot_be_zero(self):
        self.expected["rows"][0].update(state="unknown", value=None)
        assert_case(self.expected, copy.deepcopy(self.expected))
        wrong = copy.deepcopy(self.expected)
        wrong["rows"][0]["value"] = 0
        with self.assertRaises(AssertionError):
            assert_case(self.expected, wrong)

    def test_case_bound_tolerance(self):
        self.expected["rows"][0].update(kind="measure", unit="ft", value="10.00",
                                       absolute_tolerance="0.01")
        actual = copy.deepcopy(self.expected)
        actual["rows"][0]["value"] = "10.01"
        assert_case(self.expected, actual)
        actual["rows"][0]["value"] = "10.02"
        with self.assertRaises(AssertionError):
            assert_case(self.expected, actual)


if __name__ == "__main__":
    unittest.main()
```

Concatenate the two blocks into a scratch Python file and run it with `python3`;
expected three passing tests. Section implementers use their independently
adjudicated source cases and a documented comparison projection. No arbitrary
sample count or example value above is accepted as a real mechanical quantity.

The connected driver accepts `--phase connected|representative`, `--contract`
and `--output`, as spelled out in each card. It reads candidate/fixture paths
from the pinned contract. Its JSON receipt includes exact input/candidate hashes,
nonempty executed case IDs, per-criterion expected/actual results, exits and
original-source versus injected evidence. Exit0 requires all selected mandatory
criteria pass; missing inputs, skipped checks or empty discovery cannot pass.

## Execute one admitted implementation assignment

- [ ] Read the selected section card and its DEFINE output, current source/rule/
  approval identities and the exact current checkout. Record the bounded task
  brief and one writer per allowed path.
- [ ] Codex reuses verified NotebookLM findings or queries only changed/missing
  source knowledge, verifies passages and logs public submissions. Commit and
  hash-pin the research/implementation packet before a Claude dispatch.
- [ ] Use the owner-selected bounded Claude runner when its authentication and
  containment are available. Codex continues independent work on other paths.
  Record a provider failure and use the authorized fallback; no repeated unchanged
  provider/model attempt is part of section acceptance.
- [ ] Worker implements the frozen child packet using its shown failing test,
  minimal code and exact checks; it returns the complete candidate inventory,
  diff and terminal report without a commit.
- [ ] Codex independently checks the candidate and closes criterion-bound findings,
  then integrates accepted changes and updates the affected task receipt,
  section coverage and CURRENT_STATUS in the same commit. Preserve all unfinished
  siblings and choose the next executable output; do not repeat accepted checks.

## Maintenance that keeps these plans visible

Read this index to select the work, then read only that section card and its task-contract subset. Do not load all56 plans or replay the complete source inventory on each continuation.

AGENTS and CURRENT_STATUS link here directly. Each register row has its own
`delivery_plan` and four `delivery_task_ids`; each matrix row links its section
card. The goal points to this section plan, without carrying a stale immediate
feature assignment. The acceptance policy names the same criteria and definitions.

Change the task-contract source and corresponding section card together when
scope or criteria change; bump the contract version and record why. Record live
execution only in the task ledger and CURRENT_STATUS, then map accepted evidence
to the existing section coverage cells. Planning states in this index describe
this baseline, not a competing live queue. No automatic hook or scheduler is
claimed. The structural verification checks that every guide remains linked,
all224 tasks have criteria/dependencies/output paths, and every acceptance endpoint
has a corresponding QUALIFY delivery.

## Plan self-review and execution handoff

Codex checks the specification against all56 cards, checks the criterion/template
bindings and source/edition identities, scans for unspecified steps, verifies
producer/output and driver-interface consistency, and verifies the comparison
examples. Gaps in source-derived numerical rules remain explicit DEFINE outputs
and downstream input gates; they are not marked as completed mechanical design.

The existing owner preference is bounded Claude implementation with independent
Codex verification. Inline Codex execution remains the approved provider fallback.
This plan does not require another generic approval of that workflow.
