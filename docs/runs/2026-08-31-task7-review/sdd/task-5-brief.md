# Task 5 implementation brief — deterministic PDF probing in WASI

Initial controller contract: `b6f54b55a37a928f1d29f8e264ec748099d40ef5`

Reviewed xref/API controller amendment: `e8255a8596aa05f808a0a028ac83f822a96d0982`

Reviewed WASIp1 ABI/reproducible-artifact amendment: `7745eeb8a66e203ff8b8bbb17cf002dcca52e493`

Reviewed guest-only Cargo graph/source-closure amendment and current base: `e1667cb9d6b59385195c8a51d8b4edbe1e01044c`

Authoritative plan: `docs/superpowers/plans/2026-08-28-heleos-spark-foundation-0.1.md`

Approved current plan blob: `dc7a5b263308e8f5c13cb06ffede943130acb788`

Commit subject: `feat: sandbox deterministic PDF probing`

## Operating rules

1. Read the complete authoritative plan before editing, including Global Constraints, Task 5, Task 6's provenance-consumption clause, and Task 7's independent artifact-recomputation clause. This brief routes work; the committed plan is authoritative.
2. Start from the exact clean controller base above. Do not amend, reset, rebase, or edit the plan.
3. Use genuine TDD. Resolve/govern dependencies and create compilable empty roots first; then write the frozen public/protocol/geometry/resource/sandbox tests and capture exact RED commands/output before production behavior. Do not claim compiler errors caused by malformed test scaffolding as the only RED for behavioral contracts.
4. One implementation writer owns all production edits. Reviewers remain read-only. Preserve all existing user/controller work.
5. Modify only the twenty tracked paths below. The ignored `task-5-report.md` is the sole scratch/report exception and must never be staged or committed. Do not edit this brief, `progress.md`, other `.superpowers` files, or generated files outside the declared scope.
6. Use `apply_patch` for edits. Do not track a `.wasm` file. Do not weaken a limit, policy, outcome, fixture, test, or gate to reach green.
7. Stop and report before deviating if a pinned API makes a frozen contract impossible. Never classify by an error display string, add ambient authority, or invent an unreviewed dependency/workaround.

## Exact tracked scope

1. Create `.gitattributes`
2. Modify `Cargo.toml`
3. Modify `Cargo.lock`
4. Modify `crates/heleos-core/Cargo.toml`
5. Modify `crates/heleos-core/src/lib.rs`
6. Create `crates/heleos-core/src/pdf/mod.rs`
7. Create `crates/heleos-core/src/pdf/geometry.rs`
8. Create `crates/heleos-core/src/pdf/wasi_host.rs`
9. Create `crates/heleos-pdf-protocol/Cargo.toml`
10. Create `crates/heleos-pdf-protocol/src/lib.rs`
11. Create `crates/heleos-pdf-guest/Cargo.toml`
12. Create `crates/heleos-pdf-guest/src/lib.rs`
13. Create `crates/heleos-pdf-guest/src/main.rs`
14. Create `crates/heleos-test-fixtures/Cargo.toml`
15. Create `crates/heleos-test-fixtures/src/lib.rs`
16. Create `crates/heleos-core/tests/pdf_probe.rs`
17. Create `tests/fixtures/pdf/README.md`
18. Create `artifacts/pdf-guest/manifest.toml`
19. Modify `governance/fixtures.toml`
20. Modify `governance/tools.toml`

Before committing, `git diff --cached --name-only` must equal this set exactly.

## Frozen public and persistence boundary

- Implement exactly `PdfProbe::probe(&self, VerifiedObject, RevisionId, PdfLimits) -> Result<PdfProbeOutcome>`, `PdfSandboxConfig { staging_parent }`, `ApprovedPdfGuest::load_tracked(&[u8])`, and `WasiPdfProbe::new` from the plan.
- Outcomes are adjacent-tagged `Accepted(PdfInspection)` or `Quarantined(PdfQuarantine)`. Both carry host-attached `PdfProbeProvenance`; quarantine carries no revision and creates no revision/sheet/evidence.
- Provenance is literal lopdf `0.44.0`, protocol `heleos.pdf-probe/v1`, and the verified WASM/source/dependency digests. The guest cannot supply it. Task 6 must consume this returned value without reconstruction.
- Implement the exact reason, active-feature, and limit enums/ordinals and the sole reason-to-existing-`IngestOutcome` mapping. Artifact/setup/digest/copy/capacity/permission/sync/cleanup failures are hard errors, not document quarantine.
- Validate every `PdfLimits` field as nonzero and componentwise no larger than defaults. `N` accepts and `N + 1` quarantines.
- `PageUnit::Point` serializes as `pt`. Freeze the exact metadata/transform fields and public JCS bytes.

