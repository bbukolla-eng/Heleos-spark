# Foundation Native Evidence Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. This is an approved Foundation 0.1 implementation detail; do not reopen architecture approval. The controller owns commits and integration.

**Goal:** Package returned Windows evidence into one deterministic, byte-preserving archive whose claims are limited to structural and hash consistency.

**Architecture:** A standard-library Python CLI validates copied local inputs, captures their identities and bytes, validates their cross-file bindings, builds a fixed USTAR archive, and publishes it through a same-directory hard link that cannot replace an existing output. It runs on the POSIX controller after the native run and never authenticates the originating machine or executes the candidate.

**Tech Stack:** Python 3.9–3.14 standard library, `unittest`, `tarfile.USTAR_FORMAT`, SHA-256, POSIX descriptor-relative filesystem operations, local read-only Git inspection; existing Rust 1.96.1 receipt tests remain compatibility evidence.

## Global Constraints

- Work only in `/Users/bekim/Heleos-spark/.worktrees/foundation-native-evidence-export-2026-09-09`, branch `build/foundation-native-evidence-export-2026-09-09`. Planning observed HEAD `a23d1254b7ec10a1551ab7a3b1883cf466ae4853`; the controller's fixture-repair commit may advance it before execution.
- Tracked implementation scope: create `scripts/export-foundation-native-evidence.py`, `tests/continuity/test_export_foundation_native_evidence.py`, and `docs/operations/foundation-native-evidence.md`. Add one guide link in `README.md` and one workflow row in `SKILLS.md` for this new operator action. No dependency, workflow, release-status, importer, Rust, or governance changes.
- Preserve the controller-owned existing change in `tests/continuity/test_apply_github_app_decisions.py`; its fixture repair is a separate preceding commit.
- CLI is exactly `python3 scripts/export-foundation-native-evidence.py --repo PATH --candidate FULL_SHA --summary PATH --manifest PATH --receipt PATH --output PATH [--human]` plus standard help. Reject non-POSIX hosts, including Windows, before any write.
- No network calls, candidate execution, Cargo invocation by the exporter, signature claims, CI queries, owner decisions, remote publication, broad deletion, or `os.replace`.
- Final artifact: one fresh uncompressed POSIX USTAR `.tar`, regular file with mode `0600`. Preserve all 17 source byte strings exactly; only the inventory is newly serialized.
- Native authentication, CI authority, owner approval, release approval, and independent proof of native execution are explicitly false. Local tests and archive creation do not close those gates.
- Use explicit scoped Git staging; four separate controller commits are fixture repair, causal failing tests, implementation, and docs/checkpoint. Do not mix existing fixture repair with exporter tests.

## Contract and implementation decisions

### Inputs and identities

Require explicit absolute paths for repo, summary, manifest, receipt, and output; reject NUL, `..` components, root-only paths, and symlink components without silently resolving them away. Spaces and Unicode in ordinary path components are supported. Reject overlapping input file identities, output equal to any input, and output in the transcript directory. `--manifest` is the transcript manifest, and its parent contains the canonical transcripts. Summary and receipt may reside in separate directories.

Open directory chains from `/` using `O_DIRECTORY | O_NOFOLLOW` and retain descriptors needed for source and output operations. Open source files with `O_RDONLY | O_NOFOLLOW | O_NONBLOCK` relative to pinned parent descriptors; `fstat` must show a regular file, `st_nlink == 1`, no special permission bits, and no group/other write bits. Ordinary `0600`, `0640`, and `0644` source files are allowed and never chmodded. Reject directories, FIFOs, sockets, devices, symlinks, and hard links before reading them. Capture `(st_dev, st_ino, st_mode, st_nlink, st_size, st_mtime_ns, st_ctime_ns)` and each parent's identity. Compare pathname identity with opened descriptor identity at opening and every recheck; do not rely on file descriptors alone to detect renamed/replaced pathname entries.

Require repo to be an existing real directory. Validate candidate as exactly 40 lowercase hexadecimal characters, then run bounded read-only `git -C REPO cat-file -t FULL_SHA` with `GIT_OPTIONAL_LOCKS=0`, sanitized inherited `GIT_*` overrides, captured output, and a 10-second timeout. Require output `commit\n`. The candidate need not equal controller HEAD: packaging commonly occurs after later controller commits. Never fetch missing objects or print Git output or the supplied path on error.

