# Task 5 evidence report — deterministic PDF probing

## Identity and scope

- Controller base: `b6f54b55a37a928f1d29f8e264ec748099d40ef5`
- Controller-amended base: `e8255a8596aa05f808a0a028ac83f822a96d0982`
- Artifact-policy-amended base: `7745eeb8a66e203ff8b8bbb17cf002dcca52e493`
- Guest-resolution/source-closure-amended base: `e1667cb9d6b59385195c8a51d8b4edbe1e01044c`
- Warning-free TOML-requirement-amended base: `cd594d3`
- Windows compile-probe-amended base: `986f933`
- Isolated artifact-cache-split-amended base: `9bdc42b74fb064393cfae975e6cdbdac097be834`
- Pruned guest-build-evidence-amended base: `997b614e9b4411de219e51478b96114d7ea499ec`
- Feature-aware classifier-amended/current controller base: `30482d084510ac35d58e37402ccf8d4ecaca7f39`
- Approved plan blob: `4beb99edf03f92b34485f9070f7bc4ba8c93b895`
- Amended authoritative plan blob: `600440fd84ac77f8195273e4380445e990f5cb79`
- Artifact-policy authoritative plan blob: `ac062f126f7922e641ed6f156f886e09fca0d797`
- Guest-resolution/source-closure authoritative plan blob: `dc7a5b263308e8f5c13cb06ffede943130acb788`
- Warning-free TOML-requirement authoritative plan blob: `23c5d6153d3400ae41774744446f46c5e5125025`
- Windows compile-probe authoritative plan blob: `0c349eca9c9d3ed25cc73fa0e50001c2389628c7`
- Isolated artifact-cache-split authoritative plan blob: `c77b85d3700adadd00dd95f476d434e574ad76b8`
- Pruned guest-build-evidence authoritative plan blob: `e3e1489cf6e677be51cbae5be4aa734749c19b4f`
- Current feature-aware authoritative plan blob: `797cc9f67db948a3d4ec1268bab867ebaf9b3b18`
- Worktree: `/Users/bekim/Heleos-spark/.worktrees/foundation-0.1`
- This report is ignored and must never be staged.

## Dependency and governance checkpoint

- `cargo +1.96.1 generate-lockfile` resolved the frozen Task 5 graph.
- `cargo +1.96.1 update -p rand@0.10.2 --precise 0.10.1` deliberately restored the contract-pinned transitive RNG version without adding a direct RNG dependency.
- `cargo +1.96.1 fetch --locked` completed.
- `cargo +1.96.1 metadata --locked --offline --format-version 1` exited `0`.
- `cargo +1.96.1 check --locked --offline --workspace --all-targets --all-features` exited `0`.
- `cargo +1.96.1 tree --locked -p heleos-core --edges normal` contains neither `lopdf` nor `heleos-pdf-guest`.
- `cargo +1.96.1 tree --locked -p heleos-pdf-guest` contains neither `wasmparser` nor a Cargo-process helper.
- Both inverse lopdf trees resolve exactly `lopdf 0.44.0` beneath only the guest and fixture packages.
- `governance/tools.toml` parses as TOML and records all 156 newly resolved registry package identities, checksums, licenses, declared MSRVs, build-script flags, and active features.

Frozen direct pins/features:

- lopdf `0.44.0`, defaults off, guest and fixtures only.
- Wasmtime `48.0.1`, defaults off, `async`, `call-hook`, `cranelift`, `runtime`, `std`.
- Wasmtime-WASI `48.0.1`, defaults off, `p1`.
- Tokio `1.51.1`, `rt-multi-thread`, `sync`, `time`.
- TOML Cargo requirement `=1.1.4`, resolving the locked package identity `1.1.4+spec-1.1.0`, defaults off, `parse`, `serde`, `std`.
- wasmparser `0.254.0`, defaults off, optional protocol `artifact-host` only.
- wat `1.254.0`, defaults off, core dev-only.
- rand `0.10.1` is transitive only.

## RED evidence

### RED-0 — public/protocol/fixture surface absent

Command:

`cargo +1.96.1 test --locked -p heleos-core --test pdf_probe`

Result: exit `101`. Rust compiled the dependency graph and then reported expected unresolved Task 5 imports for every frozen public PDF DTO/trait, protocol DTO/framing function, and deterministic fixture factory. This is the signature/scaffold RED, not the later behavioral RED.

### RED-1 — frozen surfaces compile and behavior is absent

Command:

`cargo +1.96.1 test --locked -p heleos-core --test pdf_probe`

Result: exit `101`; `1 passed, 5 failed, 0 ignored`. The compiled tests failed on canonical request framing, strict noncanonical/duplicate/unknown/CRLF/trailing rejection, bounded response framing, deterministic PDF fixture bytes, and the public JCS golden. The JCS failure isolated a hand-derived page-ID literal typo in the test; it was corrected to the independently frozen Task 2 page-ID behavior before any Task 5 behavior implementation.

## Pinned-API stops raised to controller

1. lopdf 0.44.0 discards free xref entries/tombstones in its loaded `Document.reference_table`, so the strict frozen free/range/incremental audit requires an independent bounded raw-xref-chain prepass or a contract/dependency change.
2. Wasmtime-WASI's recording `OutputStream` trait names `bytes::Bytes`, but `wasmtime-wasi` does not reexport `Bytes`; an exact custom stderr/overflow recorder requires an explicit direct `bytes` dependency (already transitive at 1.12.1) or a contract/upstream change.

No parser/host behavior was implemented while those decisions were pending.

## Controller rulings

- The controller approved a guest-local, safe, bounded raw-xref canonical validator over the already input-bounded PDF bytes. It must parse the authoritative `startxref` chain and same-revision hybrid xref, retain free tombstones newest-first, reject malformed/conflicting/cyclic revisions and unchecked ranges, and audit the resulting active map against lopdf's loaded objects and object-stream preflight records. This is an implementation of the frozen bijection semantics, not a dependency or plan amendment.
- The controller approved direct `bytes = "=1.12.1"` in the workspace and core only, because the public Wasmtime-WASI `OutputStream` trait requires `bytes::Bytes` and the crate does not reexport it. The package identity/checksum was already present transitively; its direct API/capability authority is now governed.

## Protocol/artifact TDD evidence

### RED-2 — artifact records and hashes absent

Command:

`cargo +1.96.1 test --locked -p heleos-pdf-protocol --all-features`