## Protocol and geometry

- `heleos-pdf-protocol` is PDF-parser-free. Default features contain strict wire/artifact DTOs and pure JCS/hash helpers; non-default `artifact-host` adds pinned wasmparser/TOML support plus the pure shared guest-resolution spec, source-closure and lock-projection validators, Cargo metadata/lock normalization, and complete build policy. Guest builds must exclude that feature, TOML, and wasmparser.
- Implement the exact v1 request/response/reason/page documents, adjacent tags, enum spelling, lowercase 64-hex digests/IDs, JCS-safe integer interval, one canonical JCS document + LF + EOF, 16 KiB request cap, bounded response, deny-unknown/duplicate/missing/trailing/CRLF/noncanonical policy, and semantic echo/index/page-ID revalidation.
- Traverse the page tree yourself in `Kids` order. Enforce the exact inheritance, cycle/depth/count rules, effective CropBox/MediaBox, `UserUnit`, rotations, f32-to-micropoint ties-even conversion, JCS-safe endpoints/translations, axis cap, and four matrices from the plan. Never use `get_pages()`.

## Guest parser boundary

- Pin lopdf `=0.44.0`, no defaults, only in guest and fixtures. Core's normal tree must contain no lopdf or guest package.
- Read canonical stdin to EOF, then only `/input/input.pdf`. Check `%PDF-` in the first bounded 1,024 bytes. Use strict bounded `LoadOptions`.
- Implement the request-local single-threaded object-stream filter preflight because lopdf swallows errors: clone only the bounded `/ObjStm`, run `ObjectStream::new_with_limit`, strictly validate `/N`/`/First` and ordered header pairs, retain the lowest container independently for metadata-limit and corrupt classes, return `None` on failure and unchanged content on success.
- After unencrypted load, require the frozen non-free-xref-to-`Document.objects` bijection, including normal generations, compressed container/index/preflight order, uniqueness, free entries, converse extras, and expected object count. Limit preflight wins corruption; corruption wins object-count limit.
- Scan every loaded object and trailer deterministically, including arrays/dictionaries/stream dictionaries/decoded strings/names/keys/references, while excluding raw generic/content/image stream bytes. Do not decode content/image streams, extract/render/execute/follow/fetch, consult time/environment/RNG, or write.
- Preserve the exact encrypted/metadata/corrupt and later finding precedence. Xref work exhaustion wins when the authoritative encryption trailer lies beyond the unexamined boundary; do not add a second traversal. Runtime limits win when no complete document verdict exists.

## Dependencies, fixtures, and artifact trust

- Use the exact pins/features in Task 5: Wasmtime/Wasmtime-WASI 48.0.1, Tokio 1.51.1, TOML 1.1.4+spec-1.1.0, wasmparser 0.254.0 optional behind `artifact-host`, dev-only wat 1.254.0, and no direct RNG crate. Resolve, fetch, lock, govern, and check the complete transitive graph before behavior.
- Produce every deterministic fixture named in the plan, including empty-password encryption, every active ordinal, object/xref bombs, page-tree corruption, all geometry boundaries/rotations, prompt text, and the 10,000-page success. Freeze bytes/SHA-256 and complete fixture governance. No copied project content, clock, locale, filesystem order, or unfixed RNG.
- The tracked manifest is embedded and has the exact schema/top-level fields/literals/record DTOs/sort rules. Obtain the normalized normal+build (not dev-only) dependency closure only from the shared three-member guest-resolution spec and its locked metadata joined to exact production/pruned Cargo.lock v4 bytes. Full-workspace metadata is supplemental containment evidence and never supplies records.
- In all three governed crate manifests disable build scripts and every Cargo auto-discovery switch and declare only the exact explicit targets. Before every Cargo invocation, no-follow enumerate the shared exact seven-file crate inventory and run the shared include/path source-closure policy. Hash the three sorted JCS arrays with their exact domain tags and the six source-member files with the source-tree framing; fixture `src/lib.rs` is resolution-only and must be unreachable from the non-dev release graph. Keep exactly six leading-slash `.gitattributes` `text eol=lf` rules and reject CR bytes.
- Build twice from distinct controlled workspace/cache/target plus guest-resolution roots with Rust 1.96.1 for `wasm32-wasip1 --release`; bytes must match. Verify manifest/WASM/source/dependency/import/export values, no start section, exact signatures, exact `_start`/`__main_void`, 32 MiB artifact cap, physical-prefix absence, and governed import subset before `Module::from_binary`. Never accept a path/manifest/digest override or track the WASM.