Bound input reads before allocating: summary JSON at most 16 MiB; manifest and receipt at most 1 MiB each; each transcript at most 64 MiB; combined 17 source payloads at most 512 MiB. Read at most cap+1 even when initial `stat` reports a smaller length. JSON nesting depth at most 32, total parsed values at most 100,000 per document, integer token length at most 16 and values in the JCS-safe integer range. Enumerate at most 4,096 direct directory entries using an iterator, stopping immediately on overflow. Exactly 14 canonical transcript basenames are required. Reject any other direct entry whose name ends with `.list.txt` or `.run.txt`, case-insensitively, including directories or symlinks with those suffixes. Tolerate unrelated non-transcript entries without reading them. Do not recurse. Repeat this transcript inventory check before publication.

Parse UTF-8 JSON strictly with duplicate-key rejection at every depth, reject BOM, non-finite constants, invalid Unicode surrogates, and trailing non-whitespace data. Apply size/depth limits before recursive processing; map parser/recursion exceptions to fixed errors. Manifest/summary original whitespace and CRLF remain untouched in the archive. Compare typed JSON semantics, so `true` is not accepted as integer `1`, nor `false` as integer `0`.

### Exact native schemas

Manifest root keys are exactly `schema,candidate_sha,platform,filesystem,suites`. Values bind schema `heleos.native-suite-transcripts/v1`, exact candidate, platform `windows-x86_64`, filesystem `NTFS`, and seven suites in this order:

| Suite ID | Selectors appended to `cargo +1.96.1 test --frozen` |
| --- | --- |
| `core-backup-restore` | `-p heleos-core --test backup_restore` |
| `core-store` | `-p heleos-core --lib store::tests` |
| `core-backup` | `-p heleos-core --lib backup::tests` |
| `platform-fs` | `-p heleos-platform-fs --lib` |
| `cli-unit` | `-p heleos-cli --bin heleos` |
| `cli-integration` | `-p heleos-cli --test cli` |
| `workspace-all` | `--workspace --all-targets --all-features` |

Each suite has exactly `id,run_argv,list_argv,list_exit_code,run_exit_code,list_path,run_path`; argv values are arrays of exact strings, `list_argv` appends `--`, `--list` to `run_argv`, exit codes are integer zero, and paths are precisely `ID.list.txt` and `ID.run.txt`. Never use unchecked manifest paths to open files.

Receipt root keys are exactly `schema,manifest_sha256,suites,status,candidate_sha,platform,filesystem,suite_count,total_listed,total_passed`. Require schema `heleos.native-suite-receipt/v1`, status `pass`, candidate/platform/filesystem matching the manifest, integer suite_count 7, and seven ordered suite records. Each record has exactly `id,list_sha256,run_sha256,listed,passed`; hashes are lowercase 64-character SHA-256, `listed` and `passed` are positive integers at most 1,000,000 and equal, and root totals are their sums. Recompute transcript SHA-256 values from captured original bytes and receipt.manifest_sha256 from original transcript-manifest bytes. Counts are assertions bound in the canonical receipt; the exporter checks their type, equality, order, and totals. It does not duplicate the Rust transcript parser or claim to independently reperform its semantic native-test verification.

Require the receipt bytes to equal JCS serialization plus one LF. Its closed schema permits only ASCII keys/strings and bounded nonnegative integers, so `json.dumps(receipt, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"` is JCS-equivalent for this restricted accepted domain. Do not generalize this serializer into an arbitrary JCS implementation. Reject pretty JSON, CRLF ending, missing LF, float spellings, unknown fields, and unexpected strings in the receipt.

Importer summary root keys are exactly `schema,status,mode,native_host,native_evidence,commit,destination,destination_created,manifest_sha256,bundle_sha256,gate_summary,gate_exit_code,gate_log,error_code,error,events`. Require schema `heleos.foundation-windows-native-import/v1`, `status == "PASS"`, `mode == "native_suites"`, exact Boolean `native_host == true`, `native_evidence == true`, `destination_created == true`, exact candidate commit, integer gate_exit_code 0, and error_code/error both null. `destination` and `gate_log` are nonempty strings, transfer manifest and bundle digests are lowercase SHA-256 strings, and events is an array of JSON objects. These Windows paths and event values are retained opaque and never opened, followed, or executed. `gate_summary` must be typed-semantically equal to the validated receipt; object key order and whitespace may differ.