Result: exit `101`. The compiler reported the expected unresolved artifact DTOs and source/dependency/import/export hash helpers.

### RED-3 — artifact behavior absent

Command:

`cargo +1.96.1 test --locked -p heleos-pdf-protocol --all-features`

Result: exit `101`; `1 passed, 4 failed, 0 ignored`. The compiled tests failed on strict artifact-record validation, canonical hashes, source framing, and Cargo metadata normalization.

### GREEN-1 — public wire and artifact primitives

Commands:

- `cargo +1.96.1 test --locked -p heleos-core --test pdf_probe`
- `cargo +1.96.1 test --locked -p heleos-pdf-protocol --all-features`

Results: exit `0`; core integration `6 passed, 0 failed`; protocol unit tests `5 passed, 0 failed` and doc tests `0 failed`.

## Guest/parser TDD evidence

- Geometry RED: focused guest tests first failed to compile on the absent resolver and typed geometry failures. GREEN: `3 passed, 0 failed` for inherited/nonzero/fractional boxes, all four matrices, negative rotation, ties-even rounding, box/axis/UserUnit separation.
- Raw-xref RED: focused tests first failed to compile on the absent canonical preflight. GREEN checkpoint: `5 passed, 0 failed` for free-tombstone/work bounds, malformed Encrypt precedence, xref-stream unknown type/raw cap, current/older hybrid plus overlap, and cycles/negative/out-of-range links.
- Object-stream RED: focused tests first failed to compile on the absent request-scoped filter/preflight. GREEN checkpoint: `3 passed, 0 failed` for ordered IDs/reset, strict ASCII/increasing offsets/header gaps, and typed decompression limit.
- Inspection RED: focused tests first failed to compile on the absent inspection entry point. GREEN checkpoint: `3 passed, 0 failed` for accepted geometry/page order, frozen document precedence/feature ordinal, and N/N+1 pages.
- Follow-up behavioral RED: `raw_xref_rejects_out_of_range_normal_and_free_list_offsets` compiled and failed because an offset beyond input was accepted; `non_page_parent_edges_remain_subject_to_reference_depth` compiled and failed because every `/Parent` edge was incorrectly exempted. Both are now GREEN.
- Review-driven behavioral REDs proved that swapped active normal-object offsets, an unrepresented xref-stream object, a stream-backed page node, and a dangling non-page reference were each previously accepted or misclassified. The minimal fixes bind exact object headers and xref-stream self-representation, require true page dictionaries, distinguish corrupt references from depth exhaustion, make page-tree traversal iterative, and prevalidate filtered-xref predictor allocation shape.
- Further GREEN coverage binds a 10,000-node stack-safe page chain, exact reference depth N/N+1, multi-feature ordinal arbitration, page-string/content-stream/image-stream inert feature words, decoded octal-string and `#XX`-name metadata N/N+1, JCS i53 translation endpoints/one-over, and exact raw/decoded xref-stream cap N/N+1.
- The allocation-order xref predictor test combines an oversized `/DecodeParms` row with malformed Flate bytes and proves typed `MetadataBytes` is selected before lopdf decompression can allocate or report corruption.
- Encryption precedence is pinned in both directions: an authoritative classic or xref-stream trailer `/Encrypt` key (including `#XX` key decoding) outranks malformed values/metadata/structure once reached, while a three-row newest section returns the indirect-object work limit at public N=1 before its trailer and `Encrypted` at N=2 after reaching that trailer within the N+1 examined-row allowance. Value/string/nested-dictionary display text never classifies encryption.
- The xref-stream counterpart first compiled RED because `/Encrypt` was returned before the examined-row budget was charged, selecting `Encrypted` at N=1. After moving authoritative encryption classification after the required row examination, `cargo +1.96.1 test --locked --offline -p heleos-pdf-guest xref_stream_work_limit_precedes_unreached_encryption_dictionary -- --nocapture` is GREEN `1 passed, 0 failed`: N=1 is `WorkLimit`, N=2 is `Encrypted`.
- Page-tree review found two real structural gaps: root `/Pages` accepted an impossible `/Parent`, and the reference walker exempted every page-shaped dictionary's `/Parent` rather than only nodes in the validated page tree. Both focused tests first compiled and failed against the old behavior. The validator now rejects root backlinks and returns the exact validated page-node parent map; only root dictionaries of those reached nodes receive the general-reference exemption, so an orphan page-shaped object cannot hide a dangling or over-depth edge.
- Current command: `cargo +1.96.1 test --locked --offline -p heleos-pdf-guest --all-features`; result exit `0`, `35 passed, 0 failed`. Exact combined-precedence and both page-tree review tests are included.

## Host runtime/staging TDD evidence

