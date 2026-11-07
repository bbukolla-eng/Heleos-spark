# EVIDENCE-PDF-1 independent review

Reviewer: independent review of the uncommitted candidate. This session did not write the implementation. The review did not edit product code, `CURRENT_STATUS.md`, the contract, or the checkpoint. It did not commit, push, merge, or switch branches.

Checkout: `/Users/bekim/Heleos-spark`, branch `main`.
HEAD: `49b7f57a208e162054b0d856dd299852a1bfce50`.
Candidate state at the end of this review: the EVIDENCE-PDF-1 paths below are staged in the index and uncommitted. HEAD is unchanged. An untracked `.cursor/hooks/state/continual-learning.json` is local editor state and is outside this candidate.

## Files reviewed

- `docs/operations/evidence-pdf-2026-09-21-contract.md`
- `tests/fixtures/evidence-pdf/specimen-rows.json`
- `scripts/evidence_pdf.py` (SHA-256 `a73dc20e0ba7daa07678c350f90f74ca70870721d5c1f99940de3737ddc9a6cf`)
- `scripts/takeoff_workflow.py`
- `scripts/workspace_package.py`
- `tests/drawing-workspace/test_evidence_pdf.py`
- `tests/drawing-workspace/test_takeoff_workflow.py`
- `tests/drawing-workspace/test_workspace_package.py`
- `docs/operations/evidence-pdf-2026-09-21.json`
- `docs/operations/evidence-pdf-2026-09-21-checks.log`
- `docs/operations/takeoff-workbook.md`
- `docs/operations/build-checkpoint.json`
- `CURRENT_STATUS.md`

Staged diff scope is those paths. No duct, air-device, or equipment calculation module is in the diff.

## Commands and exit codes

Run from the repository root on the staged bytes:

| Command | Exit |
| --- | --- |
| `python3 -m unittest discover -s tests/drawing-workspace -p 'test_evidence_pdf.py' -v` | 0 (4 tests) |
| `python3 -m unittest discover -s tests/drawing-workspace -p 'test_takeoff_workbook.py' -v` | 0 (13 tests) |
| `python3 -m unittest discover -s tests/drawing-workspace -p 'test_takeoff_workflow.py' -v` | 0 (12 tests) |
| `python3 -m unittest discover -s tests/drawing-workspace -p 'test_workspace_package.py' -v` | 0 (18 tests) |
| `python3 -m unittest discover -s tests/drawing-workspace -p 'test_equipment_count_workflow.py' -v` | 0 (8 tests) |
| `python3 -m unittest discover -s tests/drawing-workspace -p 'test_air_device_workflow.py' -v` | 0 (5 tests) |
| `python3 scripts/verify-build-checkpoint.py --staged` | 0 |

`pdftotext` and `pdfinfo` are not installed. Those commands were not run. No packages were installed.

Specimen generation used `evidence_bytes` on `duct_view()` from `tests/drawing-workspace/test_takeoff_workbook.py`. The PDF was written only under `/tmp/heleos-evidence-review/duct-current.pdf` (6227 bytes, SHA-256 `394012fd2d178194e55234ac1aafdeff565643a963a2c10aa0019a1a951ccecb`). A second call on the same view returned identical bytes.

| Command | Exit |
| --- | --- |
| `swift /tmp/heleos-evidence-review/inspect.swift /tmp/heleos-evidence-review/duct-current.pdf /tmp/heleos-evidence-review/pages` | 0 |
| `qlmanage -t -s 800 -o /tmp/heleos-evidence-review /tmp/heleos-evidence-review/duct-current.pdf` | 0 |

PDFKit reported 8 pages. The duct page (index 1) has two Link annotations. Both resolve to page index 5. Page index 5 is the sources page. PDFKit text on that page contains `DEST SRC-duct-A-1`, `DEST SRC-duct-A-2`, revision `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`, `page=1`, and regions `0.100000,0.200000,0.300000,0.200000` and `0.300000,0.200000,0.400000,0.200000`. PDFKit reports the destination page index and does not print the destination name string. The name-to-page map in the PDF name tree, read with the test parser, assigns both names to page index 5.

Summary text from PDFKit:

- `SCOPE class=duct known_ft=12.00 complete_ft=12.00 unit=ft`
- air-device and equipment `NOT AVAILABLE`
- other Division 23 `NOT AVAILABLE` and `state=outstanding`
- no `999.00` and no `1.219200`

History text from PDFKit contains `relation=superseded known_ft=999.00 complete_ft=999.00`. The summary page does not.

PDFKit also rendered eight PNG page images. The summary render, the sources render, the history render, and the `qlmanage` first-page thumbnail show readable black text for those same figures. A separate parse of the stale duct view shows summary `known_ft=8.00 complete_ft=UNKNOWN`, stored meters `1.219200` on the duct page, and no `999.00` on the summary. An empty project summary uses `NOT AVAILABLE` for duct, air devices, and equipment.

## Contract checks

The frozen contract and `specimen-rows.json` agree on separate quantity channels, known/unknown/stale states, source revision/page/region, correction history, and the scope limits. `UNKNOWN` and `NOT AVAILABLE` stay those words. A current contribution of `0` or `0.00` appears only where the contract and specimen require the absence of a current row, such as an excluded air-device known subtotal of `0` with complete `UNKNOWN`, or a stale duct group `known_ft=0.00` with `complete_ft=UNKNOWN`. That group line is on the duct page. The stale summary stays `known_ft=8.00` and `complete_ft=UNKNOWN`.

`test_evidence_pdf.py` asserts the specimen strings, summary exclusions, history strings, named destination blocks, byte-identical repeats, empty scope, and duct feet drift. It is not a file-exists smoke test. Air-device and equipment cases are built from the existing producer workflows.

`scripts/evidence_pdf.py` copies the current takeoff view and rejects drifted duct feet and an integer known subtotal that does not match current rows. A reviewer probe set an air-device physical count from `1` to `9` while the known subtotal stayed `1`. The writer raised `ValueError`. Approved calculation modules are unchanged.

The draft ZIP change in `scripts/takeoff_workflow.py` adds `evidence.pdf` and `EVIDENCE.txt` beside the existing workbook. `scripts/workspace_package.py` adds `scripts/evidence_pdf.py` to the payload list. The package test expects 84 files.

`CURRENT_STATUS.md` calls the export implemented and uncommitted, says this does not accept the task or close a CSI section, and leaves the next action on independent review. The checkpoint `outcome` is `in_progress`, `review` is null, and the status block matches that task and next action. Receipt hashes matched the staged file bytes at the end of this review. No CSI section is marked complete.

## Findings

Blocking: none.

Notes:

- A PDF `ValueError` during draft export is reported as `workbook_projection`. The export stops.
- The unit file covers duct feet and duct scope drift. The air-device count probe above was a reviewer check, not a new test in the candidate.
- Each section is one page. A view that fills that page raises instead of adding another page.
- `pdftotext` and `pdfinfo` were unavailable.

## Verdict

Accept this uncommitted candidate. Mandatory contract checks passed on the bytes run in this review. This acceptance is of the export candidate only. It does not close a CSI section, qualify recognition, or accept Windows, representative-project, or release work. The candidate remains uncommitted.
