# Division 23 Delivery Maintenance Implementation Plan

**Optional maintenance only.** The [master section delivery plan](2026-09-16-division23-section-delivery.md) and its 56 section cards define mechanical work and acceptance. Do not select these tooling tasks as a substitute for section implementation.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make accepted work, the live queue and full known section coverage inspectable without replaying completed tasks or inventing new review gates.

**Architecture:** Keep source-qualified coverage in the existing register and actual task contracts in a small local ledger. A read-only validator and renderer expose explicit states and output dependencies; they never accept work, change a goal, schedule a run or promote a coverage slot. CURRENT_STATUS.md remains the sole live queue unless a later explicit migration reconciles all consumers.

**Tech Stack:** Python 3.9-compatible standard library, unittest, JSON and Markdown; no new dependency or service.

**Spec:** [Division 23 delivery structure](../specs/2026-09-16-division23-delivery-structure.md). Read it with [acceptance](../../operations/acceptance.md) and root AGENTS.md.

## Global Constraints

- Mac is the current implementation and engineering acceptance target.
- Approved D01–D10, A01–A12, E01–E12 and imperial outputs remain authoritative.
- Accepted work is reused; a demonstrated defect or changed relevant input is required to reopen affected work.
- A user illustration does not select a priority or create an example-specific deliverable.
- The cancelled 4specs adoption remains cancelled.
- Codex is the only commit owner; workers write only their assigned paths.
- NotebookLM supports source verification before knowledge-dependent work; coordination-only checks need no new mechanical research.
- No push, deployment, credential change, billing change or new automation is authorized by this plan.
- New continuity tooling uses Python 3.9-compatible standard-library code and unittest.


---

## Already delivered by DELIVERY-STRUCTURE-PLAN-1

The audit and documentation correction identify seven specific failures in the
coordination records. The provider instructions now distinguish an expected
UNKNOWN from a failed mandatory criterion and select the actual headless runner.
The goal text is stable, status is shortened, original status bytes are archived,
and historical workbook/air-device receipts have a joined terminal checkpoint.
All 56 known guide sections are visible in the completion matrix. The 448 addresses
are planned coverage slots; they are not executable assignments.

Those are documentation deliverables. **The two implementation tasks below are
planned, not implemented.** They can proceed alongside non-overlapping equipment
work and must not become a prerequisite for mechanical implementation.

## File map and ownership

| Path | Responsibility | Sole writer |
| --- | --- | --- |
| `scripts/build_work_ledger.py` | Pure metadata validation and explicit ready-state projection | Task 1 worker |
| `tests/continuity/test_build_work_ledger.py` | Fixed behavioral tests for task state/dependencies | Task 1 worker |
| `docs/plans/build-work-ledger.json` | Small actual-task index; historical acceptance points to original receipts | Task 1 worker, later Codex coordinator |
| `scripts/build-work-status.py` | Read-only CLI rendering task and all section states | Task 2 worker |
| `tests/continuity/test_build_work_status.py` | View/CLI tests | Task 2 worker |
| `docs/plans/build-work-snapshot.md` | Generated readable snapshot, not an independent queue | Codex after Task 2 checks |
| `CURRENT_STATUS.md` | Live primary task and next action | Codex only |
| `.heleos/delivery-ledger-1/`, `.heleos/delivery-view-1/` | Exact manifests/checks/reviews for the respective task | Assigned worker report, Codex acceptance |

Do not edit application code, mechanical rules, completed receipts, hooks, CI,
provider controls or the section source identities in either assignment. Never
create one assignment per coverage cell by mechanical expansion.

## Execution contract and review endpoint

Use the owner-selected bounded Claude workflow with exact-base/path containment;
Codex does independent work on other paths and independently checks the candidate.
If the provider remains unavailable, record the terminal limitation and use the
already authorized fallback. No credential retry is part of this plan. These
coordination tasks reuse existing records and introduce no mechanical claims, so
no fresh NotebookLM query or external submission is needed. A later mechanical
task must follow the source-verification workflow.

