# Local Drawing Workspace Implementation Plan

**Goal:** Import a drawing PDF, browse its sheets, and view its original bytes through a local interface backed by the existing shared Rust core.

**Architecture:** A Python 3.9+ standard-library server invokes the existing `heleos` CLI using argument arrays. One directory holds one Foundation project, database, vault, and non-authoritative display metadata. Local HTML, CSS, and JavaScript present real CLI output. An optional explicitly configured existing Poppler executable renders verified page images for browsers without a PDF viewer; browser-native PDF mode remains available. No new source registry, storage schema, intake PDF parser, or calculation engine is introduced.

**Tech stack:** Existing Rust 1.96.1 CLI; Python standard library; browser-native HTML/CSS/JavaScript and PDF preview; optional already-installed Poppler renderer. No dependency installation or network service.

## Authority and constraints

- Owner direction on 2026-09-10: defer GitHub App decisions, retain Mac and Windows, and advance local product work. This authorizes this bounded developer interface ahead of release acceptance. It does not authorize remote publication, final native-shell selection, Foundation acceptance, or quantity production.
- Base: `1f561993f381438817064690f04069418e56ce3f`; checkout `.worktrees/drawing-workspace-2026-09-10`; branch `build/drawing-workspace-2026-09-10`.
- Preserve the existing Foundation CLI/core, source closures, dependency lock, tests, registries, native candidates, and unrelated dirty paths.
- All drawing data stays local. The server binds only `127.0.0.1` and uses a random per-run capability path, same-origin writes, a fixed asset allowlist, and no user-supplied filesystem routes.
- PDF intake is bounded at 256 MiB and invokes real CLI ingestion. Quarantine is an error, never an accepted drawing. Original preview requires project membership, successful core revision verification, and matching byte length/SHA-256 before serving.
- Display page number is stored zero-based index plus one. Dimensions are PDF points/inches, never drawing scale. Titles are upload labels only. Scale is explicitly unverified and no measurement or quantity controls are provided.
- CLI operation serialization preserves the existing single-writer boundary. Timeouts and failures are shown to the user; no background worker survives server shutdown.
- No reviewer-agent dispatches; controller runs deterministic checks and a real CLI/browser acceptance flow.

## Shared interface

Launch: `python3 scripts/drawing-workspace.py --heleos /absolute/path/to/heleos --workspace /absolute/new/or/existing/workspace --port 0`. Print one `WORKSPACE_URL=http://127.0.0.1:PORT/TOKEN/` line. No automatic browser launch.

All URLs below are relative to that token path; assets are `index.html`, `app.js`, and `styles.css` from `apps/drawing-workspace/`.

`GET api/state` returns:

```json
{"project":{"id":"uuid","name":"Drawing workspace"},"documents":[{"revision_id":"sha256","name":"drawing.pdf","page_count":2,"sheets":[{"sheet_id":"sha256","index":0,"width_micropoints":612000000,"height_micropoints":792000000,"rotation_degrees":0}]}],"scale_status":"unverified"}
```

`POST api/import?name=filename.pdf` consumes raw `application/pdf` bytes and returns refreshed state as above. `GET api/pdf/REVISION_SHA256` returns verified project-member PDF bytes with inline content disposition. JSON failures have `{ "error": { "code": "stable_code", "message": "safe human explanation" } }` and non-2xx status.

Actual-browser correction: optional `--pdftoppm /absolute/existing/executable` selects view-only local page rendering. State includes `preview_mode: "image"` when configured, otherwise `"pdf"`. `GET api/page/REVISION_SHA256/INDEX.png` uses the zero-based existing sheet index, verifies project membership and original bytes before invoking the explicit renderer, bounds the rendered longest axis to 2,000 pixels, runtime to 30 seconds and output to 20 MiB. Render failures remain failures; page images do not become evidence or quantity authority. The frontend provides fit and zoom controls and retains the original-PDF link.

## Task 1 - CLI-backed local service

Owner: backend worker; sole writer of `scripts/drawing-workspace.py` and `tests/drawing-workspace/test_drawing_workspace.py`.

- [x] Write failing behavior tests with real local HTTP requests and a real small subprocess CLI fixture for transport/error cases; confirm missing module/service failure before implementation.
- [x] Implement explicit workspace initialization/reopen, fixed CLI argument arrays, bounded upload, deterministic project-scoped state, display labels, revision verification and hash-checked preview.
- [x] Implement localhost/token/origin routing, fixed static routes, safe errors and lifecycle. Reject foreign origins, traversal, unknown revisions, oversized uploads, quarantined imports, and corrupt PDF bytes.
- [x] Run `python3 -m unittest discover -s tests/drawing-workspace -v`; exercise both Python 3.14 and system Python 3.9.