The existing importer has **mode `native_suites`, not a `native_suites` Boolean field**. Its root `manifest_sha256` binds the **outbound candidate transfer manifest**, while `gate_summary.manifest_sha256`/receipt.manifest_sha256 bind the **transcript manifest**. Do not equate those two distinct hashes or claim to validate the outbound manifest/bundle, which are not inputs to this CLI.

### Archive and inventory

Exactly 18 regular-file members, in this order: `importer-summary.json`, `manifest.json`, `receipt.json`, then `transcripts/ID.list.txt`, `transcripts/ID.run.txt` for each ordered suite above, then `inventory.json`. No explicit directory members, PAX records, compression, absolute names, traversal names, environment paths, host identifiers, or timestamps generated by the exporter. Original source bytes may contain the original importer's Windows paths; preserve them exactly.

Every member has mode `0600`, mtime 0, uid/gid 0, empty uname/gname, type `tarfile.REGTYPE`, empty linkname, no PAX headers, and size equal to its original bytes. Use `tarfile.open(fileobj=..., mode="w", format=tarfile.USTAR_FORMAT)` and explicit `TarInfo`; never `TarFile.add` or filesystem-derived metadata. Always close the tar writer before fsyncing the underlying temporary file.

Inventory root is exactly:

```json
{"authority":{"ci_authority":false,"independent_native_authentication":false,"native_execution_independently_proven":false,"owner_approval":false,"release_approval":false},"candidate_sha":"FULL_SHA","claims":{"inputs_structurally_and_hash_consistent":true},"filesystem":"NTFS","members":[],"platform":"windows-x86_64","schema":"heleos.foundation-native-evidence-export/v1"}
```

`members` contains 17 records in preceding archive member order, each exactly `{"name": ARCHIVE_NAME, "sha256": LOWER_SHA256, "size": INTEGER_BYTES}`. Do not list the inventory itself and create a self-hash cycle. Serialize inventory with sorted keys, compact separators, UTF-8, and one trailing LF. Output paths, source modes/identities, candidate branch, current controller HEAD, current time, and random temporary names never enter inventory. Source identities are runtime guards, not deterministic metadata.

### Atomic publication and errors

Output basename must end in lowercase `.tar` and not begin with `.`; parent must already exist, be real and POSIX, and have no group/other write permission. Reject an existing output using no-follow lookup, including dangling symlinks. Open/pin the parent descriptor and revalidate its absolute path identity before publication. Do not create parents.

Only after all preflight validations, create a random same-parent temporary using exclusive descriptor-relative creation and mode `0600`, then `fchmod(0600)` independent of umask. Store captured source bytes in bounded memory and package those exact bytes. Re-read/hash/recheck all source descriptors, their pathname entries, parent paths, and transcript-name inventory after writing the tar and immediately before publication. A source mutation detected at any checkpoint fails closed. This detects changes across validation/packaging checkpoints; it is not a claim that hostile same-user writers cannot change files after the final check.

Flush and fsync the temporary file; revalidate output parent; atomically publish with `os.link(temp_name, output_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)`. `EEXIST` must preserve the winner byte-for-byte and return `OUTPUT_EXISTS`. Unlink only the exporter-owned temporary entry, then fsync the parent directory. Final output must be mode `0600`, regular, and nlink 1. Never fall back to replacement or copy-to-final. Prepublication failures remove only the temporary created by this invocation; never unlink a raced/existing final output.

If parent fsync or final validation fails after linking, retain the published archive and report `PUBLISH_UNCERTAIN` with `output_created: true`; do not pretend nothing was written and do not attempt race-prone rollback of a public name. Any exporter-owned remaining temporary is cleaned up when safe. A caller must inspect that exact output before retrying; no automatic overwrite or broad cleanup follows.

Success stdout is canonical JSON plus LF with exact keys `schema,status,candidate_sha,archive_sha256,archive_bytes,member_count,output_created,error_code,error`, schema `heleos.foundation-native-evidence-export-result/v1`, status `PASS`, member_count 18, output_created true, and null error fields. Failure uses the same keys, status `FAIL`, null candidate/hash/bytes/count, output_created false except post-link uncertainty, and a fixed code/message. Exit 0 on success and 1 on failure; help exits 0. `--human` prints fixed concise success/failure prose with the same code and authority limitation, never source text or paths. Argument errors use the same safe failure renderer; do not allow argparse to echo untrusted unknown arguments.