For both tasks, freeze the shown tests and input hashes in the task brief before
dispatch. Blocking findings cite the criterion IDs below or shared policy. Once
those findings close and affected checks pass, record independent acceptance and
integration; no recursive general review is required. Codex is the sole commit
owner. Product work resumes/continues at EQUIPMENT-COUNT-1 throughout.

### Task 1: Validate finite task contracts and scoped output dependencies

**Files:** Create `scripts/build_work_ledger.py`, `tests/continuity/test_build_work_ledger.py`, `docs/plans/build-work-ledger.json`. Codex updates `CURRENT_STATUS.md` and the task receipt only after acceptance.

**Interfaces:** Consumes JSON objects with `schema_version: 1` and `tasks: list[dict]`. Produces `contract_hash(contract: dict) -> str`, `validate_ledger(ledger: dict) -> list[str]`, `ready_tasks(ledger: dict) -> list[str]`. An empty error list means metadata consistency, not independently proven correctness or permission to integrate. Dependencies are exact `(task, output, scope)` bindings; resolve any conditional requirement when freezing the task, before placing it in this list.

**Finish line:** L01 accepted software does not reappear due to blocked recognition; L02 an accepted scoped output may unblock a consumer while its producer is still running; L03 missing/failed mandatory criteria prevent acceptance but an expected UNKNOWN can pass; L04 changed contracts, duplicate IDs and unbound dependencies reject; L05 deferred Windows does not appear in the ready list. Structural tests plus independent Codex review and integration close this task. No model, product or platform claim is added.

- [ ] **Step 1: Record task brief and exact base.** Run `git rev-parse HEAD` and `git status --short` in the assigned checkout. Record the allowed files above, spec/AGENTS/skill hashes, L01–L05, current source-register and terminal-checkpoint hashes in `.heleos/delivery-ledger-1/brief.json`. Preserve any existing path edits; dispatch only after resolving a writer collision.

- [ ] **Step 2: Create the test file with these independently specified behaviors.**

