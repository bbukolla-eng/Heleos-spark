# EVIDENCE-PDF-1 consolidated evidence PDF contract v1

Frozen before implementation, 2026-09-21. Coordinator and only commit owner: Codex.
Checkout: /Users/bekim/Heleos-spark; branch main; base 49b7f57a208e162054b0d856dd299852a1bfce50.

This file is the task ledger. New paths under `.heleos/` are gitignored, so the tracked ledger is this document plus `tests/fixtures/evidence-pdf/specimen-rows.json`.

## Deliverable

A reproducible Mac evidence PDF for the already supported duct, air-device and equipment results. The exporter reads the same current takeoff view as the draft workbook. It writes `evidence.pdf` and `EVIDENCE.txt` into the local draft package. It does not measure, recognize, adjudicate or persist quantities.

Included: separate quantity channels, known/unknown and stale states, internal links to source revision/page/region records, correction history, and the project scope limits below.

Excluded: new mechanical recognition, CSI section acceptance, representative-project proof, native Windows delivery, original-drawing vector crops, live synchronization with Heleos, and any change to approved D01–D10, A01–A12 or E01–E12/EC01–EC20 rules.

Platform: Mac text, link and rendered-page inspection. A passing Mac PDF is not Windows acceptance.

## Inputs

Accepted producers, reused and not reopened:

| Producer | Identity |
| --- | --- |
| Duct | Approved D01–D10. Workbook fixture `tests/fixtures/workbook/duct-request.json`. Current rows from `duct_calculation`: A-1 `2.438400` m / `8.00` ft; A-2 `1.219200` m / `4.00` ft; scope `12.00` / `12.00`. Stale A-2 drops current meters and feet; stored meters stay `1.219200`; scope known `8.00`, complete unknown. Mixed work status keeps group totals `8.00` and `4.00` and leaves scope unknown. Superseded generation in the workbook fixture is `999.00` / `999.00`. |
| Air-device software | Approved A01–A12. Connected fixture starts from local id `device-a`, bbox `[0.1, 0.2, 0.2, 0.3]`, page index 0. The stored observation id is the producer digest of that reading, not a second frozen hex. Before coverage review: physical `1`, remove `0`, reinstall `0`, new purchase unknown, known subtotal `1`, complete total unknown. After exclude: known subtotal `0`, complete unknown, prior known `1` remains in history. |
| Equipment software | E01–E12 / EC01–EC20. Frozen contract `.heleos/equipment-count-2026-09-16/contract.md`. Connected correction: three pumps plus one AHU (`total_each` 4) then exclude `pump-3` (`total_each` 3; groups 2 and 1; prior 4 remains). Channels on an included new field-installed assembly: physical 1, procurement 1, installation 1, remove 0, reinstall 0, component 0. Schedule conflict: declared 4, observed 3, state `unresolved`, known 4, complete unknown. Legend-role change on the pump page: known 1, complete unknown, AHU physical 1. |

Research reused, not re-queried: `docs/research/notebooklm/duct-export-findings-2026-09-14.json` (DUCT-EXPORT-RESEARCH-01 through 06) and `docs/research/notebooklm/duct-export-implementation-2026-09-14.json`. Workbook snapshot practice from `docs/research/notebooklm/workbook-export-implementation-2026-09-16.json`. CSI routing `docs/research/notebooklm/csi-division23-routing.json` and findings `docs/research/notebooklm/csi-division23-findings-2026-09-16.json` are scope context only. They do not add a section identity or a quantity rule. No new NotebookLM submission.

## Expected results

Literal rows are in `tests/fixtures/evidence-pdf/specimen-rows.json`. The PDF text must contain each listed string for that case. Summary text must not contain that case's excluded strings. Named destinations must resolve to a Sources record that contains the listed revision, one-based page and region.

Quantity text:

| Producer value | PDF text |
| --- | --- |
| null / missing complete total | `UNKNOWN` |
| class not calculated | `NOT AVAILABLE` |
| integer count | exact decimal digits, no thousands separator |
| duct feet | the producer two-decimal half-even string |
| duct meters | the producer six-decimal string, or `UNKNOWN` when the row is not current |
| region | four coordinates, six digits after the decimal, half-even, comma-separated |

Numerical tolerance: exact text match. There is no extra rounding band. `UNKNOWN` and `NOT AVAILABLE` are not zero. A stale group's current contribution of `0.00` ft or `0` each is the absence of a current contribution. It does not replace the stored historical meters or an unknown physical count on the row.

## Pass criteria

| ID | Check |
| --- | --- |
| PDF01 | Duct current, stale and mixed specimens match the fixture strings, including separate current feet and stored meters. |
| PDF02 | Air-device specimen keeps physical count, removal, reinstall and new purchase as separate fields. Unknown purchase stays `UNKNOWN`. Exclude keeps history and does not invent a complete total. |
| PDF03 | Equipment specimen keeps physical, procurement, installation, remove, reinstall and component channels separate. Correction history shows superseded `4` and current `3`. Schedule declared `4` and observed `3` stay unresolved. |
| PDF04 | Every specimen source link destination contains the revision SHA-256, one-based page and region. Summary pages exclude superseded totals. |
| PDF05 | Empty input is `NOT AVAILABLE` for duct, air devices and equipment, and `outstanding` for other Division 23. Limits text is present. Two exports of the same view are byte-identical. A drifted feet or count value is rejected. |
| PDF06 | The draft ZIP contains `evidence.pdf` and `EVIDENCE.txt`. Mac inspection records text, link destinations and rendered pages with the commands actually run. |

## What the PDF does not claim

It is a draft snapshot. It is not a bid or an approved estimate. It closes no CSI section. It does not qualify recognition. It does not sync later drawing edits back into Heleos or into an already written file. It does not embed original drawing crops. History is reviewable and is not an input to the current scope lines. Windows, representative-project and release acceptance remain open.

## Verification

`python3 -m unittest discover -s tests/drawing-workspace -p 'test_evidence_pdf.py'`

Also run the existing workbook, workflow and package tests whose export inventory or payload list changes.

Reviewer: independent review by Codex. This implementation does not accept itself. The work stays uncommitted until the commit owner integrates it.
