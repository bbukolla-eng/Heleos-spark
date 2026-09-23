# Local drawing workspace

The developer workspace makes the existing Foundation PDF intake and evidence store usable through a local browser. Select a PDF, import it into a local project, browse its sheets, and view the drawing. The server invokes the shared Rust CLI for intake, inspection, and revision verification. It uses no model, cloud upload, or external assets. An optional existing Poppler renderer supplies page images in browsers without a built-in PDF viewer.

## Run on Mac or Windows

Use Python 3.9 or later and an existing native `heleos` executable. From the repository root:

```sh
python3 scripts/drawing-workspace.py --heleos target/debug/heleos --workspace .heleos/drawing-workspace --port 0
```

On Windows, use the native executable path:

```powershell
py -3 scripts/drawing-workspace.py --heleos target/debug/heleos.exe --workspace .heleos/drawing-workspace --port 0
```

Open the `WORKSPACE_URL` printed by the command. The listener is local to `127.0.0.1`; its random URL is valid for this running session. Stop with Ctrl+C. Relaunch with the same workspace directory to reopen the saved project; each launch prints a fresh URL. Supply a new empty directory for a different project.

The existing executable must be a native build for the machine running the service. This slice adds a portable local developer interface, not a final native desktop shell. Native Windows execution and product parity still require actual Windows evidence.

For an integrated image preview with fit/zoom controls, add `--pdftoppm /absolute/path/to/pdftoppm` (or `pdftoppm.exe` on Windows). The renderer must already be installed; the server does not download or install it. The Codex in-app browser needs this mode because its PDF plugin did not render during acceptance. The Mac check used the runtime's existing Poppler 26.05.0 executable. Its bundled default font configuration points at an unavailable build location and can time out on PDFs needing substitute fonts. The checked command explicitly uses the repository's Mac font configuration and a writable local cache:

```sh
FONTCONFIG_FILE="$PWD/apps/drawing-workspace/fonts-macos.conf" \
XDG_CACHE_HOME="$PWD/.heleos/drawing-renderer-cache" \
python3 scripts/drawing-workspace.py --heleos target/debug/heleos --workspace .heleos/drawing-workspace --pdftoppm /Users/bekim/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/bin/pdftoppm --port 0
```

Page rendering takes only verified original bytes, requires an existing page in the selected revision, and bounds image size and rendering time. The rendered page is a view, not a new source of quantity or scale authority.

## Use your drawings

Choose **Import PDF**, select one local PDF, and wait for the core to finish intake. Select a document, then a page. The sheet list shows the PDF's page dimensions and rotation. File names are display labels; the stored content hash identifies the revision. Selecting the same PDF again preserves its document and sheet identity.

The original file is left in place. A copy is admitted into the workspace's content-addressed vault. Preview serves only project-member revisions after core verification and a byte-length/hash check. Encrypted, corrupt, suspicious, or unsupported inputs rejected by the core produce an error and do not appear as accepted drawings.

Paper size is not drawing scale. The interface keeps scale **unverified** and does not calculate quantities, infer equipment, recognize sheet titles, or present estimates. Sheet numbers are page positions, not extracted drawing sheet labels. Image mode provides fit/zoom controls; PDF mode uses the browser's built-in viewer. **Open original** remains available in either mode.

The workspace directory contains the database, vault, process lock, and display metadata. Upload and rendering scratch are temporary and removed after each operation. Keep that directory together when reopening it. Use the Foundation's backup/restore tools for governed evidence recovery.

## Synthetic demonstration and checks

Create a new original two-sheet mechanical-layout demonstration PDF:

```sh
python3 scripts/create-drawing-workspace-demo.py /absolute/new/mechanical-demo.pdf
```

The destination must not already exist. The drawing is explicitly synthetic and not for construction; it contains no private project material and no accepted scale or quantities.

Run the service/transport tests and the separate acceptance flow against the real CLI:

```sh
python3 -m unittest discover -s tests/drawing-workspace -v
python3 tests/drawing-workspace/acceptance_cli.py --heleos /absolute/path/to/heleos
python3 tests/drawing-workspace/acceptance_cli.py --heleos /absolute/path/to/heleos --pdftoppm /absolute/path/to/pdftoppm
```

The acceptance command uses disposable synthetic data and checks real import, two-sheet geometry and rotation, original PDF bytes, duplicate identity, quarantine, project membership, foreign-origin denial, persistence, and corruption rejection. It does not establish native Windows evidence, Foundation release acceptance, or final desktop delivery.

## Observed intake compatibility limits

Real drawing checks found inputs that common PDF viewers open but the current strict Foundation guest rejects. Two cases were narrowed to a missing indirect reference and a free-xref-pointer bound. These are recorded compatibility cases requiring small synthetic reproductions and an explicit intake-policy/code correction. The interface does not repair/rewrite a source PDF, skip the guest, or promote a quarantined input. Other real drawing sets, including a 21-page set, passed the unchanged core. General PDF compatibility has not been established.

## Verified local checkpoint - 2026-09-10

The 15 service tests passed under Python 3.14.6 and 3.9.6. The separate real-CLI acceptance passed all ten checks with local Poppler page rendering, including exact page dimensions and rotation. The actual browser imported a real 21-page drawing set, displayed pages 1 and 2, zoomed to 125% of fit, restored fit, and preserved page 2 through refresh with no browser errors. Runtime data is retained in `.heleos/drawing-workspace` in visible main; original source PDFs remain unchanged. This is a usable local drawing workspace; scale calibration, takeoff, final native shells, and native Windows verification remain unfinished.