```python
import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from build_work_ledger import contract_hash, ready_tasks, validate_ledger


def ready_case():
    c = {"version": 1, "deliverable": "unknown-source display", "scope": "one fixture",
         "exclusions": ["recognition"], "inputs": [{"path": "fixture.json", "sha256": "a" * 64}],
         "write_paths": ["scripts/example.py"], "writer": "worker", "reviewer": "Codex",
         "criteria": [{"id": "C01", "mandatory": True, "expected": "UNKNOWN",
                       "check": "python3 -m unittest test_unknown", "evidence": "check.json"}]}
    return {"id": "NEXT", "state": "ready", "priority": 1, "contract": c,
            "contract_sha256": contract_hash(c), "dependencies": [], "outputs": []}


class LedgerTests(unittest.TestCase):
    def test_accepted_software_and_blocked_recognition_do_not_block_next(self):
        old = {"id": "SOFTWARE", "state": "accepted", "priority": 0,
               "inherited_acceptance": {"receipt": "original.json", "integration_commit": "a" * 40,
                                        "scope": "software only"}}
        blocked = {"id": "RECOGNITION", "state": "blocked_input", "priority": 0,
                   "next_action": "Define changed input and fixed original-source metric"}
        ledger = {"schema_version": 1, "tasks": [old, blocked, ready_case()]}
        self.assertEqual(validate_ledger(ledger), [])
        self.assertEqual(ready_tasks(ledger), ["NEXT"])

    def test_partial_accepted_output_suffices(self):
        producer = ready_case()
        producer.update(id="PRODUCER", state="in_progress", outputs=[
            {"id": "source-index", "scope": "drawing A", "state": "accepted", "receipt": "index.json"}])
        consumer = ready_case()
        consumer["dependencies"] = [{"task": "PRODUCER", "output": "source-index", "scope": "drawing A"}]
        ledger = {"schema_version": 1, "tasks": [producer, consumer]}
        self.assertEqual(ready_tasks(ledger), ["NEXT"])
        consumer["dependencies"][0]["scope"] = "drawing B"
        self.assertIn("NEXT: unresolved dependency output", validate_ledger(ledger))

    def test_unknown_is_expected_but_mandatory_failure_blocks_closure(self):
        t = ready_case()
        t.update(state="accepted", acceptance={"independent_review": "review.json",
                 "integration_commit": "b" * 40, "candidate_identity": "c" * 64,
                 "results": {"C01": "pass"}})
        ledger = {"schema_version": 1, "tasks": [t]}
        self.assertEqual(validate_ledger(ledger), [])
        t["acceptance"]["results"]["C01"] = "fail"
        self.assertIn("NEXT: mandatory criterion not passed", validate_ledger(ledger))

    def test_contract_change_requires_new_identity(self):
        t = ready_case()
        t["contract"]["criteria"][0]["expected"] = 1
        self.assertIn("NEXT: contract identity mismatch", validate_ledger({"schema_version": 1, "tasks": [t]}))

    def test_duplicate_ids_and_unbound_dependency_rejected(self):
        t = ready_case()
        self.assertEqual(validate_ledger({"schema_version": 1, "tasks": [t, copy.deepcopy(t)]}),
                         ["ledger: invalid or duplicate task IDs"])
        t["dependencies"] = [{"task": "TAX"}]
        self.assertIn("NEXT: dependency missing output binding",
                      validate_ledger({"schema_version": 1, "tasks": [t]}))

    def test_deferred_windows_never_selected(self):
        ledger = {"schema_version": 1, "tasks": [ready_case(),
                  {"id": "WINDOWS-DELIVERY-1", "state": "deferred", "priority": 0}]}
        self.assertEqual(ready_tasks(ledger), ["NEXT"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Confirm the red test.** Run `python3 -m unittest discover -s tests/continuity -p 'test_build_work_ledger.py' -v`. Expected: import failure for the not-yet-created `build_work_ledger`; retain the actual exit and output.

- [ ] **Step 4: Create the module.**

```python
"""Read-only task metadata checks. This does not adjudicate test evidence."""
import hashlib
import json

STATES = {"planned", "definition_pending", "ready", "in_progress", "review",
          "accepted", "blocked_input", "deferred"}
ACTIVE = {"ready", "in_progress", "review"}