- Typed stream/limiter/arbitration RED: focused tests failed to compile on absent `RecordingStdout`, `RecordingLimiter`, typed signals, and compound arbitration. GREEN proved exact-cap stdout, overflow recording, typed memory/table denial without message parsing, no false flag for a module-maximum failure, and frozen signal priority.
- Owner/admission/ticker RED: focused tests failed to compile on absent admission, runtime owner, and ticker. GREEN proved a blocked third permit, timer execution from callers nested inside both Tokio runtime flavors, exactly configured two-worker owner runtime, and two independently relative epoch interruptions.
- Real execution RED: focused tests failed to compile on absent async module executor. GREEN proved typed fuel exhaustion, dynamic memory growth denial, the 257th host call, stdout overflow, and handled stderr through real private WAT modules.
- Staging RED: focused tests failed to compile on absent `StagedInput`. GREEN proved retained parent/root/input identity, exact private/read-only permissions, exact single input, length/digest checks, explicit `TempDir::close`, parent recheck, cleanup, and fail-closed permission tampering.
- Public boundary GREEN proves a pre-seeked `VerifiedObject` is rewound/rehashed, Wasm executes on the owner runtime while the caller is inside an unrelated current-thread Tokio runtime, the response is revalidated and provenance-injected, staging is empty afterward, revision mismatch is hard `Integrity`, and tightened input bytes quarantine without staging.
- Shared module-extractor behavioral REDs proved that syntactically valid GC operators, out-of-range active element-table/function and data-memory indices, shared globals, and inverted memory/table limits were previously accepted. The protocol extractor now rejects those plus custom-descriptor/shared-everything/function-reference/stack-switching operator groups, validates every ref-bearing operator and type, and drains all relevant bodies/expressions without enabling a validator dependency.
- Real capability GREEN proves the retained read-only preopen denies `../secret` reads and denies write rights on `input.pdf`; the outside sentinel remains unchanged.
- Cleanup arbitration GREEN injects explicit staging cleanup and parent recheck outcomes, proving cleanup hard errors override every candidate and a clean lifecycle preserves the candidate.
- Dispatch/cleanup behavioral RED: `cargo +1.96.1 test --locked -p heleos-core --lib reports_explicit_staging_cleanup -- --nocapture` ran two tests and both failed because closed dispatch and a panicking Tokio job disconnected the cleanup-observer channel. GREEN: the same command passes `2 passed, 0 failed`; the production job now owns a drop-safe explicit cleanup observer, reports `StagedInput::close` on dispatch rejection and task unwind, and the synchronous caller waits for cleanup so a cleanup/parent-recheck error overrides dispatch, panic, or candidate output.
- Instantiation resource-failure behavioral RED: `cargo +1.96.1 test --locked -p heleos-core --lib instantiation_resource_failure_reaches_unclassified_sandbox_arbitration -- --nocapture` failed because callback-recorded allocation failure still returned hard `Integrity`. GREEN: `1 passed, 0 failed`; a typed limiter denial or callback-recorded resource failure now reaches frozen arbitration, while a signal-free opaque instantiation failure remains hard `Integrity`. The real memory/table module-maximum execution test remains GREEN `1 passed, 0 failed`.
- Component-model behavioral RED: a valid component binary was admitted by the production engine because the transitive Wasmtime feature default remained enabled. After the explicit `wasm_component_model(false)` configuration, `cargo +1.96.1 test --locked --offline -p heleos-core --lib production_engine_disables_the_component_model -- --nocapture` is GREEN `1 passed, 0 failed`.
- Unix root-substitution behavioral RED proved a retained staging root renamed within its retained parent leaked after `TempDir::close` targeted the now-missing original path. Cleanup now locates the unique capability-relative retained inode, removes it explicitly, and rechecks the parent; the focused substitution test and the five-test Unix private-staging suite are GREEN.
- The committed `cfg(windows)` source matrix covers exact parent/root/input protected private DACLs, READONLY input, no-delete sharing, distinct root symlink/root junction/non-symlink reparse/input symlink/input reparse rejection, marker mismatch, all three preopen identity brackets, and explicit no-leak cleanup across success/quarantine/trap/timeout/limit/setup/substitution outcomes. The unstable Rust `junction_point` API and inherited `SystemRoot` were both rejected: the final bounded junction fixture is an absolute create-new fixed `.cmd` with an exact private DACL and non-reparse retained handle, constant bytes, empty environment, controlled cwd, exact directory inventory, explicit `.cmd.exe` shadow exclusion, junction reparse readback, and explicit script cleanup. The selector received independent approval.
- Windows input creation now requests the exact data-read/data-write/READ_CONTROL/WRITE_DAC mask `0xc0060000` before applying the handle-bound DACL; read-only reopen requests `0x80020000`. The test fixture reuses the same production creation path. The pinned-Rust `MetadataExt::number_of_links` E0658 was removed by cloning the retained `std::fs::File`, wrapping it in safe `cap_std::fs::File`, and using `cap_fs_ext::MetadataExt::nlink`.
- Exact dynamic boundaries now prove table growth from 1 by 9,999 succeeds and growth by 10,000 yields typed `TableElements`; 256 host calls succeed and the 257th yields typed `HostCalls`.
- Current focused command: `cargo +1.96.1 test --locked --offline -p heleos-core --all-features pdf::wasi_host::tests -- --nocapture`; result exit `0`, `42 passed, 0 failed`.
- Current command: `cargo +1.96.1 test --locked --offline -p heleos-core --all-features`; core library result `74 passed, 0 failed`; integration `pdf_probe` result `7 passed, 1 failed` only at the controller-held `tracked_guest_build_is_reproducible_across_distinct_roots` dependency-normalization seam. One earlier unrelated existing store race failed transiently, passed alone, then passed in the complete rerun.
- Source-closure behavioral REDs proved local macro repetition and builtin include-macro aliasing could hide external reads; closure now rejects every local `macro_rules!` and every code token equal to `include`, `include_bytes`, or `include_str`. A later exact seven-file live gate found the lexer desynchronized on ordinary `'b'`; the bounded char parser now consumes only exactly one scalar or valid bounded escape followed immediately by `'`, preserves lifetimes, and rejects the two-lifetime/include hiding attack. Exact live source closure is GREEN.
- Lock-projection grammar behavioral RED accepted forbidden source-without-version and leading/repeated/trailing-space spellings. Parsing now admits only exact single-space `name`, `name version`, or `name version (source)` forms while retaining contextual no-version equivalence. The independently approved protocol source SHA is `aa9b6d5888c45c804a447b817d3b416bec145ab36af7b81a3fe8c908167f475c`.
- Current command: `cargo +1.96.1 test --locked --offline -p heleos-pdf-protocol --all-features`; result exit `0`, `14 passed, 0 failed`, plus `0` doc-test failures.
- Frozen fixture inventory now contains 53 governed deterministic vectors; exact hash gate exit `0`, `1 passed, 0 failed`.
- `cargo +1.96.1 fmt --all --check` exits `0`. Strict all-target/all-feature clippy reaches one controller-held artifact-launcher-only `collapsible_if` at `crates/heleos-core/tests/pdf_probe.rs:1907`; production and all independent Task 5 paths are otherwise warning-clean, and the held line was not edited.

## Windows compile evidence

- The normal frozen command `cargo +1.96.1 check --locked -p heleos-core --all-targets --target x86_64-pc-windows-msvc` cannot reach Heleos Rust in this macOS environment: governed `wasmtime-internal-fiber 48.0.1` first stops compiling `src/windows.c` because `windows.h` is absent, and `libsqlite3-sys 0.38.2` later lacks `stdlib.h` through `mm_malloc.h`.
- The retained final Rust-only probe is `/private/tmp/heleos-task5-windows-probe-final.bMzCqE`. It uses the approved exact poison `windows.h` bytes (`998d03d3a9c0fc18c65ace24d09a50194b9b941b28753f0b2dbbe1b57ed934d4`), canonical Xcode clang, and pinned-sysroot `rust-lld -flavor link /lib`. Its final locked/offline all-target MSVC check compiles the staged cfg(windows) source successfully with exit `0` and no warnings. The complete probe evidence is recorded in the final staged-gate section below.
- The erroneous earlier work-in-progress probe was moved recoverably to `/Users/bekim/.Trash/heleos-task5-windows-probe-wip.rbDHYG`. It is not final evidence.

