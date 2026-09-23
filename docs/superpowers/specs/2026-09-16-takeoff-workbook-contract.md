# Connected takeoff workbook

WORKBOOK-EXPORT-1 advances roadmap 0.6 from the accepted local application at
`4f0ca41ffd00bbd828cd8d83ad4d843cc2ad2c94`. The authorized build delivers a
reviewable draft Excel workbook in the existing source evidence ZIP. It does not
change D01–D10, A01–A12, recognition qualification or release authority.

## Product contract (Codex owns projection and connection)

Use the current application view, after existing export evidence verification.
Sheets: Takeoff, Measurements, Air devices, History, Sources. Separate ft and
each; list each supported group with known subtotal and conditional final.
Unknown/stale/incomplete finals say UNKNOWN; absent classes say NOT AVAILABLE.
Retain work status and unresolved attributes. Never infer purchases from physical
counts. Superseded generations are history only and cannot feed current totals.

Length formulas sum integer micrometers before converting with half-even rounding
to hundredths of a foot (3048 micrometers per hundredth, half tie 1524). Each
positive input and each aggregate must be strictly below 10^12 micrometers.
Invalid or unsupported workbook input blocks its output instead of displaying a
plausible zero. Cached values must match the deterministic application snapshot.
Live formulas are an inspectable projection, not a second source of takeoff truth.
Workbook edits never save corrections to the application. Regenerate after app
correction/review; a static workbook cannot learn about later source changes.

Source links navigate to exact revision/page/geometry/evidence locators on Sources;
complete retained records remain in the accompanying JSON/evidence ZIP. Do not
open network links or manufacture inaccessible private document URLs.

Acceptance: focused projection/kernel reconciliation, zero vs unknown, changed
source/stale guards, duplicate/history isolation, count grouping and operations,
portable package imports, independent formula recalculation and mutation/restore,
visual inspection. Native Excel/Windows acceptance remains explicitly separate.

## Bounded writer assignment (Claude owns only these files)

Implement `scripts/xlsx_workbook.py` and
`tests/drawing-workspace/test_xlsx_workbook.py`, Python 3.9+ standard library only.
Codex owns all other paths. You are not alone; do not revert others' changes.
Do not implement takeoff semantics or change quantity rules. Return no extra files,
commits or external calls. Codex runs the tests independently.

Public API: `write_workbook(sheets, title='Heleos takeoff') -> bytes`, raising
`ValueError` on invalid input. `sheets` is a list of dictionaries, each with
`name`, `rows` (list of rows of cells), optional `widths` (column widths),
`freeze_rows` (default 0) and `filter_row` (optional 1-based table header row).
Cells are None, literal strings, integers, or these dictionaries:

- `{'text': str, 'style': style, 'location': optional_internal_A1_link}`
- `{'number': int_or_plain_decimal_string, 'style': style}`
- `{'formula': expression_without_leading_equals, 'cached': int_or_decimal_string_or_text,
  'cache_type': 'number' or 'string', 'style': style}`

Styles: body (default), title, header, note, warning, input, quantity (0.00),
integer (0), link. Use Arial 10; title 14; dark blue header/white type, amber
warning, pale input fill, restrained light borders, wrapped text, hidden gridlines,
frozen specified header rows and explicit widths. No merged cells or decorative
graphics. Internal links only, e.g. `'Sources'!A6`, validated against sheet names
and A1 limits. Do not create external hyperlink relationships.

Use ElementTree for XML, BytesIO and ZIP_STORED with fixed ZipInfo timestamp
(1980-01-01), sorted part names, explicit attributes, and close the archive before
reading bytes. Include correct content types, package/workbook relationships,
styles, workbook and worksheets. Literal labels including leading =,+,-,@ are
inline strings. Formula cache numeric values use v; string cache uses t=str.
Set automatic full recalculation on load. No macros, external links, shared
strings, formula calculation or third-party dependency.

Bound inputs: 1–16 unique case-insensitive legal Excel sheet names, <=50,000 rows,
<=256 columns, <=200,000 cells, <=32,000 characters per string. Reject XML-invalid
characters, booleans as numbers, nonfinite numbers, and numeric values with more
than 15 significant decimal digits. Reject leading =, external reference brackets,
URLs and DDE pipes in formulas. Only generated arithmetic/cell references, quoted
sheet names, numbers, commas, comparisons, string literals and calls to
IF, IFERROR, AND, OR, ISNUMBER, COUNT, COUNTA, SUM, SUMIF, SUMIFS, COUNTIF,
COUNTIFS, INT, MOD, ROUND, ABS are supported. Reject other function calls.
Validate styles and bounds before emitting any archive. Avoid silent truncation.

Meaningful tests independently parse the archive/XML: required relationships and
parts resolve, deterministic bytes, safe literal labels, formula/cache types,
styles/widths/panes/filters/internal links, invalid sheet/cell/numeric/formula
inputs, maximum boundaries and no network or external-link parts.