def contract_hash(contract: dict) -> str:
    raw = json.dumps(contract, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def validate_ledger(ledger: dict) -> list[str]:
    errors = []
    if not isinstance(ledger, dict) or ledger.get("schema_version") != 1:
        return ["ledger: unsupported schema"]
    tasks = ledger.get("tasks")
    if not isinstance(tasks, list):
        return ["ledger: tasks must be a list"]
    ids = [t.get("id") for t in tasks if isinstance(t, dict)]
    if (len(ids) != len(tasks) or any(not isinstance(i, str) or not i for i in ids)
            or len(set(ids)) != len(ids)):
        return ["ledger: invalid or duplicate task IDs"]
    by_id = {t["id"]: t for t in tasks}
    for t in tasks:
        tid = t["id"]
        state = t.get("state")
        if state not in STATES:
            errors.append(tid + ": invalid state")
        if type(t.get("priority")) is not int:
            errors.append(tid + ": priority must be an integer")
        if state in {"definition_pending", "blocked_input"} and not t.get("next_action"):
            errors.append(tid + ": missing bounded next action")
        inherited = t.get("inherited_acceptance")
        if inherited is not None and (state != "accepted" or not isinstance(inherited, dict)
                or not all(inherited.get(k) for k in ("receipt", "integration_commit", "scope"))):
            errors.append(tid + ": invalid inherited acceptance")
        # Original terminal receipts preserve accepted history without manufacturing
        # new contracts or replaying old tests. Codex verifies these receipts.
        if state in ACTIVE or (state == "accepted" and inherited is None):
            c = t.get("contract")
            if not isinstance(c, dict) or not all(c.get(k) for k in (
                    "version", "deliverable", "scope", "exclusions", "inputs",
                    "write_paths", "writer", "reviewer", "criteria")):
                errors.append(tid + ": incomplete contract")
            else:
                if t.get("contract_sha256") != contract_hash(c):
                    errors.append(tid + ": contract identity mismatch")
                criteria = c["criteria"]
                if not isinstance(criteria, list) or any(
                        not isinstance(x, dict) or not all(x.get(k) for k in
                        ("id", "check", "evidence")) or "expected" not in x
                        or type(x.get("mandatory")) is not bool for x in criteria):
                    errors.append(tid + ": invalid criteria")
                elif len({x["id"] for x in criteria}) != len(criteria):
                    errors.append(tid + ": duplicate criteria")
                elif state == "accepted":
                    a = t.get("acceptance", {})
                    if not isinstance(a, dict) or not all(a.get(k) for k in (
                            "independent_review", "integration_commit", "candidate_identity")):
                        errors.append(tid + ": incomplete acceptance")
                    elif any(a.get("results", {}).get(x["id"]) != "pass"
                             for x in criteria if x["mandatory"]):
                        errors.append(tid + ": mandatory criterion not passed")
        outputs = t.get("outputs", [])
        if not isinstance(outputs, list) or any(not isinstance(o, dict) or
                not all(o.get(k) for k in ("id", "scope", "state")) for o in outputs):
            errors.append(tid + ": invalid outputs")
        elif len({o["id"] for o in outputs}) != len(outputs):
            errors.append(tid + ": duplicate outputs")
        elif any(o["state"] == "accepted" and not o.get("receipt") for o in outputs):
            errors.append(tid + ": accepted output missing receipt")
        deps = t.get("dependencies", [])
        if not isinstance(deps, list):
            errors.append(tid + ": dependencies must be a list")
            continue
        for d in deps:
            if not isinstance(d, dict) or not all(d.get(k) for k in ("task", "output", "scope")):
                errors.append(tid + ": dependency missing output binding")
                continue
            producer = by_id.get(d["task"])
            outputs = producer.get("outputs", []) if producer else []
            matches = [o for o in outputs if isinstance(o, dict)
                       and o.get("id") == d["output"] and o.get("scope") == d["scope"]]
            if len(matches) != 1:
                errors.append(tid + ": unresolved dependency output")
            elif state in ACTIVE and matches[0].get("state") != "accepted":
                errors.append(tid + ": required output not accepted")
    # Dependency cycles are errors, not a reason to silently select another task.
    if not errors:
        visiting, visited = set(), set()
        def visit(tid):
            if tid in visiting:
                return False
            if tid in visited:
                return True
            visiting.add(tid)
            for d in by_id[tid].get("dependencies", []):
                if not visit(d["task"]):
                    return False
            visiting.remove(tid)
            visited.add(tid)
            return True
        if any(not visit(tid) for tid in by_id):
            errors.append("ledger: dependency cycle")
    return errors


def ready_tasks(ledger: dict) -> list[str]:
    errors = validate_ledger(ledger)
    if errors:
        raise ValueError("; ".join(errors))
    # A deliberate ready state is required; this function does not promote work.
    return [t["id"] for t in sorted(ledger["tasks"], key=lambda t: (t["priority"], t["id"]))
            if t["state"] == "ready"]
```

- [ ] **Step 5: Seed actual task records from verified terminal history.** Reconcile the input receipt against live state first; if a task advanced since this plan, retain its newer terminal result and record the difference. Execute this Python from the assigned project root; this is the initial projection, not a replacement for original acceptance evidence:

```python
import hashlib
import json
from pathlib import Path
receipt_path = Path("docs/operations/delivery-checkpoint-2026-09-16.json")
receipt = json.loads(receipt_path.read_text())
ref = {"path": str(receipt_path), "sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest()}
tasks = []
for r in receipt["records"]:
    if r["state"] == "accepted_local_software":
        tasks.append({"id": r["id"], "state": "accepted", "priority": 0,
                      "inherited_acceptance": {"receipt": ref, "integration_commit": r["integration_commit"],
                                               "scope": r["scope"]}, "outputs": [], "dependencies": []})
    else:
        tasks.append({"id": r["id"], "state": "blocked_input", "priority": 20,
                      "next_action": r["next_action"], "outputs": [], "dependencies": []})
tasks.extend([
 {"id": "EQUIPMENT-COUNT-1", "state": "definition_pending", "priority": 1,
  "next_action": "Bind approved E01–E12/EC01–EC20 to current interfaces, exact writer paths and connected correction/reopen/export cases; then implement without another owner rule approval."},
 {"id": "EVIDENCE-PDF-1", "state": "definition_pending", "priority": 2,
  "next_action": "Freeze supported-result specimen rows, original-source links, unknown/stale display and PDF open checks before implementation."},
 {"id": "D23-CSI-CATALOGUE-1", "state": "blocked_input", "priority": 3,
  "next_action": "Obtain an authorized edition-specific complete hierarchy and reconcile the 56 guide records; no cancelled source adoption."},
 {"id": "WINDOWS-DELIVERY-1", "state": "deferred", "priority": 100}
])
Path("docs/plans/build-work-ledger.json").write_text(json.dumps(
    {"schema_version": 1, "authority": "projection only; CURRENT_STATUS.md remains the live queue",
     "tasks": tasks}, indent=2) + "\n")
```

- [ ] **Step 6: Verify L01–L05 and the real ledger.** Run the Step 3 command; all six tests must pass. Run `python3 -c 'import json,sys; sys.path.insert(0,"scripts"); from build_work_ledger import validate_ledger; errors=validate_ledger(json.load(open("docs/plans/build-work-ledger.json"))); print(errors); sys.exit(bool(errors))'`; expected `[]`, exit 0. Codex inspects every inherited receipt and integration identity once, without repeating completed product tests.

- [ ] **Step 7: Close the bounded review and integrate.** Worker returns candidate inventory/diff/check exits; Codex independently runs Step 6, resolves L01–L05 findings, records acceptance and updates CURRENT_STATUS with product work still primary. Codex stages only these three files plus CURRENT_STATUS and commits `feat: validate scoped build task contracts`. No worker commit or push.

### Task 2: Show every known section alongside the actual task queue

**Files:** Create `scripts/build-work-status.py`, `tests/continuity/test_build_work_status.py`; Codex generates `docs/plans/build-work-snapshot.md` and updates CURRENT_STATUS after acceptance. Read the register and Task 1 ledger; do not mutate either.

**Interfaces:** Consumes Task 1 `validate_ledger(ledger: dict) -> list[str]` and `ready_tasks(ledger: dict) -> list[str]`. Produces `render_status(ledger: dict, register: dict) -> str` and `main(argv=None) -> int`. CLI returns 0 for valid output, 1 for invalid metadata/shape, 2 for read/JSON failure. It prints only; it has no scheduler, Git operation, network call or acceptance mutation.

**Finish line:** V01 all 56 currently known guide records appear exactly once with eight explicit coverage states; V02 planned slots never become ready assignments; V03 primary task and missing definition action remain visible; V04 malformed/read-error inputs give documented exits. The output accurately states the hierarchy limit. Independent review and integration close this view task; it does not close any mechanical section.

- [ ] **Step 1: Record the task brief.** Pin the accepted Task 1 commit/module and live register/ledger bytes, V01–V04 and the two worker paths in `.heleos/delivery-view-1/brief.json`. Reconcile any Task 1 interface change before dependent edits.

- [ ] **Step 2: Write the tests.**

```python
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
p = Path(__file__).resolve().parents[2] / "scripts" / "build-work-status.py"
spec = importlib.util.spec_from_file_location("build_work_status", p)
view = importlib.util.module_from_spec(spec)
spec.loader.exec_module(view)


class ViewTests(unittest.TestCase):
    def test_all_sections_visible_without_promoting_slots(self):
        ledger = {"schema_version": 1, "tasks": [
            {"id": "EQUIPMENT-COUNT-1", "state": "definition_pending", "priority": 1,
             "next_action": "Bind approved examples to connected lifecycle cases"}]}
        stages = ("TAX", "EVID", "RULE", "CALC", "RECON", "EDIT", "OUT", "ACCEPT")
        register = {"sections": [{"id": str(i), "title": "A | B", "tasks":
                     {k: {"status": "planned"} for k in stages}} for i in range(56)]}
        out = view.render_status(ledger, register)
        self.assertIn("Known guide records: 56", out)
        self.assertIn("Ready assignments: none", out)
        self.assertEqual(out.count("planned"), 56 * 8)
        for i in range(56):
            self.assertEqual(out.count("| " + str(i) + " | A"), 1)
        self.assertIn("A \\| B", out)
        self.assertIn("EQUIPMENT-COUNT-1", out)

    def test_read_error_and_invalid_ledger_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            ledger, register = root / "ledger.json", root / "register.json"
            argv = ["--ledger", str(ledger), "--register", str(register)]
            self.assertEqual(view.main(argv), 2)
            ledger.write_text(json.dumps({"schema_version": 2, "tasks": []}))
            register.write_text(json.dumps({"sections": []}))
            self.assertEqual(view.main(argv), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Confirm the red test.** Run `python3 -m unittest discover -s tests/continuity -p 'test_build_work_status.py' -v`; expected missing CLI file/import failure, with the actual exit retained.

- [ ] **Step 4: Create the read-only CLI.**

```python
"""Print a checked local work snapshot; never schedule, accept or write tasks."""
import argparse
import json
from pathlib import Path
from build_work_ledger import ready_tasks, validate_ledger


def render_status(ledger: dict, register: dict) -> str:
    ready = ready_tasks(ledger)
    sections = register["sections"]
    lines = ["# Build work snapshot", "", "Ready assignments: " + (", ".join(ready) or "none"), "",
             "Known guide records: " + str(len(sections)) + "; complete CSI hierarchy remains unverified.", "",
             "| Task | State | Next action |", "| --- | --- | --- |"]
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    for t in sorted(ledger["tasks"], key=lambda x: (x["priority"], x["id"])):
        lines.append("| " + " | ".join(cell(t.get(k, "")) for k in ("id", "state", "next_action")) + " |")
    lines.extend(["", "Coverage slots are not ready assignments. SC01–SC08 define section closure.", "",
                  "| Section | Title | TAX | EVID | RULE | CALC | RECON | EDIT | OUT | ACCEPT |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"])
    for s in sections:
        values = [s["id"], s["title"]] + [s["tasks"][k]["status"] for k in
                  ("TAX", "EVID", "RULE", "CALC", "RECON", "EDIT", "OUT", "ACCEPT")]
        lines.append("| " + " | ".join(map(cell, values)) + " |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--register", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        ledger = json.loads(args.ledger.read_text())
        register = json.loads(args.register.read_text())
    except (OSError, ValueError) as exc:
        print("Cannot read snapshot: " + str(exc))
        return 2
    try:
        errors = validate_ledger(ledger)
        if errors:
            print("\n".join(errors))
            return 1
        print(render_status(ledger, register), end="")
    except (KeyError, TypeError, ValueError) as exc:
        print("Invalid snapshot shape: " + str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Check and generate the view.** Run `python3 -m unittest discover -s tests/continuity -p 'test_build_work_*.py' -v`; all eight tests must pass. Run `python3 scripts/build-work-status.py --ledger docs/plans/build-work-ledger.json --register docs/plans/division-23-section-register.json > .heleos/delivery-view-1/snapshot.md`; require exit 0 before copying to `docs/plans/build-work-snapshot.md`. Check each register ID occurs once in its section table and all 448 states agree with the input. Do not hand-edit generated statuses.

- [ ] **Step 6: Close the bounded review and integrate.** Codex independently repeats Step 5 against the candidate, verifies V01–V04 and no writes outside named paths, and records evidence. Update CURRENT_STATUS with the new view link while retaining the actual product next action. Stage only the two source/test files, generated snapshot and status; commit `feat: show complete known section work coverage`. Do not install a hook or recurring job.

## Product lineup and section endpoints

This maintenance plan is separate from product implementation plans. It does not
replace the approved equipment implementation with tooling. The current lineup is:

| Work | Next finite deliverable | What closes it / what stays separate |
| --- | --- | --- |
| EQUIPMENT-COUNT-1, primary | Approved physical/package/procurement/installation counts connected to review, correction, reopening and source exports | EC01–EC20 plus frozen connected lifecycle cases pass; independent integration. Recognition and whole equipment sections stay separately visible. |
| EVIDENCE-PDF-1, independent output | Supported results and original-source evidence assembled into a local PDF | Frozen specimen rows/totals/unknown states and source destinations agree; PDF opens. It supports section OUT cells, not whole-section closure. |
| Known section taxonomy and rule preparation | Selected section's full source inventory mapped to required types/variants/relations/services/exclusions | Every clause in declared source scope has an explained disposition and exact source evidence. New estimating decisions block only affected rules. |
| D23-CSI-CATALOGUE-1 | Authorized complete edition-specific hierarchy reconciled to known guides | Every supplied number/title/hierarchy edge reconciles without forced mappings. Missing catalogue blocks the full-coverage claim only. |
| Recognition qualification | Changed bounded experiment with original-source truth and frozen metrics | Every named mandatory metric passes; parked failed runs are not repeated unchanged. |

All 56 currently known section rows and source-bound work prompts are listed in
[the completion matrix](../../plans/division-23-completion-matrix.md). The catalogue
task adds genuinely verified missing sections/subsections. For each section,
SC01–SC08 apply to its entire declared taxonomy; eight coverage dimensions do not
require eight separate code changes. Section acceptance requires full scope,
connected correction/reopen/exports and its own frozen representative Mac dossier.
New editions or variants extend the scope by explicit version; they do not erase
previously accepted evidence.

## Maintenance and handoff

On a task terminal event, Codex records actual checks/review/integration, links the
accepted output to its covered scope, advances the status queue, regenerates any
affected view and commits the checkpoint together. Source updates require source
verification; unchanged evidence is reused. Only changed inputs, a concrete defect
or an explicitly open criterion justify another check. Status is read once at
startup and again on changed bytes, handoff or context recovery.

No global executable queue migration or enforcement hook is part of these tasks.
Before any future migration, inventory consumers, prove no conflicting queues and
make the switch an explicit bounded change. Until then the new ledger is a checked
projection and CURRENT_STATUS.md remains authoritative.

## Plan self-review

Coordinator checks this plan against the spec: audit fixes are delivered in the
coordination docs; fixed acceptance/output dependencies are Task 1; complete known
visibility is Task 2 and the matrix; full section completion is SC01–SC08; product
implementation and catalogue acquisition remain explicit separate work. Confirm
there are no missing interfaces or unspecified code steps. Run the shown examples
in isolated scratch to check their syntax and behavioral assertions; record that
as plan-example verification, never as installation of the planned tools.

The owner's existing execution preference is bounded Claude plus independent
Codex checks. Inline Codex execution remains the authorized provider fallback.
This handoff does not introduce another approval requirement.