## Task 2 - Drawing workspace interface

Owner: frontend worker; sole writer of `apps/drawing-workspace/index.html`, `app.js`, and `styles.css`.

- [x] Build an accessible responsive interface with clear project context, PDF import, document list, sheet navigation, dimensions/rotation, embedded original PDF, empty/loading/error states, and explicit unverified scale.
- [x] Consume only the shared relative API; render all labels through text nodes. Never invent successful data or send data to external services. During import, disable competing import actions and show progress.
- [x] Preserve selected document/page across state refresh when present. Selection updates iframe PDF fragment to `#page=N`; provide an open-original link if embedded PDF rendering is unavailable.
- [x] Check JavaScript syntax and complete the controller's real-browser flow.

## Task 3 - Runnable demonstration and handoff

Owner: controller; sole writer of this plan, `CURRENT_STATUS.md`, `docs/roadmap.md`, `docs/operations/drawing-workspace.md`, `scripts/create-drawing-workspace-demo.py`, and `tests/drawing-workspace/acceptance_cli.py` (the independent real-CLI acceptance runner).

- [x] Create an original synthetic two-sheet mechanical-layout PDF with explicit demonstration labeling; write only to a caller-selected new output file under runtime/demo storage, not governed PDF fixture directories.
- [x] Run an actual Foundation CLI import through the service; assert two pages, exact dimensions, identical original bytes, duplicate import without duplicate sheets, persistence after server restart, and quarantine failure.
- [x] Open the local browser and verify upload, document/page selection, PDF preview or functional original-file link, and visible error state. Record browser limitations truthfully.
- [x] Record exact changed-file identities and real check results in the operator guide/task record. Commit scoped work and integrate locally after checks; no push, workflow/account action, or release acceptance.

## Current execution

2026-09-10: API discovery completed by `sheet_api_inventory` (read-only, no mutations/network/tests). Existing `inspect foundation` and `verify --revision` already supply all required sheet and evidence data. Isolated worktree created successfully. Next action: dispatch the two disjoint implementation assignments and run actual CLI acceptance after their outputs arrive.

2026-09-10 execution: backend and frontend workers dispatched against `edde43e223223d0b1a3bb362b4a03fce1915cb06`; root owns the demo and independent live acceptance. Existing native macOS `heleos 0.1.0` binary SHA-256 `4438f7293169e60424d9d8c8f96413f97dba2b7c2b098941269809a823b08dd5` is the explicit service dependency for this run; no rebuilt/current-HEAD binary claim is made. A relative-vault direct invocation returned exit 22 both inside and outside the runtime sandbox. Source tracing to `vault/path.rs:93` established the absolute-path requirement; the absolute-path inspection passed, then direct real-core intake passed with two expected sheets (including 90-degree rotation) for synthetic PDF SHA-256 `315307c0c84e892a8905c7aeefc4159e4a113bac4ffc97d744a7173132aa8b57`. No core change was needed. Direct ingest process 29975 is terminal exit 0.

The independent service acceptance runner first failed at startup before the backend existed (exit 1, no workspace URL), preserving a causal missing-feature baseline. Its contract covers real import/geometry, original-byte preview, duplicate intake, unknown revision, real quarantine, foreign-origin rejection, restart, and corrupt-vault rejection. The owner has existing local drawings; their path was requested asynchronously while synthetic integration checks continue. No private drawing has been read or uploaded.

2026-09-10 controller validation: 9/9 focused service tests pass under Python 3.14.6 and 3.9.6. The independent real-CLI HTTP acceptance passes all nine behaviors (report in ignored `.heleos/drawing-workspace-demo/acceptance-macos.json`); its first post-implementation invocation needed localhost listener permission, then the harness was corrected to send the browser's mandatory same-origin header. All test servers are terminal. The interactive server is separately retained for browser verification.

The owner supplied the local drawings directory. Root located it and inspected file names locally; no private document content was sent to external workers/providers or placed in tracked evidence. Two real drawing sets were quarantined. Poppler can open both; a temporary diagnostic-only copy of the existing guest narrowed the rejections to missing indirect references and a free-xref-pointer bound. These are compatibility findings, not permission to skip checks or assert that the source files are valid. Two other real drawing sets passed the unchanged native guest diagnostic. A 21-page real set then passed actual WASM/CLI intake through the browser, with working page navigation and metadata. Existing frozen guest/core bytes remain unchanged. Remaining parser compatibility work requires independent synthetic reproductions and scoped corrections, not weakening or bypassing the frozen gates.

The first actual browser run exposed missing PDF-plugin rendering in the Codex in-app browser. Chrome automation reported `ERR_BLOCKED_BY_CLIENT`; no browser protections were changed. The local Poppler 26.05.0 executable already supplied by the runtime rendered the accepted input without new installation. Backend/frontend workers received a bounded image-preview follow-up; no quantity, source-admission, or Foundation release authority changes.