Fixed errors: `UNSUPPORTED_PLATFORM`/`POSIX controller required.`; `INVALID_ARGUMENT`/`Invalid export arguments.`; `INVALID_PATH`/`Unsafe export path.`; `INVALID_CANDIDATE`/`Candidate commit unavailable.`; `UNSAFE_SOURCE`/`Unsafe source file.`; `INPUT_LIMIT`/`Input limit exceeded.`; `INVALID_JSON`/`Invalid input JSON.`; `CONTRACT_MISMATCH`/`Evidence contract mismatch.`; `HASH_MISMATCH`/`Evidence hash mismatch.`; `SOURCE_CHANGED`/`Source changed during export.`; `OUTPUT_EXISTS`/`Output already exists.`; `WRITE_FAILED`/`Archive write failed.`; `PUBLISH_UNCERTAIN`/`Archive publication requires inspection.`; `INTERNAL_ERROR`/`Export failed.`. Do not print exception strings, traceback, transcript fragments, event text, Git stderr, user-supplied paths, or values, even on unexpected exceptions.

## Task 1: Commit the causal RED contract

**Files:** Create `tests/continuity/test_export_foundation_native_evidence.py`; do not create the exporter yet.

**Interfaces:** Black-box `sys.executable SCRIPT` with the exact CLI above; `unittest` fixtures create disposable Git repositories and copied evidence only. Fixture values must be independent of exporter constants. Later fault-injection tests load the script with `importlib.util.spec_from_file_location`, after the causal missing-script check has been recorded.

- [ ] Confirm the controller separately committed the existing fixture repair. Record current branch/HEAD and `git status --short`; read `AGENTS.md`, the live active-build result, and this plan. Never stage the fixture-repair file with exporter tests.
- [ ] Write a complete independent synthetic fixture with seven exact manifest argv records, fourteen small original transcripts, a canonical receipt with recomputed digests/counts, and a standalone importer summary. Use a real local temporary Git commit as candidate; label the module docstring that synthetic fixtures do not prove native execution. Preserve one CRLF transcript and a pretty/CRLF summary to exercise byte preservation. Make output parent a different private directory.
- [ ] Implement the following real subprocess test first. `make_fixture` returns `candidate`, `summary`, `manifest`, `receipt`, `repo`, and `output`; `run_export` constructs all six flags and captures stdout/stderr without `check=True`:

```python
def test_exports_exact_original_bytes(self):
    fixture = self.make_fixture()
    result = self.run_export(fixture)
    self.assertEqual(result.returncode, 0, result.stderr)
    with tarfile.open(fixture["output"], "r:") as archive:
        self.assertEqual(archive.getnames(), self.expected_member_names())
        for name, original in self.source_members(fixture):
            self.assertEqual(archive.extractfile(name).read(), original)
        self.assertEqual(len(archive.getmembers()), 18)
```

- [ ] Run `python3 -m unittest discover -s tests/continuity -p test_export_foundation_native_evidence.py -v`. Require causal RED: the subprocess cannot open the absent `scripts/export-foundation-native-evidence.py`, and the exit-zero assertion fails. A syntax/import error in the test is not accepted RED. Record actual command, exit status, failing assertion, and absent script. Fix the test harness if it fails for another reason; do not weaken the assertion.
- [ ] Add the acceptance cases below using `subTest` tables where the same assertion applies. Keep checks externally observable; use deterministic mocks for write races/faults, never sleeps or scheduler-dependent concurrent writes. For rejection, require exit 1, exact safe code/message, absent final output before publication, unchanged input bytes, and no exporter-created temporary leftovers.