## Host filesystem and capability boundary

- Consume and rewind `VerifiedObject`; require revision/digest, declared length, EOF, and rehash agreement. Tightened input-limit quarantine hashes without staging.
- Acquire the stable private staging parent no-follow, retain it through cleanup (Windows: no delete sharing), and bracket the sole admitted path-based capacity query. Enforce copy bytes + allocation unit + max(20 GiB, 10%).
- Create one private `TempDir` with exactly read-only `input.pdf`; preallocate nonzero, hash/copy/sync/rehash, apply exact Unix/Windows permissions through Task 3 handle helpers, and retain checked handles.
- Bracket Wasmtime's ambient `preopened_dir` reopen before/after and before accepting output across parent/root/input identity, type, permissions, contents, length, and digest. Preopen only `/input` read-only.
- Provide finite canonical stdin, bounded stdout, and a Heleos recording closed zero-cap stderr. Any stderr method attempt invalidates all output even if the guest handles `Closed`. No args/env/inherited stdio/network/other preopen.

## Deterministic WASI and runtime

- The sole guest-visible nondeterminism API is manifest-bound `random_get`, max 32 bytes, needed by Rust 1.96.1 HashMap. Override builder secure/insecure RNGs and seed before `build_p1` with the three exact domain-separated digest values and separate `wasmtime_wasi::random::Deterministic` instances.
- Pin the real byte oracle: for secure digest `d[0..32]`, the 16-byte HashMap request yields `d[3],d[7],d[11],d[15],d[19],d[23],d[27],d[31]` twice. Test it across fresh stores/targets. Default builder entropy must never remain guest-visible.
- Configure one shared Engine, 10 ms cancellable/joined epoch ticker, relative per-store deadlines, fuel, disabled threads/multi-memory/memory64/component model, and exact static imports. The P1 linker may define more names, but the verified module can import only its manifest set.
- `WasiPdfProbe` owns a dedicated OS runtime-owner thread. Only it creates/drops `Builder::new_multi_thread().worker_threads(2).enable_time().build()`. Public sync callers never enter/block_on/drop a Tokio runtime. Use bounded std dispatch/result channels and an RAII mutex/condvar admission count of two before staging. Calls from current- or multi-thread Tokio contexts must be safe though blocking by API contract.
- Wrap instantiation/start/output collection in Tokio timeout. On drop, stop admission/dispatch, finish or cancel two bounded jobs, drop runtime on owner thread, join it, then cancel/join ticker. Prove no leaks and independent staggered deadlines.
- Before instantiation, use `Module::resources_required()` plus exact import/fresh-store invariants for typed initial Memories/Tables/GuestMemory/TableElements; use a host attempt counter for Instances. Attach a custom typed `RecordingLimiter` for dynamic memory/table denial and keep Wasmtime counts as defense. Never parse opaque count-limit strings; an impossible post-proof count failure is hard Integrity.
- Apply exact compound-signal priority from the plan, including all combination tests. Only normal return or typed `I32Exit(0)` with no higher signal reaches protocol validation.
- Cleanup every path in exact order: discard invalid output; drop instance/store/WASI/pipes/input/root; retain parent; explicitly close TempDir; recheck parent; drop parent. Cleanup uncertainty overrides any outcome.

## Required evidence and finish

Capture the exact RED/GREEN commands and results in ignored `task-5-report.md`, including dependency/governance provenance, fixture digests, source/dependency/import/export golden bytes/hashes, two clean guest-build hashes, tracked manifest hash, staged path set, host gates, and both Windows compile attempts.

Run the exact Task 5 commands from the plan, including:

- locked guest release build
- `cargo +1.96.1 fmt --all --check`
- strict locked workspace clippy, all targets/features
- core lib + `pdf_probe` with `HELEOS_PDF_GUEST`
- locked workspace tests/check
- locked/offline metadata and tree assertions
- normal MSVC all-target check
- if bundled SQLite stops before Heleos Rust, the exact Task 4-derived locked/offline `modern_sqlite` all-target MSVC probe with final Task 5 graph/tests and an audited bundled-SQLite-only delta

The non-native probe is compile evidence only. Commit the full cfg(windows) source required by the plan; Task 10 owns native NTFS execution.

Stage exactly the twenty tracked paths, inspect cached diff/name-only/check, ensure no `.wasm` or ignored file is staged, rerun staged-source gates, and commit exactly:

`git commit -m "feat: sandbox deterministic PDF probing"`

Report the commit SHA, exact path count, RED/GREEN totals, reproducibility hashes, normal MSVC result, Rust-only probe result/path, remaining environment limitations, ignored-report path, and clean tracked status.