## Terminal implementation and controller handoff - 2026-09-10

Backend implementation is finished; its renderer follow-up was reassigned to root after the worker stopped without writing it. The worker later completed six additional renderer boundary tests only and is terminal with no processes. Frontend implementation and image-viewer follow-up are finished. The discovery worker is terminal. No external reviewer or provider submission occurred.

Controller verification on final executable bytes:

- `python3 -m unittest discover -s tests/drawing-workspace -v`: exit 0, 15/15 under Python 3.14.6.
- `/usr/bin/python3 -m unittest discover -s tests/drawing-workspace -v`: exit 0, 15/15 under Python 3.9.6.
- `FONTCONFIG_FILE="$PWD/apps/drawing-workspace/fonts-macos.conf" XDG_CACHE_HOME=/Users/bekim/Heleos-spark/.heleos/drawing-renderer-cache python3 tests/drawing-workspace/acceptance_cli.py --heleos /Users/bekim/Heleos-spark/target/debug/heleos --pdftoppm /Users/bekim/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/poppler/bin/pdftoppm --output .heleos/drawing-workspace-demo/acceptance-renderer-macos.json`: exit 0, all ten real-core checks pass. Report remains ignored runtime evidence. Both page images have the expected bounded dimensions including rotated page 2.
- `node --check apps/drawing-workspace/app.js`: exit 0. `git diff --check`: exit 0. Python sources and embedded fixtures parse under Python 3.9.
- Actual browser: 21-page real import, visible page 1 and page 2 images, next-page selection, zoom to 125% of fit, fit restoration, refresh retaining page 2, and no browser console errors. The earlier quarantined imports showed visible errors and stayed outside the document list. Original bytes were rechecked unchanged. Private filenames and content are excluded from tracked records.

The renderer acceptance first failed at unsupported renderer startup, then exposed the bundled Mac font configuration's missing build path and unusable cache. Direct comparison rendered the real drawing in 1.67 seconds while substitute-font synthetic rendering timed out. An explicit small Mac font configuration with local cache resolved this: direct synthetic render passed in 3.02 seconds and the complete real-core acceptance then passed. Renderer stderr is discarded to avoid accumulating repetitive diagnostics in memory; safe errors still reach the user. No timeout was extended and no intake check was relaxed.

The original interactive server and all diagnostic/test processes are terminal. The saved workspace was moved intact into visible-main `.heleos/drawing-workspace`; only this workspace copy moved, never source drawings. The product server is intentionally retained for the owner, with the current session URL supplied separately at handoff. Local source integration follows these checks; no push or release is authorized by this work.

First finish line achieved: local PDF import, sheet browsing, visible verified-page preview, fit/zoom and persistence. Next action: reproduce the two identified intake compatibility cases using new synthetic fixtures and scope corrections without changing the frozen Foundation source closures. Later product milestones: scale calibration, first deterministic takeoff interaction, and native Mac/Windows shell delivery. Native Windows and Foundation release acceptance remain open.

Changed executable, test, and guide file identities (SHA-256):

| Path | SHA-256 |
| --- | --- |
| `scripts/drawing-workspace.py` | `25809a4de6c5f1f848059db697a95698085e2c0eee22e5fe827574cba14ff081` |
| `scripts/create-drawing-workspace-demo.py` | `953d950a80f506e05af4fac337a769ec5cd9034c4c4b5807207c244df323de35` |
| `tests/drawing-workspace/test_drawing_workspace.py` | `c4dbb3ff4a399743042253ebbf4095cc12439fdf4a8c3c94b777483e6b49158a` |
| `tests/drawing-workspace/acceptance_cli.py` | `9d9a6ccd594849d204ba4341d474c975be58d0135a2215e75b5ae7a05fdd936a` |
| `apps/drawing-workspace/index.html` | `860628295f37b30ce49839fc86e75f71ae850776a45fbdded773a8a351a7be17` |
| `apps/drawing-workspace/app.js` | `f41f1d3a602b0f79226f56dfd56119d754624c51eb4135d3255f15e9614ac95b` |
| `apps/drawing-workspace/styles.css` | `a7a2d379942c572648125a4edaf3ab323e2744d34706cf3a621f06e168a98bc8` |
| `apps/drawing-workspace/fonts-macos.conf` | `4e4121b314db885a0c49dde1a293cb6137f12626237cbd067b4c819daf5bf264` |
| `docs/operations/drawing-workspace.md` | `db39f88795493c531bde1869a0613f3f7cb85e81a9c85d3a5e4a3a5f0eec4990` |
