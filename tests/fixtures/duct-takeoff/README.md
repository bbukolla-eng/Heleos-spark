# Original duct fixture preparation

`build_source_pdf.py` produces three deterministic US Letter drawing pages using
only the Python standard library. `source-manifest.json` pins the exact PDF hash,
page coordinates, size/elevation/dimension regions, separate case scopes and
literal proposed answers. It does not feed the builder. The original nine
mathematical/semantic examples remain unchanged.

Generate into a new path, then verify the pinned canonical output:

```sh
python3 tests/fixtures/duct-takeoff/build_source_pdf.py output/pdf/original-duct-source-fixtures.pdf
python3 tests/drawing-workspace/verify_duct_source_fixtures.py \
  --workspace .heleos/duct-source-verification-attempt-1 \
  --heleos /absolute/path/to/heleos \
  --pdftotext /absolute/path/to/pdftotext \
  --pdftoppm /absolute/path/to/pdftoppm
```

The builder refuses to overwrite a file. The verifier requires a new workspace
for every attempt. Preserve prior artifacts when correcting a fixture. Generated
PDFs belong in `output/pdf`, not the frozen Foundation `tests/fixtures/pdf` set.
The native verification retains page renders, positioned text and exact tool and
source identities. Review all rendered pages as well as the numerical checks.
If a relocated Poppler installation cannot find its font configuration, set
`FONTCONFIG_FILE` to that installation's `etc/fonts/fonts.conf` for the verification
process. Its path and hash are recorded in the receipt. Keep the product preview
timeout unchanged; this is an explicit runtime dependency configuration.

| Source page | Cases | Prepared inputs |
| --- | --- | --- |
| D-101 | E01, E02, E03 | Two sizes, separate supported rise, separate unknown rise |
| D-102 | E05 | Two unconnected crossing ducts, explicit elevations and wall context |
| D-103 | E04 | One not-to-scale slope, projection, rise and common datum |

Each case is independent. Repeated geometry on D-101 represents separate runs;
it is not duplicate depiction of one run. E06-E09 remain literal examples only.
E01 uses a straight size change with the same arithmetic as the pending packet's
L-shaped example; this pack does not establish polyline or elbow coverage.
The wall reference centerline is an answer-key construction; only the two wall
outlines appear in the source PDF. The slope cannot be scaled from page pixels.

Only the PDF or its rendered pages may be future inference inputs. Exclude this
answer manifest, expected values, builder internals and verification receipts
from prompts or model inputs. Normalized paths and semantic relationships in
the manifest are author-supplied reference observations, not extraction results.
Plain drawing annotations deliberately make the cases unambiguous; these do not
represent realistic project diversity or measure recognition accuracy.

Owner adjudication and production rule admission remain pending. Passing this
check verifies source preparation and existing native geometry/text/render
integration; it does not verify automatic duct extraction, the new quantity
engine, representative-project accuracy, Windows or full takeoff acceptance.
