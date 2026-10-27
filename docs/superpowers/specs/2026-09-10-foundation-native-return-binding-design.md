# Foundation native-return binding design

Date: 2026-09-10. Status: approved design for Foundation 0.1 release enablement.

## Decision and scope

Add a standalone, read-only Python 3.9–3.14 standard-library CLI:

```text
scripts/verify-foundation-native-return-binding.py --repo PATH --candidate FULL_SHA --handoff PATH --summary PATH [--human]
```

The verifier establishes whether a returned Foundation importer summary names the exact candidate and actual outbound handoff bytes inspected locally. A PASS establishes this binding only. It does not authenticate the returned report, prove native execution, or approve a release.

Changing `foundation-release-status.py` would couple this narrow return check to App, workflow, ledger, and acceptance checks. That alternative is rejected. Changing the completed native-evidence exporter would mix outbound-handoff verification with packaging and require its established interface to change. That alternative is also rejected. Preserve both tools and test the new verifier against the existing `transfer()` contract.

The Foundation importer always runs the full `scripts/verify-supply-chain.ps1` gate, including all seven native suites. Its summary uses `heleos.foundation-windows-native-import/v1` and mode `native_suites`. It is distinct from the worker importer; worker receipts and reduced execution modes cannot satisfy this contract.

## Data flow

```text
--repo -> unique registered visible-main checkout -> committed trusted importer
--candidate -> full local commit identity
--handoff -> ignored, untracked Foundation transfer -> verified manifest and bundle hashes
--summary -> closed Foundation importer summary -> candidate and outbound hashes
all identities match + stable final observations -> deterministic binding PASS
```

Resolve the repository and visible-main checkout using the same semantics as release-status. Snapshot main HEAD, repository identity, registration, and relevant file identities before inspection; recheck them before reporting success. The candidate must be a complete lowercase 40-character SHA naming a locally present commit. It need not equal current main HEAD. The committed trusted importer comes from the observed main HEAD, as in `transfer()`.

## Outbound handoff contract

Inspect the actual directory supplied by `--handoff`; hashes copied into a return report cannot substitute for these bytes. Apply the existing `foundation-release-status.py:transfer()` rules:

- Require a canonical existing directory, ignored by visible main and containing no tracked transfer files. Reject traversal, symlink or reparse-point components, aliases, and nonregular evidence files.
- Read `candidate/candidate.json` as strict UTF-8 JSON with exactly `schema`, `branch`, `commit`, `bundle`, `bundle_sha256`, `bundle_bytes`, `native_gate`, `required_filesystem`, and `native_gate_status`.
- Require schema `heleos.foundation-windows-native-candidate/v1`, the requested candidate, branch `release/foundation-0.1-native-<candidate>`, bundle `heleos-spark-<candidate>.bundle`, a positive integer byte count, lowercase SHA-256, gate `scripts/verify-supply-chain.ps1`, filesystem `NTFS`, and gate status `pending`.
- Hash the manifest's exact bytes, README, bundle, and handoff importer. Require the bundle length and hash to match the manifest. Require both the main working importer bytes and the handoff importer bytes to match the regular importer blob committed at observed main HEAD.
- Require `SHA256SUMS.txt` to equal the exact four ordered checksum lines for `candidate/candidate.json`, `candidate/README.md`, the candidate bundle, and `scripts/import-foundation-native-candidate.ps1`, including spacing and final newlines.
- Require `git bundle list-heads` to advertise exactly the candidate branch and SHA; require local `git bundle verify` to pass. Recheck all consumed file bytes or streaming hashes and identities before success.

Dynamically import the adjacent `foundation-release-status.py` with `sys.dont_write_bytecode = True` set before import. Reuse its `main_checkout()`, `commit_exists()`, and `transfer()` functions to preserve the established contract. Install a verifier-owned bounded Git adapter in the imported state helper before invoking these functions: `verify-repo-state.py` otherwise captures Git output without the required bounds. Pre-open, pin, and bound the summary and every consumed handoff source before transfer inspection; retain their identities through the operation and perform final rechecks. Contract-parity tests prevent drift while the wrapper adds resource limits and observed-state checks. No existing helper file is changed.

## Returned summary contract and binding

`--summary` identifies one regular JSON file containing one final summary object, not an importer event stream or an archive. Reject duplicate keys at every depth, invalid UTF-8, nonfinite numbers, trailing values, unknown top-level fields, and missing fields. Its exact fields are:

```text
schema status mode native_host native_evidence commit destination
destination_created manifest_sha256 bundle_sha256 gate_summary
gate_exit_code gate_log error_code error events
```

Require the Foundation import schema, `status=PASS`, `mode=native_suites`, the requested commit, literal booleans true for `native_host`, `native_evidence`, and `destination_created`, integer zero for `gate_exit_code`, null errors, nonempty string destination and gate log, and an array of event objects. Those strings and events are untrusted metadata: do not print them, follow their paths, execute their content, or infer authority from them.