## Artifact blockers held by controller

- Exact Rust 1.96.1 `wasm32-wasip1` build succeeded and initially produced a 991,214-byte module. Its unremapped SHA-256 was `516c0e2a...`; the controller moved it recoverably to `/Users/bekim/.Trash/heleos_pdf_guest.unremapped-516c0e2a.wasm`, leaving the canonical generated path absent. No manifest hash was recorded.
- The approved `7745eeb...` amendment freezes `_start: () -> ()` plus compiler/CRT `__main_void: () -> i32`, host lookup of `_start` only, `rust_path_remap = "heleos-rust-path-remap/v1"`, and a shared pure build policy/scanner used by Tasks 5/7/9.
- The first real distinct-root launcher execution stopped before build because pinned Cargo 1.96.1 metadata format v1 omits registry checksums even though Cargo.lock v4 contains them. Full-workspace metadata also feature-unifies core's `artifact-host` edge into protocol, which is not the guest-only graph. The controller has held checksum normalization, launcher, and manifest edits while approving an isolated three-member guest resolution context and exact Cargo.lock-v4 join. The tracked artifact manifest remains a deliberate placeholder until the exact approved controller commit/blob is supplied.
- The approved first cache-split implementation produced a genuine launcher RED: a Cargo home containing only graph-active archives and sparse entries cannot rerun the unlocked guest resolution, because Cargo first requires the inactive optional `toml` sparse entry. A narrow temporary diagnostic copied sparse entries for all 67 registry names in the validated pruned lock while retaining active-only archives; Cargo then failed offline requesting inactive `cpufeatures 0.3.1`.
- A second temporary diagnostic used the exact governed seven-file/three-member resolution root and frozen pruned lock with exact argv `+1.96.1 build --locked -p heleos-pdf-guest --target wasm32-wasip1 --release`. An active-only archive cache again failed on inactive `cpufeatures 0.3.1`; a checksum-verified all-pruned archive/index cache succeeded in 13.08 seconds. No resolver-only package was compiled. Temporary lock parsing/copy diagnostics were fully removed afterward. The controller is freezing the necessary shared all-pruned cache-identity/build-trace boundary before production implementation resumes.
- Process-tree containment has its own genuine RED/GREEN: a Unix grandchild retaining inherited stdout/stderr made the old timeout return only after 3.02 seconds and failed its under-two-second assertion. A new per-command process group, TERM/KILL escalation, and bounded channel drain makes timeout and overflow variants pass `2/2` in 0.17 seconds; the Windows branch uses a separately validated absolute `System32/taskkill.exe` selector rather than an inherited search path.

## Live build-evidence RED — pinned Cargo weak optional edge

- Controller base remains `997b614e9b4411de219e51478b96114d7ea499ec`; authoritative plan blob remains `e3e1489cf6e677be51cbae5be4aa734749c19b4f`. The latest independently approved protocol blob before this RED is `7c9642afd162b8bef9c4fda689d221fe6b2aa361`.
- Exact command: `cargo +1.96.1 test --locked -p heleos-core --test pdf_probe tracked_guest_build_is_reproducible_across_distinct_roots -- --nocapture`.
- Result: exit `101` after the first isolated controlled build completed successfully with empty stderr; the shared typed validator returned `InvalidDocument` at the metadata/trace unit-set equation. Production cleanup behavior was immediately restored before analysis. The tracked manifest remains the zero placeholder and no artifact was published.
- Exactly one ignored bounded diagnostic scratch is retained at `/var/folders/_f/r9mx9j7n0vd3tpjmy1snndyr0000gn/T/.tmp9Ca52a`. Its exact trace is `first-root/short/target-output/cargo-trace-diagnostic.jsonl`: 77,148 bytes, 78 LF records, SHA-256 `9230ebc5b31252ced6bcff011fec49c9f45813acbf1dfadbf05313b739d6a8cb`. The records are exactly 68 `compiler-artifact`, nine `build-script-executed`, and one final successful `build-finished`; all artifacts have `fresh = false`. They cover exactly 58 package identities, including exactly 56 registry identities. The profile/unit shapes are one guest bin at opt 3, nine custom builds at opt 0, four host libs at opt 0, 52 guest libs at opt 3, and two proc macros at opt 0, with every frozen debug/test/overflow flag false and debuginfo zero.
- The retained pruned lock is 15,584 bytes with raw SHA-256 `511be5a28809ca4eb332715fc8d6fd54d5b19a7ad4b6123a0a63efc727e5e8f9`; the retained copied production lock is 70,678 bytes with raw SHA-256 `d06571ece5da554829b071c585e26ade7d4bf8af9ed83ee083a1009397b3f8fa`. An exact locked guest-metadata rerun is 281,158 bytes with SHA-256 `1e90c321c6b444beb7aed882816c04e776e4a26bcc65d69fbb4c2346d441c766`; the supplemental full metadata is 1,062,655 bytes with SHA-256 `1236ebd06be84732d08375a5afbca0e05f967b5284057e0f0c76d6fd228399a2`.
- A field-by-field join proved every emitted target, crate type, feature union, profile, filename/executable, and all nine build-script identities match. The first and only unit-set mismatch is `registry+https://github.com/rust-lang/crates.io-index#zlib-rs@0.6.7|zlib_rs|lib|lib`: current normal/build traversal of locked metadata yields 59 reached package IDs and 69 expected compiler units, while the exact build emits the frozen 58/68 set without zlib-rs.
- Exact cause: `flate2 1.1.10` has active metadata features `any_impl`, `default`, `miniz_oxide`, `runtime_detection`, and `rust_backend`; its manifest defines `runtime_detection = ["zlib-rs?/std", "crc32fast?/std"]`, while actual activation of the optional zlib-rs dependency requires the separate `zlib-rs = ["any_zlib", "dep:zlib-rs"]` feature. Nevertheless pinned Cargo 1.96.1 metadata v1 emits a target-null normal `flate2 -> zlib-rs` entry and a zlib-rs node with features `rust-allocator,std`. The canonical 455-byte flate/zlib metadata slice has SHA-256 `57a9b86e8099a40b8a12616293b3c83e06b76c7dd638d23fcc2ab64bf5d2f98b`; the exact cached flate2 normalized manifest has SHA-256 `1365f28dd7ad9f83ebcc9afd545d7cdcedb284bf6e059081b0e1d1847ce6a94e`.
- Independent exact `cargo +1.96.1 tree --locked --offline -p heleos-pdf-guest --target wasm32-wasip1 -e normal,build` output is 87 lines/3,680 bytes with SHA-256 `3add4c58cc5b9256ff8d91064fca0d6816893d8739fb44eb7dc608955b553c50`; it reaches `flate2 -> miniz_oxide` and does not reach zlib-rs. The controller has held all semantic changes pending an approved design decision; no trace rule, graph rule, dependency feature, or manifest field was relaxed.