| Test name / case family | Exact causal checks |
| --- | --- |
| `test_deterministic_bytes_and_metadata` | Export twice to different output names and different absolute input roots with identical source bytes, then compare whole tar bytes and SHA-256. Change source mtimes and permitted modes between runs; compare again. Validate 18 ordered names, USTAR magic, all fixed metadata, no PAX/compression, exact bytes, canonical inventory, mode 0600 and nlink 1. |
| `test_receipt_and_summary_binding` | Reject changed candidate, schema, platform, filesystem, status, missing/extra fields, reordered/duplicate suite IDs, changed selectors, zero/noninteger/bool counts, wrong totals, gate_summary mismatch, false flags, nonzero gate exit, non-null errors. Accept reordered summary object keys and unequal outbound/transcript manifest hashes. |
| `test_tampering_and_canonical_receipt` | Independently alter each of 14 transcript byte strings, transcript manifest whitespace, and each bound receipt digest without updating receipt: `HASH_MISMATCH`. Alter summary gate_summary only: `CONTRACT_MISMATCH`. Receipt whitespace, CRLF, extra LF, missing LF, float integers and canonicality deviations fail. |
| `test_duplicate_keys_and_json_limits` | Duplicate root/nested suite/gate_summary/event keys in every JSON input; reject invalid UTF-8, BOM, NaN, Infinity, surrogate escapes, trailing second object, depth 33, 100001 values, excessive integer token length. Test exact configured byte caps and cap+1, aggregate cap and cap+1 using patched small constants and separate checks of production constant values. |
| `test_exact_transcript_inventory` | Remove each canonical file, add `extra.list.txt`, `extra.run.txt`, uppercase suffix variants, suffix-shaped symlink/directory, or add a 4097th entry: reject. A harmless `notes.md` and non-transcript subdirectory are accepted and omitted. |
| `test_path_and_link_hazards` | Relative paths, `..`, root paths, wrong `.tar` suffix, dot basename, missing output parent, writable shared parent, output inside transcript directory, symlinked ancestor/input/output parent, dangling output link, hard-linked source, source group/other writes or special bits, duplicate source identity, FIFO/socket/directory source all fail without hanging/writing. Accept ordinary spaces and Unicode in paths. |
| `test_no_clobber_race` | Existing output is unchanged. Patch publication `os.link` so a competing file is created immediately before the real link; winner retains sentinel bytes, exporter gets OUTPUT_EXISTS, temporary disappears. Patch `os.replace` to raise AssertionError if called anywhere. |
| `test_write_failure_cleanup` | Inject failures in tar serialization, write, flush/file fsync, prepublication link, and temporary cleanup handling. No incomplete final archive appears before a successful link; source files are untouched. Directory-fsync failure after link returns PUBLISH_UNCERTAIN and output_created true while retaining a complete verifiable tar. Do not assert impossible cleanup when unlink itself is intentionally denied; require safe fixed error and retained exact temp identity instead. |
| `test_source_changes_are_detected` | At controlled checkpoints change content (including same-size with mtime restored), chmod, add hard link, truncate/grow, rename-and-replace a source with equal bytes, swap a source parent, or add an unexpected transcript after initial enumeration. Require SOURCE_CHANGED before publication and no archive. Use actual filesystem mutations triggered by a patched private check function. |
| `test_posix_only_before_writes` | Load module without executing main, patch its platform probe to Windows, and make every write/create/link callable fail if reached; main returns UNSUPPORTED_PLATFORM and no artifact. Run on actual POSIX for successful behavior. |
| `test_authority_claims_are_explicit` | Inventory contains exactly the five false authority booleans and exactly inputs_structurally_and_hash_consistent true. Neither inventory nor success report claims independently proven native execution, verified CI, owner approval, or release readiness. Original opaque summary remains byte-preserved, separate from exporter-generated claims. |
| `test_secret_safe_errors` | Put a unique synthetic canary in malformed JSON, transcript name/content, exception message, path, unknown CLI argument, and fake Git stderr. Exercise default and human output; canary, source paths, traceback and arbitrary exception text never appear in stdout/stderr. Unexpected exception gives INTERNAL_ERROR. |
| `test_candidate_and_no_network` | Reject abbreviated/uppercase/nonhex/missing/noncommit SHA and Git timeout; accept an existing commit older than HEAD. Assert the only child process is the fixed local Git inspection, with no shell, fetch, Cargo, or network client. |

- [ ] Commit tests only through the controller: `git add -- tests/continuity/test_export_foundation_native_evidence.py`, then `git commit -m "test: specify native evidence export contract"`. Record the intentional RED; this commit is not a completed implementation checkpoint.

## Task 2: Implement minimal GREEN exporter

**Files:** Create `scripts/export-foundation-native-evidence.py`; modify only its new test file for concrete fixture defects, preserving acceptance assertions.

