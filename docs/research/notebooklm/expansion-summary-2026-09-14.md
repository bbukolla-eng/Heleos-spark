# NotebookLM expansion — 2026-09-14

All five working notebooks now contain **250 entries each**. The expansion added **848 net entries**, all reported ready by NotebookLM, and preserved all 402 original source IDs. The original 59-notebook inventory remains unchanged.

| Notebook | Before | Added | Total | Ready |
| --- | ---: | ---: | ---: | ---: |
| [N05 — Drawing Intelligence](https://notebooklm.google.com/notebook/0c0f0f2b-c73d-4764-9c2f-21895648c5bd) | 70 | 180 | 250 | 245 |
| [N07 — Software Engineering](https://notebooklm.google.com/notebook/f0404db5-8c1e-4591-9c9d-2727bd668cbe) | 37 | 213 | 250 | 246 |
| [N10 — HVAC Takeoff Methods](https://notebooklm.google.com/notebook/d71c3a3a-4b2a-415e-ad5a-5026b407b329) | 71 | 179 | 250 | 246 |
| [N11 — Ductwork & Air Distribution](https://notebooklm.google.com/notebook/932527dc-c37a-40ed-89de-4240e8197609) | 61 | 189 | 250 | 234 |
| [Secret Key Leakage in Hugging Face Spaces](https://notebooklm.google.com/notebook/c065f36c-c830-4651-a60e-76dc3649606a) | 163 | 87 | 250 | 248 |
| **Total** | **402** | **848** | **1,250** | **1,219** |

The 31 entries that are not ready were already failed before this work. Existing duplicate aliases, unavailable landing pages and historical editions also remain flagged. Entry counts do not mean 1,250 usable independent documents. Three newly failed imports were removed and replaced, with exact action records.

## Use in the build

- **N05 — Drawing Intelligence:** PDF/CAD coordinates, OCR, drawing interpretation, datasets and evaluation. [Source inventory](expansion-n05-2026-09-14.json).
- **N07 — Software Engineering:** Local desktop architecture, storage, integrity, platform APIs, accessibility and tests. [Source inventory](expansion-n07-2026-09-14.json).
- **N10 — HVAC Takeoff Methods:** Takeoff workflows, government mechanical specifications, estimating and evidence exports. [Source inventory](expansion-n10-2026-09-14.json).
- **N11 — Ductwork & Air Distribution:** Ducts, fittings, dampers, air devices, terminals, insulation, acoustics and installation. [Source inventory](expansion-n11-2026-09-14.json).
- **Secret Key Leakage in Hugging Face Spaces:** Engineering drawing/CAD/BIM studies, model data preparation, training, evaluation and defensive references. [Source inventory](expansion-secret-key-2026-09-14.json).

The secret-key notebook contains much broader engineering and model research than its title suggests. Its title paper concerns historical credential exposure in public model-hosting repositories. The retained findings cover source-backed research and defensive context; no credentials were investigated.

## Verification and limits

N05 retained all 180 new indexed bodies; the secret-key notebook retained all 87; N11 retained all 189 content responses, of which 186 contain text and three expose page-image references. The three original publisher PDFs were visually checked. N07 and N10 used 12 and 10 substantive indexed-body samples respectively, spanning their source families. Ready status alone is not verification of every claim.

Two original N11 web discovery response bodies were lost in an early receipt-ID collision. Their original hashes and recovered requests remain recorded. Every notebook mutation retains its exact request/result evidence, and root independently checked the final source IDs, preserved baseline IDs, indexed content identities and declared limits.

Archived platform documentation, historical HVAC articles and project-specific government specifications require an applicability check before implementation. Follow the [standing research workflow](../../operations/notebooklm-research.md): select relevant sources, query bounded requirements/exceptions/examples, verify supporting text or original pages, and link adopted findings to code and meaningful tests. Approved rules and deterministic calculations retain authority.

The source expansion changed no tested product bytes. The circular-arc preview remains verified on Mac, with 361 checks on each Python runtime and 99 UI checks. Native Windows, representative-project accuracy, remaining mechanical categories and final export approval remain open.

[Machine-readable summary and verification references](expansion-summary-2026-09-14.json). The task ledger is `.heleos/notebooklm-expansion-2026-09-14/`; all writers, helpers and external operations are terminal.