## Current artifact/publication status (supersedes the historical blocker sections above)

The controller approved and committed the feature-aware classifier amendment at `30482d084510ac35d58e37402ccf8d4ecaca7f39`; the complete current authoritative plan blob is `797cc9f67db948a3d4ec1268bab867ebaf9b3b18`. The implementation now classifies the exact pinned-Cargo weak optional edge rather than treating every resolve edge as active. No dependency, Cargo trace, target-unit, or manifest requirement was relaxed.

Exact approved/frozen source identities at the overall artifact-audit boundary:

- Protocol `crates/heleos-pdf-protocol/src/lib.rs`: git blob `af73661df0bac59a3effee3f0a5a6bfe41406e90`; SHA-256 `909fe9afd5f8f1e785f8e4b13354e16b96eddfac6b1cbea9d691976f8d06b488`.
- Launcher/publication/E2E `crates/heleos-core/tests/pdf_probe.rs`: git blob `ea0b59c08dcaae221a9eef2a88d6cac95fe36e02`; SHA-256 `6e40b4f86f43ae947c8ce191df3f04c36d45d5aafda42dae21bf7eee239d8a83`.
- Populated `artifacts/pdf-guest/manifest.toml`: git blob `f3e0c6038f66316189f1a1e6c23194dadfd262de`; SHA-256 `61c4db1f34eb683b1821dde4df007172ff19595e2dee10cf3f20bf2f6b03ef55`.
- Six-source LF policy `.gitattributes`: git blob `8e88ff619ec61fa8cb9a26f075e9af87e23152fc`; SHA-256 `2474aff9dd1f75373c4684081c2de1377317de2dede70bf8f026a6506241fb41`.

Final classifier/build-evidence facts from the retained exact Cargo 1.96.1 tuple:

- Locked guest metadata: 281,158 bytes, SHA-256 `1e90c321c6b444beb7aed882816c04e776e4a26bcc65d69fbb4c2346d441c766`.
- Supplemental full metadata: 1,062,655 bytes, SHA-256 `1236ebd06be84732d08375a5afbca0e05f967b5284057e0f0c76d6fd228399a2`.
- Reached graph: exactly 58 identities, 56 registry identities, and 86 normal/build edges; `zlib-rs` is absent.
- Build trace: exactly 68 compiler artifacts, nine build scripts, one final success, and no stderr.
- Domain-separated resolution-lock digest: `355ee328894390cc65ea62ff25020a67cfd614042864f41a0cebe1eeb6f3ff52`.
- Domain-separated cache-plan digest: `809419491262af6647fc246b0e9f556ae9deeba8570a80b87cac1029bc52580c`.
- Protocol review gates: `cargo_metadata_` focused tests `11/11`; full all-feature tests `27/27` plus zero doc-test failures; strict all-target/all-feature Clippy with `-D warnings`, formatting, diff-check, and guest tree exclusions all GREEN. Two independent reviewers approved the exact protocol blob.

Final artifact RED/GREEN and publication evidence:

- RED command: `cargo +1.96.1 test --locked --offline -p heleos-core --test pdf_probe tracked_guest_build_is_reproducible_across_distinct_roots -- --exact --nocapture`. Result: exit `101` after 94.75 seconds only because the deliberately zeroed tracked manifest did not equal the fully verified reproducible candidate. Both candidate builds, graph/evidence joins, prefix scans, and byte equality had already passed.
- The exact emitted candidate was populated into the tracked manifest using `apply_patch`: 58 dependency records, 11 imports, and three exports.
- Before the publication GREEN, an unrelated stale ignored canonical artifact was found at 1,019,373 bytes/SHA-256 `3cf9df71571ebba52d19a4b3374dc518798b0f3477d3d3ff9a55f5d1e506afd2`. The controller verified and moved it intact to `/private/tmp/heleos-task5-stale-artifact.NS2dCD/heleos_pdf_guest.wasm`; the launcher never deleted or overwrote it.
- GREEN command: the same exact distinct-root test. Result: exit `0`, `1 passed, 0 failed`, 14 filtered out, in 110.81 seconds. It rebuilt in two distinct workspace/cache/target roots, obtained byte-identical fully validated candidates, matched the populated manifest, atomically published no-replace, reopened and revalidated the winner, and explicitly cleaned both build trees.
- Published ignored generated artifact: `target/wasm32-wasip1/release/heleos_pdf_guest.wasm`, exactly 1,018,667 bytes, SHA-256 `fb27d71bf20cdfbbd31ebc96cf203ea3802dad1000855fcc56e6aad2e3112efc`. It is not tracked and must never be staged.
- Manifest source-tree digest: `4eb2e265095b6541735932d234efb425327a9566872f8cc9e627dd68cb412c90` over the exact six source members, including `crates/heleos-test-fixtures/Cargo.toml`.
- Manifest reached dependency-graph digest: `63c98f658963977879d12eeacbc07299a9a01c1bdae663c7e006a698e0f8cb01`.
- Manifest import digest: `77cd0b91fdf2ecec95658e1e3aaf50b4aebef727ca3b331358d2a98fd693452d`.
- Manifest export digest: `e6dbdd01e52ce17be5b0e43e73c7c79a5a17fa2b900f2efdfa0685a9cfd811e9`.
- Real public-boundary command: `HELEOS_PDF_GUEST=target/wasm32-wasip1/release/heleos_pdf_guest.wasm cargo +1.96.1 test --locked --offline -p heleos-core --test pdf_probe tracked_guest_executes_public_two_page_ten_thousand_page_and_rejection_paths -- --exact --nocapture`. Result: exit `0`, `1 passed, 0 failed`, 14 filtered out, in 7.74 seconds. The tracked guest accepted exact two-page and 10,000-page fixtures, quarantined bad magic, and left the retained private staging parent empty after each outcome.

## Post-audit Clippy and frozen-evidence repair (supersedes the artifact bytes above)