**Interfaces:** Define `ExportError(code: str)`; `main(argv=None) -> int`; `is_posix_host() -> bool`; `strict_json(data: bytes) -> object`; `canonical_json(value: object) -> bytes`; `export(args) -> dict`. Private helpers `open_source`, `recheck_sources`, `validate_contract`, `build_archive`, and `publish_archive` own descriptor acquisition, mutation checking, typed schema checks, tar bytes, and no-replace publication respectively. Importing the module performs no I/O; guard execution with `if __name__ == "__main__": sys.exit(main())`.

- [ ] Add standard-library imports and the closed constants: suite matrix, field sets, size/count bounds, tar names, safe errors, schemas, and five false authority flags. Implement strict scalar checks with `type(value) is int`/`type(value) is bool`, rather than Python equality alone. Restrict receipt scalar values before using its JCS-equivalent serializer:

```python
def canonical_json(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")

def reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ExportError("INVALID_JSON")
        result[key] = value
    return result
```

- [ ] Implement safe argument parsing and platform-first validation, local Git candidate lookup, descriptor-relative directory opening, file snapshots and bounded reads, and strict JSON validation. Put cap checks before reads/parsing, use depth-aware lexical scanning that ignores brackets inside strings, and reject duplicate keys via `object_pairs_hook=reject_duplicate_pairs`. Retain all input descriptors until final prepublication rechecks complete.
- [ ] Implement the exact manifest/receipt/summary checks in this plan. Hash original captured bytes, compare typed gate_summary semantics, and preserve opaque summary/event data. Count receipt assertions without reimplementing native transcript test semantics. Build fixed-name source member records and canonical inventory.
- [ ] Implement explicit tar entries and full-archive SHA-256/byte count. Do not incorporate temporary file metadata:

```python
def add_member(archive, name, data):
    item = tarfile.TarInfo(name)
    item.type = tarfile.REGTYPE
    item.size = len(data)
    item.mode = 0o600
    item.mtime = item.uid = item.gid = 0
    item.uname = item.gname = item.linkname = ""
    archive.addfile(item, io.BytesIO(data))
```

- [ ] Implement the prescribed temporary/fsync/hard-link flow, parent identity checks, prepublication full source rechecks, scoped temporary cleanup, and explicit post-link uncertainty result. Close descriptors in finally blocks. Hash the actual complete temporary archive before publication; never hash only its payload stream and label that the archive hash.
- [ ] Implement default JSON and human rendering solely from trusted result fields and the fixed error map. Catch expected OS/parser/timeout exceptions at their boundary and map to stable codes; the outer catch returns INTERNAL_ERROR without exception interpolation. Ensure stdout has exactly one result and no diagnostic leakage.
- [ ] Run the focused exporter test file on Python 3.14 and 3.9; fix the implementation until both pass. Keep the first successful GREEN results distinct from the earlier causal RED.
- [ ] The controller stages only `scripts/export-foundation-native-evidence.py` and any justified new-test correction, then commits `feat: export deterministic native evidence return archives`. Keep the Python source tracked mode 100644, consistent with invocation through python3; the generated archive is mode 0600.

## Task 3: Operator documentation and scoped verification checkpoint

**Files:** Create `docs/operations/foundation-native-evidence.md`; modify `README.md` by one operator-guide link and `SKILLS.md` by one workflow row. Update this plan's checkboxes with actual completion evidence only; the controller records volatile results in its existing task checkpoint.

**Interfaces:** The guide documents the exact CLI, 18-member contract, errors and authority limits already implemented. It must not introduce a new export format, archive importer, release-status input, handoff automation, or owner decision workflow.

- [ ] Write the guide with prerequisites: complete native Windows x64/NTFS importer run first, save its final standalone summary JSON separately from its event stream, preserve canonical receipt stdout as a separate file, copy summary/receipt/transcript manifest and all 14 transcripts back to a private POSIX directory, and retain the exact candidate commit locally. Never present synthetic test fixture files as returned native evidence.
- [ ] Include this command shape, explaining that all paths are explicit absolute operator-supplied locations and output parent already exists:

```sh
python3 scripts/export-foundation-native-evidence.py \
  --repo /absolute/path/to/Heleos-spark \
  --candidate 0123456789abcdef0123456789abcdef01234567 \
  --summary /absolute/path/to/returned/importer-summary.json \
  --manifest /absolute/path/to/returned/transcripts/manifest.json \
  --receipt /absolute/path/to/returned/receipt.json \
  --output /absolute/path/to/private-return/foundation-native-evidence.tar
```

