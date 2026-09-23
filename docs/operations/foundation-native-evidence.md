# Foundation native evidence return packaging

This guide defines the operator workflow for
`scripts/export-foundation-native-evidence.py`: package copied Windows evidence
on a POSIX controller as one deterministic, uncompressed USTAR archive. The
exporter validates structural and hash consistency only. It independently
authenticates neither the native host nor execution, and grants no CI authority,
owner approval, or release approval. This workflow description is not an
implementation verification result or evidence of a completed native run.

## Prerequisites and returned inputs

First complete the trusted `scripts/import-foundation-native-candidate.ps1`
handoff on native Windows x64 on a local fixed NTFS volume, using the separately
pinned candidate and transfer manifest digest. It runs the complete committed
`scripts/verify-supply-chain.ps1` gate; a transfer or preflight result is
insufficient. Follow the handoff's offline tool prerequisites and retain the
native checkout and console output, including failed attempts.

For a completed run, preserve and copy back these 17 original source files to
a private POSIX location:

1. The final standalone importer summary JSON object, saved separately from
   the preceding JSON event stream. It must use schema
   `heleos.foundation-windows-native-import/v1`, status `PASS`, mode
   `native_suites`, Boolean `native_host`, `native_evidence`, and
   `destination_created` all true, the exact candidate commit, integer
   `gate_exit_code` zero, and null `error_code` and `error`.
2. The separate canonical receipt emitted by the existing Rust verifier. The
   native gate saves `native-suite-receipt.json` in its retained
   `target/supply-chain.<run-id>` directory. Preserve that file or canonical
   receipt stdout with exactly one final LF; do not reserialize `gate_summary`.
3. The final `native-suite-manifest.json` from that evidence directory, plus all
   14 canonical transcript files beside it. Keep the copied manifest and
   transcripts together. Summary and receipt may be elsewhere. The example
   renames the copied manifest to `manifest.json` without changing its bytes.

Retain the candidate's full 40-character lowercase hexadecimal commit locally;
it need not equal controller HEAD. The exporter performs bounded read-only Git
inspection, never fetching or executing the candidate. Synthetic test fixtures
are not returned native evidence.

## Command