Require a closed `gate_summary` with exactly `schema manifest_sha256 suites status candidate_sha platform filesystem suite_count total_listed total_passed`. Require schema `heleos.native-suite-receipt/v1`, status `pass`, matching candidate, platform `windows-x86_64`, filesystem `NTFS`, integer suite count seven, and seven suite entries in the canonical Foundation matrix order used by the exporter. Each entry has exactly `id list_sha256 run_sha256 listed passed`, lowercase SHA-256 values, and equal integer listed/passed counts from 1 through 1,000,000. Total counts must be integers equal to the summed suite counts; booleans are never integers for validation.

The binding comparisons are explicit:

| Returned field | Required local identity |
| --- | --- |
| `commit` and `gate_summary.candidate_sha` | Requested candidate and outbound manifest commit |
| Top-level `manifest_sha256` | SHA-256 of exact outbound `candidate/candidate.json` bytes |
| Top-level `bundle_sha256` | SHA-256 of the actual verified outbound bundle |
| `gate_summary.manifest_sha256` | Well-formed transcript-manifest digest, reported separately |

The nested receipt manifest digest identifies `native-suite-manifest.json`, not the outbound candidate manifest. Never compare those two different manifest identities for equality or use one as a substitute for the other. This CLI does not receive transcript files and cannot verify the nested transcript digest or individual transcript hashes against source bytes; the exporter and native-suite verifier retain that responsibility.

## Resource safety and deterministic results

Bound metadata reads at 64 KiB each, summary reads at 16 MiB, bundle streaming at 512 MiB, JSON nesting at 32 levels, JSON values at 100,000, integer tokens at 16 characters, and integer values to the inclusive safe range -9,007,199,254,740,991 through 9,007,199,254,740,991. Bound each Git invocation at 10 seconds and 1 MiB per output pipe. Enforce bounds during reading, JSON processing, and pipe draining, not after an unbounded capture. Reject oversized declared bundles before hashing; read at most the limit plus one byte. Hash bundles incrementally in 1 MiB chunks.

Validate paths before opening, use no-follow and nonblocking flags where available, compare descriptor identity with lstat identity, and verify regular-file type and stable size/metadata before and after reads. Repeat relevant reads/hashes and repository observations at the end. These checks detect observed drift; they do not provide filesystem locking or a transactional snapshot against an adversarial concurrent writer.

Run Git with argument arrays, no shell, sanitized inherited Git configuration, disabled optional locks, lazy fetch, replacement objects and prompting, isolated global/system configuration, and disabled hooks and external protocols. Only repository discovery, read-only identity/tracking queries, committed-blob reads, and local bundle inspection are allowed. Never echo Git output or subprocess exceptions.

Emit exactly one deterministic JSON object to stdout by default, with sorted keys and a final newline. `--human` emits a fixed-order plain-text rendering of the same validated result. Neither mode emits timestamps, local paths, logs, events, environment contents, or raw errors. Both success and failure have exactly these result keys: `schema`, `status`, `candidate_sha`, `outbound_manifest_sha256`, `bundle_sha256`, `trusted_importer_sha256`, `transcript_manifest_sha256`, `summary_sha256`, `authority`, `error_code`, and `error`. The schema is `heleos.foundation-native-return-binding-result/v1`. Success has status `PASS`, the six verified or explicitly distinguished identity values, and null `error_code` and `error`; `summary_sha256` hashes the exact returned summary bytes. The transcript-manifest digest remains a validated report field, not a locally verified transcript hash.

The `authority` object has exactly five claims, all literal false on every result: `independent_native_authentication`, `native_execution_independently_proven`, `ci_authority`, `owner_approval`, and `release_approval`. Input claims of native evidence do not change these output authority fields. Use exit zero only for a completed binding PASS. Every argument, input, mismatch, drift, timeout, or internal failure exits one with status `FAIL`, all six identity fields null, the same false authority object, and a fixed allowlisted error code/message. Argument parsing must obey this stdout-only protocol rather than leaking paths through default parser diagnostics. Failure priority follows the documented validation order and is stable across runs.

## Verification and acceptance

Use standard-library subprocess tests with small real local Git repositories and real bundles. Cover successful binding from a registered worktree, deterministic JSON and human output, an older locally present candidate, and preserved repository/file state. Test candidate, manifest, and bundle mismatches independently, including a validly rebuilt handoff for another candidate.

Exercise malformed/duplicate/unknown JSON fields, boolean integers, absent fields, worker summaries, failed or partial imports, invalid receipt matrix/counts/totals, wrong platform, and summary event-stream input. Demonstrate that distinct outbound and transcript-manifest hashes pass, while replacing the top-level outbound hash with the nested transcript hash fails.

Cover tracked or nonignored handoffs, incorrect checksum order/bytes, modified trusted importer, wrong bundle heads, invalid bundles, missing commits, symlinks/reparse points where supported, directories/FIFOs, resource limits, bounded Git failures, and controlled file/main drift. Use secret-like sentinels in hostile metadata and command failures to prove stdout/stderr never reveal them. Check no network, writes, bytecode cache, Cargo execution, Git fetch, hooks, or child-process escape occurs. Run meaningful compatibility checks on available Python versions; report unavailable interpreters honestly.

Acceptance is the standalone implementation plus passing targeted tests and documented observed limits. No ledger, release-status, exporter, workflow, importer, or release-authority changes are part of this implementation. No extraction, network access, native gate execution, release acceptance, or native/CI evidence is produced by this verifier.
