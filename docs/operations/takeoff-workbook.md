# Takeoff workbook

The drawing workspace's **Export → Download draft takeoff package** includes
`takeoff.xlsx`. The workbook uses current supported duct and air-device results
after the existing source-evidence checks. It runs locally with Python's standard
library and is included in the relocatable Mac/Windows runtime payload.

| Sheet | Use |
| --- | --- |
| Takeoff | Known subtotals and conditional complete totals for each group and scope |
| Measurements | Current duct lengths, exact stored meter text, status and source links |
| Air devices | Physical assemblies, requested counts, removal/reinstall operations and supported purchase quantities |
| History | Saved generation summaries, including superseded values kept outside current formulas |
| Sources | Exact revision, one-based page, sheet, geometry, evidence and JSON record locators |

Click a source link to navigate to its Sources entry. JSON paths refer to files
inside the extracted package. Complete decisions, relationships and original
evidence remain in those files. A multi-observation assembly has consecutive
source entries, one per member, starting at its linked entry.

`UNKNOWN` means unresolved. `NOT AVAILABLE` means that calculation is absent or
the category is unfinished. Neither is zero. Mixed or unknown duct work statuses
keep their supported group totals while the combined scope total stays unknown.
Physical counts do not imply new purchases. Equipment, piping, fittings,
accessories, controls, insulation and broader demolition calculations remain
outstanding; the workbook does not turn draft tag reconciliation into takeoff.

The workbook is a snapshot. Correct and review in Heleos, then regenerate the
package. Workbook edits do not update the project or detect later source changes.
Live formulas support inspection; the deterministic application and approved
rules remain quantity authority. The file and package remain drafts even when
supported scope totals are complete.

Length formulas sum integer micrometers before half-even rounding to hundredths
of a foot. Both individual inputs and relevant aggregates must be below 10^12
micrometers. The exporter fails explicitly if its precision or size contract
cannot represent the current result. It also rejects a projection whose cached
subtotals differ from the deterministic application result. Historical or stale
lengths never become current contributions.

The XLSX contains formulas with snapshot caches and requests recalculation on
open. Independent ZIP/XML, Python, application and spreadsheet-engine checks are
recorded in `.heleos/workbook-export-2026-09-16/`. Native Microsoft Excel and
Windows/NTFS acceptance are separate remaining checks. Recognition qualification,
complete Division 23 coverage and release acceptance remain open.

The same export writes `evidence.pdf`. Its frozen rows, source links and limits
are in the EVIDENCE-PDF-1 contract. The PDF is a snapshot of supported results.
It does not replace this workbook or close a CSI section.