The first overall release audit found three acceptance gaps after the preceding publication: strict workspace Clippy rejected two complex guest page-tree types and the eight-argument reference walker; the launcher had one nested-if lint; and the live Cargo evidence was compared only A-to-B rather than also to a committed reviewed golden. The guest now uses named `PageTreeParents` and `ReferenceWalkContext` types without an `allow`, and the launcher uses the equivalent warning-free let-chain. The artifact is source-bound, so this semantic-neutral guest repair deliberately invalidated the preceding published bytes and manifest source digest.

Exact source identities at the current frozen re-audit boundary:

- Protocol: git blob `af73661df0bac59a3effee3f0a5a6bfe41406e90` (unchanged from its dual approval).
- Guest: git blob `ffaf8c1a8b13ce6a24ddb85d1115e8194c4098cc`.
- Launcher: git blob `84e46dc7509b3026bce5dc79f77d2aae46de3c1d`; SHA-256 `599b8d585c181d9f3cae8cbdefd414291078dfba7f4be71f162ffc374c0d2ab5`.
- Populated manifest: git blob `798c58b5f7db47ba3db30560bc709d2c5961cab7`.
- Six-source LF policy: git blob `8e88ff619ec61fa8cb9a26f075e9af87e23152fc`.

The controller-approved typed diagnostic completed both distinct-root builds and emitted the exact repaired candidate before manifest comparison. The diagnostic branch was removed immediately; only its three exact typed manifest field values were applied:

- Candidate WASM SHA-256: `c4a39659129a3d7fe97b3cf01753eec85e2474f514d50cb82b379ccf0f0aedaf`.
- Candidate length: `1,018,666` bytes.
- Candidate six-source digest: `23b4a02506504b2513046812cbc8b3e357111943759b6c5a76336da330bbc62b`.
- Dependency graph/import/export/resolution digests and their typed records remain unchanged from the preceding verified candidate.

The committed launcher now freezes all live evidence rather than accepting same-run equality alone:

- 67 checksum-bound pruned resolution archives.
- 58 active identities, including exactly 56 active registry identities.
- 68 compiler units and nine build scripts.
- Resolution-lock digest `355ee328894390cc65ea62ff25020a67cfd614042864f41a0cebe1eeb6f3ff52`.
- Cache-plan digest `809419491262af6647fc246b0e9f556ae9deeba8570a80b87cac1029bc52580c`.
- Exact `GuestBuildEvidenceV1` JCS length 18,188 bytes, framed by `heleos-pdf-guest-build-evidence-v1\0`, with SHA-256 `f57df7089b3c6d1234f31d436fb514bc746b6aaa201c3b290a0811c3a6f62d83`. This single reviewed digest binds every exact compiler tuple and each build-script identity/cfg/env record in addition to the explicit counts and lock/cache-plan digests.

New acceptance negatives are GREEN:

- `cargo +1.96.1 test --locked --offline -p heleos-core --test pdf_probe inactive_cpufeatures_cache_inputs_are_required_and_unplanned_entries_fail_preflight -- --exact --nocapture`: `1 passed, 0 failed` in 23.02 seconds. Removing either the inactive `cpufeatures 0.3.1` archive or its returned sparse-index entry makes the real offline resolution fail; injecting an unreturned full-workspace-only `bytes 1.12.1` archive fails the exact cache preflight.
- `cargo +1.96.1 test --locked --offline -p heleos-core --test pdf_probe build_target_precondition_rejects_zero_extra_preexisting_and_rebound_temp_inventory -- --exact --nocapture`: `1 passed, 0 failed` in 0.02 seconds. It rejects zero temp entries, any extra target entry, nonempty preexisting temp content, and a post-precondition temp identity rebound.
- Whole-root identity first compiled RED because moving the original `.heleos-tmp` inode into a substituted target root and supplying an otherwise-valid candidate passed post-build validation. The launcher now retains and rechecks target plus temp device/file IDs. A second exact RED proved the caller could swap the target after inventory and make its ambient artifact reopen read `attacker-substituted-candidate` rather than `cargo-produced-candidate`. The candidate is now opened no-follow and read through the retained checked target capability; its inventory device/file ID/link-count/length/hash and retained root/temp markers are checked before and after, and the ambient target path must still resolve to that retained root. Both focused regressions are GREEN.
- Toolchain identity tests first failed `0/2`: canonical-path equality rejected valid same-file hardlink rustup proxies, while a new executable inode at the recorded path passed stale validation. `ToolProxySet` now binds the exact lexical cargo/rustc/rustup names to one regular executable device/file-ID/length marker, accepts symlink or hardlink proxy layouts, and rejects replacement. The complete set is rechecked immediately before and after every Cargo/rustc probe, metadata command, build-policy call/build, including process-error paths. Windows uses safe by-handle `cap_std` metadata; Unix additionally requires execute bits.
- The remaining non-publication `pdf_probe` suite is GREEN `18 passed, 0 failed`; strict `cargo +1.96.1 clippy --locked --offline --workspace --all-targets --all-features -- -D warnings`, `cargo +1.96.1 fmt --all -- --check`, and `git diff --check` are GREEN.

After dual static/non-publication clearance, the controller verified and moved the preceding ignored `fb27d71bf20cdfbbd31ebc96cf203ea3802dad1000855fcc56e6aad2e3112efc`/1,018,667-byte artifact intact to `/private/tmp/heleos-task5-old-canonical.8y64qh/heleos_pdf_guest.wasm`; the launcher neither deleted nor overwrote it. Final publication evidence on the frozen bytes is:

- Absent-winner command: `cargo +1.96.1 test --locked --offline -p heleos-core --test pdf_probe tracked_guest_build_is_reproducible_across_distinct_roots -- --exact --nocapture`; exit `0`, `1 passed, 0 failed`, 19 filtered out, in 101.27 seconds. Both distinct-root candidates reproduced the frozen counts/digests/evidence and exact manifest, then the launcher atomically published and reopened the candidate.
- Published ignored canonical: `target/wasm32-wasip1/release/heleos_pdf_guest.wasm`, exactly 1,018,666 bytes and SHA-256 `c4a39659129a3d7fe97b3cf01753eec85e2474f514d50cb82b379ccf0f0aedaf`; `git status --ignored` reports it only beneath `!! target/`.
- Equal-existing-winner command: the same exact distinct-root test; exit `0`, `1 passed, 0 failed`, 19 filtered out, in 99.52 seconds. The no-replace winner branch left hash and length unchanged after full A/B regeneration and revalidation.
- Real boundary command: `HELEOS_PDF_GUEST=target/wasm32-wasip1/release/heleos_pdf_guest.wasm cargo +1.96.1 test --locked --offline -p heleos-core --test pdf_probe tracked_guest_executes_public_two_page_ten_thousand_page_and_rejection_paths -- --exact --nocapture`; exit `0`, `1 passed, 0 failed`, 19 filtered out, in 7.61 seconds. The exact repaired tracked artifact accepts the two-page and 10,000-page vectors, quarantines bad magic, and leaves staging clean.