Before export, complete the [native return binding check](#native-return-binding-before-packaging)
against the original outbound handoff and returned summary.

The CLI is exactly:

```text
python3 scripts/export-foundation-native-evidence.py --repo PATH --candidate FULL_SHA --summary PATH --manifest PATH --receipt PATH --output PATH [--human]
```

Run from the checkout containing the exporter, using Python 3.9–3.14 on POSIX:

```sh
python3 scripts/export-foundation-native-evidence.py \
  --repo /absolute/path/to/Heleos-spark \
  --candidate 0123456789abcdef0123456789abcdef01234567 \
  --summary /absolute/path/to/returned/importer-summary.json \
  --manifest /absolute/path/to/returned/transcripts/manifest.json \
  --receipt /absolute/path/to/returned/receipt.json \
  --output /absolute/path/to/private-return/foundation-native-evidence.tar
```

Every path and the candidate above is illustrative: supply actual absolute
locations and the returned run's exact commit. The output parent must already
exist. Standard `--help` describes the arguments; `--human` selects concise
operator output. Non-POSIX hosts, including Windows, are rejected before writes.
This command makes no network calls, invokes no Cargo command, changes no
workflow, and does not publish remotely.

## Byte preservation and binding checks

All 17 source byte strings are retained exactly, including original whitespace,
CRLF, and opaque Windows paths and event values in the summary. Only
`inventory.json` is newly serialized. The exporter never follows or executes
Windows paths from the summary and never opens unchecked manifest paths.

Strict UTF-8 JSON parsing rejects duplicate keys, BOM, invalid Unicode,
non-finite constants, and trailing data. Closed schemas use typed comparisons:
booleans do not substitute for integers. Summary `gate_summary` must equal the
receipt semantically, though key order and whitespace may differ. Receipt bytes
must equal the existing restricted JCS representation plus one LF; pretty JSON,
CRLF endings, missing LF, float spellings, and unknown fields are rejected.

The two manifest digests have distinct meanings:

| Field | What it binds |
| --- | --- |
| Importer summary root `manifest_sha256` | The outbound candidate transfer manifest. |
| Receipt `manifest_sha256`, also in summary `gate_summary.manifest_sha256` | The original bytes of the returned transcript manifest supplied by `--manifest`. |

These hashes need not equal each other. The exporter recomputes the transcript
manifest digest and each transcript digest. It checks the transfer manifest
digest and `bundle_sha256` string formats, but cannot validate their source
bytes: the outbound manifest and bundle are not exporter inputs.

### Native return binding before packaging

Before packaging returned evidence, run the native return binding verifier
against the original outbound handoff and the standalone final importer summary.
PASS binds the candidate and the actual outbound manifest and bundle bytes.
The embedded receipt manifest digest refers to the returned transcript manifest.
This command does not inspect transcripts or authenticate execution. The
Foundation importer always runs the full committed supply-chain gate,
`scripts/verify-supply-chain.ps1`, including all seven native suites; there is
no preflight-only Foundation mode. Worker importer summaries cannot substitute.

Run from the checkout containing the verifier with Python 3.9–3.14 on POSIX:

```text
python3 scripts/verify-foundation-native-return-binding.py --repo PATH --candidate FULL_SHA --handoff PATH --summary PATH [--human]
```

Supply actual absolute canonical paths and the full lowercase 40-character
candidate commit, which must exist locally but need not equal current main HEAD.
`--repo` resolves the unique registered visible-main checkout. `--handoff` is
the original ignored, untracked outbound transfer directory. The verifier
checks its exact manifest, bundle, checksum lines, advertised branch, and local
bundle validity; both working and handoff importer bytes must match the importer
committed at observed main HEAD. `--summary` is one final summary object, not
the event stream or an archive. Its root `manifest_sha256` must hash the exact
outbound `candidate/candidate.json` bytes, and root `bundle_sha256` must hash
the actual verified bundle. Never substitute the nested
`gate_summary.manifest_sha256` for the outbound manifest digest.

Paths may contain no traversal, aliases, or symlink/reparse-point components.
The summary and inspected handoff sources must be regular files with one hard
link and no group/world write bits (`mode & 0o022 == 0`). Read-only sources are
allowed; execute bits alone do not cause rejection. Inherited `GIT_*` overrides,
including `GIT_OPTIONAL_LOCKS`, are rejected before inspection. Git runs with a
controlled environment, a 10-second limit per invocation, and at most 1 MiB per
output pipe. Source bytes, file/parent identities, importer, main HEAD, and
registration are rechecked before PASS. Observed drift fails closed; these
checks do not lock the filesystem or provide a transactional snapshot.

Binding input limits are 64 KiB per handoff text file, 16 MiB for the summary,
and 512 MiB for the bundle, streamed in chunks of at most 1 MiB. Strict JSON
rejects BOM, invalid UTF-8, duplicate keys, trailing data, and nonfinite numbers.
Limits are 32 nesting levels, 100,000 values, 16 characters per integer token,
and integers within ±9,007,199,254,740,991. Closed summary and receipt schemas
require the successful Foundation native-suites contract described above.

Default stdout is exactly one deterministic JSON object with sorted keys and
one LF, schema `heleos.foundation-native-return-binding-result/v1`. Its fields
are `schema`, `status`, `candidate_sha`, `outbound_manifest_sha256`,
`bundle_sha256`, `trusted_importer_sha256`, `transcript_manifest_sha256`,
`summary_sha256`, `authority`, `error_code`, and `error`. The summary digest
hashes its exact bytes; the transcript-manifest digest is a validated report
field, not a locally verified transcript hash. PASS has null errors. Every
failure has status `FAIL`, all six identity fields null, and a fixed safe error
code/message. `--human` emits one concise PASS or FAIL line with the authority
boundary. Exit is 0 for binding PASS or help, and 1 for every failure; help
performs no inspection. Both modes keep stderr empty and omit paths, events,
logs, environment contents, raw Git output, and exception text.

All five `authority` fields are always literal false:
`independent_native_authentication`, `native_execution_independently_proven`,
`ci_authority`, `owner_approval`, and `release_approval`. Binding is read-only:
it performs no extraction, network access, Cargo execution, fetch, or evidence
writes. It complements packaging without changing exporter semantics or the
acceptance ledger. A binding PASS is not native execution or release acceptance.

### Returned transcript contract

The transcript manifest uses `heleos.native-suite-transcripts/v1`, the exact
candidate, `windows-x86_64`, `NTFS`, and these ordered suites. Each `run_argv`
is `cargo +1.96.1 test --frozen` plus the selectors; `list_argv` appends `--`,
`--list`. Both exit codes are integer zero; paths are `ID.list.txt`/`ID.run.txt`.

| Suite ID | Selectors |
| --- | --- |
| `core-backup-restore` | `-p heleos-core --test backup_restore` |
| `core-store` | `-p heleos-core --lib store::tests` |
| `core-backup` | `-p heleos-core --lib backup::tests` |
| `platform-fs` | `-p heleos-platform-fs --lib` |
| `cli-unit` | `-p heleos-cli --bin heleos` |
| `cli-integration` | `-p heleos-cli --test cli` |
| `workspace-all` | `--workspace --all-targets --all-features` |

Receipt schema is `heleos.native-suite-receipt/v1`, status `pass`, with matching
candidate/platform/filesystem and integer `suite_count` 7. Seven ordered records
bind the transcript digests; listed/passed counts must be equal positive
integers at most 1,000,000 per suite, and totals must match the sums. Counts are
bound assertions. Transcript semantic verification remains the existing Rust
verifier's responsibility; the exporter does not reperform it.

## Paths, modes, and limits

All path arguments must be absolute and must not contain NUL or `..` components,
be root-only, or have symlink components. Ordinary spaces and Unicode components
are supported. The repository must be an existing real directory. Inputs must
be distinct regular files with one hard link, no special permission bits, and
no group/other write bits. Ordinary `0600`, `0640`, and `0644` source modes are
allowed and are not changed. Directories, FIFOs, sockets, devices, symlinks, and
hard-linked sources are rejected before reading.

The output must differ from every input and must be outside the transcript
directory. Its basename must end in lowercase `.tar` and must not begin with
`.`. Its existing real parent must have no group/other write permission.
Existing output entries, including dangling symlinks, are rejected. Parent
directories are never created by the exporter.

| Input bound | Maximum |
| --- | --- |
| Summary JSON | 16 MiB |
| Transcript manifest JSON | 1 MiB |
| Receipt JSON | 1 MiB |
| Each transcript | 64 MiB |
| Combined 17 source payloads | 512 MiB |
| JSON nesting depth | 32 |
| Parsed values per JSON document | 100,000 |
| Integer token length | 16 characters, within the JCS-safe integer range |
| Direct entries in transcript directory | 4,096 |

Exactly the 14 canonical transcript basenames are required. Any extra direct
entry ending in `.list.txt` or `.run.txt`, case-insensitively, is rejected,
including directories and symlinks. Unrelated entries are tolerated without
reading their contents; enumeration does not recurse.

Pinned source, pathname, and parent identities, reread hashes, and transcript
inventory are rechecked after archive writing and immediately before
publication. Detected mutation fails closed. A hostile writer with the same
user identity can still change files after the final check.

## Exact archive contract

The result is one uncompressed POSIX USTAR `.tar` with exactly 18 regular-file
members in this exact order:

```text
importer-summary.json
manifest.json
receipt.json
transcripts/core-backup-restore.list.txt
transcripts/core-backup-restore.run.txt
transcripts/core-store.list.txt
transcripts/core-store.run.txt
transcripts/core-backup.list.txt
transcripts/core-backup.run.txt
transcripts/platform-fs.list.txt
transcripts/platform-fs.run.txt
transcripts/cli-unit.list.txt
transcripts/cli-unit.run.txt
transcripts/cli-integration.list.txt
transcripts/cli-integration.run.txt
transcripts/workspace-all.list.txt
transcripts/workspace-all.run.txt
inventory.json
```

There are no directory members, PAX records, compression, absolute member names,
or traversal names. Each member has mode `0600`, mtime 0, uid/gid 0, empty
uname/gname and linkname, regular-file type, and its exact payload size. Source
modes and timestamps do not affect archive metadata. Identical source bytes
and candidate produce identical complete archive bytes regardless of the
controller's input/output locations or temporary filenames.

Inventory schema `heleos.foundation-native-evidence-export/v1` has exactly
`schema`, `candidate_sha`, `platform`, `filesystem`, `members`, `claims`, and
`authority`. It binds `windows-x86_64`/`NTFS` and lists 17 ordered source members
with exactly `name`, lowercase SHA-256 `sha256`, and integer byte `size`;
it excludes itself. Serialization uses sorted keys, compact UTF-8 JSON and one
LF. It adds no paths, host identifiers, timestamps, branch, controller HEAD,
source identities, or temporary names. Original payloads may contain host paths.

The sole `claims` field is `inputs_structurally_and_hash_consistent: true`.
The five `authority` fields are all false: `independent_native_authentication`,
`native_execution_independently_proven`, `ci_authority`, `owner_approval`, and
`release_approval`.

## Publication, errors, and inspection

After validation, the exporter creates one exclusive random temporary file in
the output parent and fixes its mode to `0600`. It writes captured bytes,
closes the tar writer, flushes and fsyncs the file, rechecks the sources and
output parent, then publishes with a same-directory atomic hard link that
cannot replace an existing name. It removes only its own temporary entry and
fsyncs the parent. The successful final archive is a regular `0600` file with
one hard link. There is no replacement or copy-to-final fallback.

An existing or raced output returns `OUTPUT_EXISTS` and preserves the winning
file byte-for-byte. Prepublication failures remove only this invocation's
temporary when safe; they never unlink an existing or raced final output.
If temporary cleanup itself is denied, retained temporary evidence is not
grounds for broad deletion. Choose a fresh output name for an intentional
retry after resolving the reported condition; do not overwrite prior evidence.

After a successful link, failure of parent fsync or final validation returns
`PUBLISH_UNCERTAIN` with `output_created: true`. The published archive is
retained, and any remaining exporter-owned temporary is cleaned only when
safe. Inspect that exact output before retrying. Uncertain publication does
not mean that no file was created and does not authorize rollback or overwrite.

Default stdout is one canonical JSON object plus LF with schema
`heleos.foundation-native-evidence-export-result/v1` and fields `schema`,
`status`, `candidate_sha`, `archive_sha256`, `archive_bytes`, `member_count`,
`output_created`, `error_code`, and `error`. Success is `PASS`, with the whole
archive's digest/size, count 18, output_created true, and null errors. Failure
is `FAIL`, with null candidate/hash/bytes/count and output_created false except
post-link uncertainty. Exit is 0 for success/help and 1 for failure.

JSON and `--human` errors use fixed safe codes/messages, including
`OUTPUT_EXISTS` / `Output already exists.` and `PUBLISH_UNCERTAIN` /
`Archive publication requires inspection.` Other codes are
`UNSUPPORTED_PLATFORM`, `INVALID_ARGUMENT`, `INVALID_PATH`, `INVALID_CANDIDATE`,
`UNSAFE_SOURCE`, `INPUT_LIMIT`, `INVALID_JSON`, `CONTRACT_MISMATCH`,
`HASH_MISMATCH`, `SOURCE_CHANGED`, `WRITE_FAILED`, and `INTERNAL_ERROR`.
Errors never echo exception text, tracebacks, source fragments, event values,
Git stderr, or supplied paths. Diagnose privately using the fixed code.

Inspect member names without extraction:

```sh
tar -tf /absolute/path/to/private-return/foundation-native-evidence.tar
```

Listing names is an inspection aid, not validation of payload hashes or native
execution. Keep original files and archives within the approved private
evidence-handling scope: the archive preserves logs and paths rather than
redacting them. Do not track generated evidence in Git or transfer it to an
unapproved service. Release acceptance still requires the separate governed
evidence and owner process described by the
[Foundation release status guide](foundation-release-status.md); an export
result supplies none of those approvals.
