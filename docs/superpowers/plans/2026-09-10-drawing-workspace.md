# Local Drawing Workspace Implementation Plan

**Goal:** Import a drawing PDF, browse its sheets, and view its original bytes through a local interface backed by the existing shared Rust core.

**Architecture:** A Python 3.9+ standard-library server invokes the existing `heleos` CLI using argument arrays. One directory holds one Foundation project, database, vault, and non-authoritative display metadata. Local HTML, CSS, and JavaScript present real CLI output and the browser's PDF viewer. No new source registry, storage schema, PDF parser, or calculation engine is introduced.

**Tech stack:** Existing Rust 1.96.1 CLI; Python standard library; browser-native HTML/CSS/JavaScript and PDF preview. No new dependencies or network services.

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

## Task 1 - CLI-backed local service

Owner: backend worker; sole writer of `scripts/drawing-workspace.py` and `tests/drawing-workspace/test_drawing_workspace.py`.

- [ ] Write failing behavior tests with real local HTTP requests and a real small subprocess CLI fixture for transport/error cases; confirm missing module/service failure before implementation.
- [ ] Implement explicit workspace initialization/reopen, fixed CLI argument arrays, bounded upload, deterministic project-scoped state, display labels, revision verification and hash-checked preview.
- [ ] Implement localhost/token/origin routing, fixed static routes, safe errors and lifecycle. Reject foreign origins, traversal, unknown revisions, oversized uploads, quarantined imports, and corrupt PDF bytes.
- [ ] Run `python3 -m unittest discover -s tests/drawing-workspace -v`; exercise both Python 3.14 and system Python 3.9.

## Task 2 - Drawing workspace interface

Owner: frontend worker; sole writer of `apps/drawing-workspace/index.html`, `app.js`, and `styles.css`.

- [ ] Build an accessible responsive interface with clear project context, PDF import, document list, sheet navigation, dimensions/rotation, embedded original PDF, empty/loading/error states, and explicit unverified scale.
- [ ] Consume only the shared relative API; render all labels through text nodes. Never invent successful data or send data to external services. During import, disable competing import actions and show progress.
- [ ] Preserve selected document/page across state refresh when present. Selection updates iframe PDF fragment to `#page=N`; provide an open-original link if embedded PDF rendering is unavailable.
- [ ] Check JavaScript syntax and complete the controller's real-browser flow.

## Task 3 - Runnable demonstration and handoff

Owner: controller; sole writer of this plan, `CURRENT_STATUS.md`, `docs/roadmap.md`, `docs/operations/drawing-workspace.md`, and `scripts/create-drawing-workspace-demo.py`.

- [ ] Create an original synthetic two-sheet mechanical-layout PDF with explicit demonstration labeling; write only to a caller-selected new output file under runtime/demo storage, not governed PDF fixture directories.
- [ ] Run an actual Foundation CLI import through the service; assert two pages, exact dimensions, identical original bytes, duplicate import without duplicate sheets, persistence after server restart, and quarantine failure.
- [ ] Open the local browser and verify upload, document/page selection, PDF preview or functional original-file link, and visible error state. Record browser limitations truthfully.
- [ ] Record exact changed-file identities and real check results in the operator guide/task record. Commit scoped work and integrate locally after checks; no push, workflow/account action, or release acceptance.

## Current execution

2026-09-10: API discovery completed by `sheet_api_inventory` (read-only, no mutations/network/tests). Existing `inspect foundation` and `verify --revision` already supply all required sheet and evidence data. Isolated worktree created successfully. Next action: dispatch the two disjoint implementation assignments and run actual CLI acceptance after their outputs arrive.