## Final staged-source gates

The index contains exactly the twenty authorized Task 5 paths: fourteen additions and six modifications, with 23,773 insertions and 153 deletions. `git diff --cached --check` is clean. The exact cached paths/blobs are:

```text
A  .gitattributes                                      8e88ff619ec61fa8cb9a26f075e9af87e23152fc
M  Cargo.lock                                          cff8bd3e1312e743c38275935e107db10de96e43
M  Cargo.toml                                          66a0b59d5d1c7424c50a19b38bc079b65a587cef
A  artifacts/pdf-guest/manifest.toml                   798c58b5f7db47ba3db30560bc709d2c5961cab7
M  crates/heleos-core/Cargo.toml                       e0dd70fa2823f7a3170f2e82874f6d014b5590d5
M  crates/heleos-core/src/lib.rs                       03aa4ce014d52a26abaaa182ac69316b91d914e1
A  crates/heleos-core/src/pdf/geometry.rs              f36f5d62e79deac0e0b7d60d410ddcde9a7f07d4
A  crates/heleos-core/src/pdf/mod.rs                   aceefb210e8541e5e9a48966e8a3dfb5357489d5
A  crates/heleos-core/src/pdf/wasi_host.rs             0f2a7b9c012a8108e2ad58912b7c02f40226e7cb
A  crates/heleos-core/tests/pdf_probe.rs               84e46dc7509b3026bce5dc79f77d2aae46de3c1d
A  crates/heleos-pdf-guest/Cargo.toml                  20c928769d39d3fbae0fc7ea473b217acc057db0
A  crates/heleos-pdf-guest/src/lib.rs                  ffaf8c1a8b13ce6a24ddb85d1115e8194c4098cc
A  crates/heleos-pdf-guest/src/main.rs                 360330d9e8e2e9f09d47bbe4c24b6a984f616f99
A  crates/heleos-pdf-protocol/Cargo.toml               af68fc2c2cf0db3437321db72c5624bc9a726cd1
A  crates/heleos-pdf-protocol/src/lib.rs               af73661df0bac59a3effee3f0a5a6bfe41406e90
A  crates/heleos-test-fixtures/Cargo.toml              610648787eba656a03b2f19f5e76bea2d44e28bc
A  crates/heleos-test-fixtures/src/lib.rs              15cc73233c151cd98626b80b6d01bd77954efcad
M  governance/fixtures.toml                            c488a3e2af7222dae350c192b47fd0b79185e22e
M  governance/tools.toml                               82e2f0c4247d7e1447cdaa601caeb0838468050b
A  tests/fixtures/pdf/README.md                        84063c3c293c3c6ea4437ecbeb96c28a78de1c68
```

The ignored report/brief/progress files, authoritative plan, retained probe, and generated artifact are absent from the index; `git ls-files '*.wasm'` returns nothing. The six source members each report `text: set` and `eol: lf`; the two governance files and populated manifest parse as TOML.

Final commands/results from the staged bytes:

- Exact equal-winner A/B test: exit `0`, `1 passed, 0 failed`, 19 filtered out, 97.19 seconds. The canonical artifact remains `c4a39659129a3d7fe97b3cf01753eec85e2474f514d50cb82b379ccf0f0aedaf`, 1,018,666 bytes.
- `cargo +1.96.1 fmt --all --check`: exit `0`, no output.
- `cargo +1.96.1 clippy --locked --workspace --all-targets --all-features -- -D warnings`: exit `0`, no warnings.
- `HELEOS_PDF_GUEST=target/wasm32-wasip1/release/heleos_pdf_guest.wasm cargo +1.96.1 test --locked -p heleos-core --lib --test pdf_probe`: exit `0`; core library `81/81`, `pdf_probe` `20/20`, including A/B in 101.45 seconds and real two-page/10,000-page/bad-magic execution.
- `cargo +1.96.1 test --locked --workspace --all-targets --all-features`: exit `0`; 232 counted tests pass—core 81, domain 16, migrations 33, PDF integration 20, vault 20, guest 35, protocol 27—with no failure or warning.
- `cargo +1.96.1 check --locked --workspace --all-targets --all-features`: exit `0`, no warnings.
- `cargo +1.96.1 metadata --locked --offline --format-version 1`: exit `0`; exact stdout is 1,256,508 bytes, SHA-256 `017f029a5ec5c149c37bd1a1c9f9018aec81876834aa2bdabcaf2523888a0f97`.
- `cargo +1.96.1 tree --locked -p heleos-core --edges normal`: exit `0`; neither `lopdf` nor `heleos-pdf-guest` is present.
- `cargo +1.96.1 tree --locked --offline -p heleos-pdf-guest --target wasm32-wasip1 -e normal,build`: exit `0`; 87 lines/3,580 bytes, SHA-256 `67287ec48bc98913149b37b2225cc30b9367a69533027af144ada2d188202cb3`; `toml`, `wasmparser`, `artifact-host`, `zlib-rs`, and the fixture are absent.
- Both locked/offline inverse trees resolve exactly `lopdf 0.44.0` under the guest and fixture packages.

## Final Windows compile/probe evidence

Normal command: `cargo +1.96.1 check --locked -p heleos-core --all-targets --target x86_64-pc-windows-msvc`. It exits `101` before Heleos Rust. The first governed stop is `wasmtime-internal-fiber 48.0.1` compiling `src/windows.c`, where ambient host `cc --target=x86_64-pc-windows-msvc` cannot find `windows.h`. The concurrent bundled `libsqlite3-sys 0.38.2` translation unit also stops at Clang's `mm_malloc.h` because the Windows CRT `stdlib.h` is absent. No other first stop occurred.