- [ ] Explain bytes versus semantics, outbound versus transcript manifest hashes, deterministic archive order/metadata, size/mode/path bounds, no-clobber retry behavior, secret-safe errors, and PUBLISH_UNCERTAIN retention. Show inspection using `tar -tf /absolute/path/to/private-return/foundation-native-evidence.tar`; do not recommend extraction as a validation shortcut. State that the archive contains original logs/paths and should remain within the approved evidence-handling scope.
- [ ] State explicitly: the packager validates structural and hash consistency only; it independently authenticates neither the native host nor execution; it grants no CI authority, owner approval, or release approval. Receipt counts are bound assertions, and native transcript semantic verification belongs to the existing Rust verifier. Explain that release acceptance still requires the separate governed evidence and owner process.
- [ ] Add the README link adjacent to existing Foundation release-routing guidance and one SKILLS workflow row labelled `Native evidence return packaging`, linking this guide and the CLI help. This discoverability is justified because an operator now has a new manual return step; do not duplicate volatile gate statuses or test results there.
- [ ] Run the exact verification commands below from the assigned worktree, recording interpreter versions, actual exit codes and counts. These are local compatibility/regression results, never Windows execution evidence:

```sh
python3 --version
/usr/bin/python3 --version
python3 -m unittest discover -s tests/continuity -p test_export_foundation_native_evidence.py -v
/usr/bin/python3 -m unittest discover -s tests/continuity -p test_export_foundation_native_evidence.py -v
python3 -m unittest discover -s tests/continuity -p 'test_*.py' -v
/usr/bin/python3 -m unittest discover -s tests/continuity -p 'test_*.py' -v
cargo +1.96.1 test --frozen --offline -p heleos-verification --test native_suite_receipt
python3 -m unittest discover -s tests/research -p 'test_*.py' -v
/usr/bin/python3 -m unittest discover -s tests/research -p 'test_*.py' -v
python3 -c 'import ast,pathlib; [ast.parse(pathlib.Path(p).read_text()) for p in ("scripts/export-foundation-native-evidence.py", "tests/continuity/test_export_foundation_native_evidence.py")]'
/usr/bin/python3 -c 'import ast,pathlib; [ast.parse(pathlib.Path(p).read_text()) for p in ("scripts/export-foundation-native-evidence.py", "tests/continuity/test_export_foundation_native_evidence.py")]'
python3 scripts/export-foundation-native-evidence.py --help
/usr/bin/python3 scripts/export-foundation-native-evidence.py --help
git diff --check
git diff --stat
git diff --summary
git ls-files --stage -- scripts/export-foundation-native-evidence.py tests/continuity/test_export_foundation_native_evidence.py docs/operations/foundation-native-evidence.md README.md SKILLS.md
git status --short
```

- [ ] Treat the exporter acceptance tests' strict parsing of stdout and inventory as the JSON validation check; they must assert canonical LF bytes, exact keys/types, member payload hashes, and authority flags. Inspect their successful output and include their test names in the checkpoint. Run no additional broad suite after these pass unless changes/failures introduce a specific unresolved concern.
- [ ] Inspect exact scoped diff and file modes, ensure no generated evidence/archive is tracked, and record candidate implementation commit plus source-file SHA-256 identities in the existing controller checkpoint. The controller commits guide/discoverability/plan progress separately as `docs: document native evidence return packaging`; no new checkpoint system is created.
- [ ] Handoff distinguishes implementation complete, local checks passed, and independent native/release gates pending; record the terminal process state and next concrete action: use the CLI on genuinely returned native inputs when those are available. Do not manufacture those inputs or start another native run merely to demonstrate this packager.

## Plan self-check

- [ ] Confirm every contract above has an acceptance test; causal RED is missing-script failure, followed by real GREEN on both required interpreters.
- [ ] Confirm existing fixture repair, tests, implementation, and documentation are separate commits; controller owns all commits.
- [ ] Confirm no unbounded reads/enumeration, unchecked manifest paths, source rewrites, replace-based publication, network calls, or positive authority claims were introduced.
- [ ] Confirm documentation and tests distinguish the importer's transfer manifest hash from the receipt's transcript manifest hash, and use mode `native_suites` with native_host true.
- [ ] Confirm archive publication failure semantics accurately distinguish no output before link from uncertain durability after link.