The retained governed probe remains `/private/tmp/heleos-task5-windows-probe-final.bMzCqE`; it is intentionally not cleaned pending controller authorization. Manifest SHA-256 is `276356191d289529c476495557bed28fdc6168dbf2aa8c5f3569f7b23afc16a4`, frozen lock SHA-256 is `686d37bf5a2540b491df7bafbd83ef95f2ad8d8b2f1f323ac99f2d20c7c43a2c`, and the only admitted include file has exact bytes `typedef void *LPVOID;\nLPVOID GetCurrentFiber(void);\n` and SHA-256 `998d03d3a9c0fc18c65ace24d09a50194b9b941b28753f0b2dbbe1b57ed934d4`.

Before the probe, no inherited compiler/flag/linker/runner variable was present. The exact added environment is:

```text
CC_x86_64_pc_windows_msvc=/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang
CFLAGS_x86_64_pc_windows_msvc=-I/private/tmp/heleos-task5-windows-probe-final.bMzCqE/poison-include
AR_x86_64_pc_windows_msvc=/Users/bekim/.rustup/toolchains/1.96.1-aarch64-apple-darwin/lib/rustlib/aarch64-apple-darwin/bin/rust-lld -flavor link /lib
```

Exact command: `cargo +1.96.1 check --locked --offline --all-targets --target x86_64-pc-windows-msvc`. It exits `0` with no warning. The pinned input authorities revalidate as: crate archive/checksum `596f1d85e06cbf9c9add9c78ea135171c5753264ffe94a83dcc082a1ff49fc9b`; `build.rs` `ddbaaacd6e6284ee020867aff9d83f2cf9f298cbcf60f592e42accd55cda8552`; `src/windows.c` `164d7680582e6c7709ad14904304d17b113b8c07cda7c39ea2c9611bc2679b92`. That source has exactly one include, `<windows.h>`, uses only `LPVOID` and `GetCurrentFiber`, and exports the versioned wrapper.

The preserved translation outputs are:

- COFF object `ea708c7824d36062-windows.o`: SHA-256 `e19e8e40aa210790eef430d48e7a7ad3f2481c421308f3b939ed7636c39e1748`; `file` reports Intel amd64 COFF; `llvm-objdump` reports `coff-x86-64`/`x86_64`.
- Static archive `wasmtime-fiber.lib`: SHA-256 `9eb30b861ff013e6663f8cae8f71fb072dd0c66592e0dd58d9eda8d726db7256`; `file` reports a current ar archive.
- Exact relevant symbol table: undefined `GetCurrentFiber`; defined text `wasmtime_fiber_get_current_48_0_1`. The object is check-only and is never linked or executed.

Tool evidence:

- Cargo proxy resolves to `/Users/bekim/.cargo/bin/rustup`, device `16777232`, file ID `8679601`, 11,053,296 bytes, SHA-256 `aeb4105778ca1bd3c6b0e75768f581c656633cd51368fa61289b6a71696ac7e1`; Cargo is `1.96.1 (356927216 2026-06-26)` and rustc is `1.96.1 (31fca3adb 2026-06-26)`, LLVM 22.1.2.
- Canonical Apple Clang is `/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/clang`, device `16777232`, file ID `1515428`, 141,373,024 bytes, SHA-256 `7def90dd8829726686213a747fc5bff1583df933dae5edc55d755479e0bfe00a`, Apple Clang 21.0.0.
- Canonical pinned-sysroot `rust-lld` is `/Users/bekim/.rustup/toolchains/1.96.1-aarch64-apple-darwin/lib/rustlib/aarch64-apple-darwin/bin/rust-lld`, device `16777232`, file ID `8325180`, 130,957,808 bytes, SHA-256 `1a19ae9ae29c2f5a73bd897fd53291e5a15b97cf1b89fed55297f5a3da716c16`; it is invoked only as `-flavor link /lib`.
- `llvm-nm` and `llvm-objdump` are canonical Xcode tools (Apple LLVM 21.0.0), with SHA-256 `d910f3acb104791e5475254000ede2aa129aa1a42eafcc7f5bdb27afffc642dc` and `3cccdd0dad838d2130d7fc6237aa176c8c13a593a56b79c64fd980ddb89c670c`. `/usr/bin/file` is 5.41/SHA-256 `ce0f6b87d442da6b67e3c735ce5e8315ec10b0bda124039376da805a8f9b0ac2`; `/usr/bin/ar` SHA-256 is `44a68ddc1983d6cff3fd35ba3f9ba5f82004216f1dcde69892b3d1b06e408698`.

The `modern_sqlite` probe retains `rusqlite/modern_sqlite`, `libsqlite3-sys/bundled_bindings`, and the `cc 1.4.4`, `find-msvc-tools 0.1.11`, and `shlex 2.0.1` package nodes/Wasmtime-fiber edges. The normalized no-dedupe tree contains 243 unique package identities on each side, with identical SHA-256 `698eb8238345e8f5376c5ff2b28856a041b607e003f656e8bdc5ec520c0b6b25`. Full metadata contains 249 registry identity/version/source/checksum tuples on each side, with identical SHA-256 `144dae56a4ea8e757635619505a248c98f387c74d103ad1ffc181cdbf1f36abf`. Production has 573 unique active edges and the probe 572: the sole removal is `libsqlite3-sys 0.38.2 -> cc 1.4.4`. There are 262 unique package-feature rows on each side. Relative to production the probe removes exactly three feature atoms—`rusqlite/bundled`, `libsqlite3-sys/bundled`, and `libsqlite3-sys/cc`; after only those allowed removals both feature sets hash to `2beb5347ada2a2e2254cb79facadf52a2b93505be78bedbe3c4423356b7cd9cb`. The production metadata has 253 packages versus the probe's 252 solely because the production workspace additionally contains the unreachable local `heleos-pdf-guest`; shared local core/protocol/fixture identities are preserved. No poison-header node or edge exists.

This is cross-target Rust/type/one deliberately unresolved C-object evidence only. Task 10 still owns real MSVC/Windows-SDK compilation, linking, and execution on native NTFS, the complete cfg(windows) DACL/reparse/identity/cleanup/runtime matrix, and two native distinct-root guest builds equal to the tracked SHA. Task 5 makes no native Windows runtime claim.

Both independent read-only reviewers approved the final frozen source and trust path with no Critical or Important finding, and the independent cached-byte audit approved the exact staged candidate. The resulting implementation commit is `d90a1639c3b5e4276d6164157c2507565d4eab74` with exact subject `feat: sandbox deterministic PDF probing`; this report remains ignored and unstaged.
