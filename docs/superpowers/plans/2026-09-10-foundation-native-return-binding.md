# Foundation Native Return Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only controller command that binds a strictly validated returned Foundation importer summary to the candidate and actual outbound handoff bytes.

**Architecture:** Import the adjacent `foundation-release-status.py` with bytecode disabled and reuse its `main_checkout`, `commit_exists` and `transfer` functions without changing their validation semantics. Override its `state.git` and `state.git_text` with a verifier-owned bounded, sanitized adapter; pre-open, pin and bound the summary and actual handoff sources, route helper reads/hashes through those descriptors, and final-recheck them so the helper's unbounded defaults are never used. Strictly validate the returned summary and embedded receipt, compare three independent bindings, and emit one deterministic result with all authority claims false; drift observation does not claim a filesystem lock or native authentication.

**Tech Stack:** Python 3.9–3.14 standard library, Git inspection, unittest, POSIX controller; native Windows/NTFS remains the importer execution environment.

## Global Constraints

- CLI: `python3 scripts/verify-foundation-native-return-binding.py --repo PATH --candidate FULL_SHA --handoff PATH --summary PATH [--human]`.
- No writes, extraction, network, Cargo, fetch, ledger updates, or exporter changes; disable Python bytecode before adjacent imports.
- Foundation importer always runs the full committed `scripts/verify-supply-chain.ps1` gate. There is no preflight-only Foundation mode.
- Require the existing Foundation transfer contract, including registered main checkout, ignored/untracked handoff, exact importer bytes, checksums, bundle verification and branch advertisement.
- PASS means consistent local binding only. It grants neither independent native authentication, proof of execution, CI authority, owner approval, nor release approval.
- Actual outbound `candidate/candidate.json` SHA256 binds summary root `manifest_sha256`; embedded `gate_summary.manifest_sha256` describes the returned transcript manifest and must never be compared with the outbound digest.
- Only create the verifier and its test file; modify operations documentation, README.md, SKILLS.md, and the final `CURRENT_STATUS.md` checkpoint. Preserve unrelated changes; stage exact named files.

---

## Locked Interface and Data Contract

Paths are absolute canonical paths with no traversal, symlink/reparse components or aliases. Repository selection still resolves its registered main via the existing helper. Candidate is exactly 40 lowercase hexadecimal characters, present locally; it need not equal today's main HEAD. Reject inherited `GIT_*` overrides before inspection (including otherwise benign overrides), then use the existing helper's controlled Git environment. Reject unknown, duplicate or missing arguments and values; `--human` occurs at most once. Help is fixed text, exits zero and performs no inspection. All other diagnostics go to stdout, never stderr; argparse must not leak input or usage errors.

Result has exactly these keys in both success and failure:

```python
{
    "schema": "heleos.foundation-native-return-binding-result/v1",
    "status": "PASS",  # FAIL on any error
    "candidate_sha": "a" * 40,  # null on failure
    "outbound_manifest_sha256": "b" * 64,  # null on failure
    "bundle_sha256": "c" * 64,  # null on failure
    "summary_sha256": "d" * 64,  # null on failure
    "trusted_importer_sha256": "e" * 64,  # null on failure
    "transcript_manifest_sha256": "f" * 64,  # null on failure
    "authority": {
        "independent_native_authentication": False,
        "native_execution_independently_proven": False,
        "ci_authority": False,
        "owner_approval": False,
        "release_approval": False,
    },
    "error_code": None,
    "error": None,
}
```

All six identity fields are null on failure. On success, `trusted_importer_sha256` comes from the verified transfer's `importer_sha256`; `transcript_manifest_sha256` comes from the validated embedded receipt and is a reported claim, not an independently verified transcript digest. Default output is `json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n"`, exactly once. Exit 0 for PASS, 1 for FAIL. Human success is exactly `PASS: Foundation native return matches the outbound handoff. No independent native authentication, execution proof, CI authority, owner approval, or release approval.\n`. Human failure is exactly `FAIL [CODE]: MESSAGE No independent native authentication, execution proof, CI authority, owner approval, or release approval.\n` using the table below. Never echo paths, events, destination, gate log, malformed input, exception text or Git output.

| Error code | Fixed message |
| --- | --- |
| INVALID_ARGUMENT | Invalid binding arguments. |
| UNSUPPORTED_PLATFORM | POSIX controller required. |
| GIT_OVERRIDE | Git environment overrides are not accepted. |
| INVALID_PATH | Unsafe binding path. |
| UNSAFE_SOURCE | Unsafe source file. |
| INPUT_LIMIT | Input limit exceeded. |
| INVALID_JSON | Invalid summary JSON. |
| INVALID_SUMMARY | Invalid Foundation importer summary. |
| INVALID_REPOSITORY | Repository inspection failed. |
| INVALID_CANDIDATE | Candidate commit unavailable. |
| INVALID_HANDOFF | Invalid Foundation outbound handoff. |
| CANDIDATE_MISMATCH | Returned candidate does not match the outbound handoff. |
| MANIFEST_MISMATCH | Returned outbound manifest digest does not match. |
| BUNDLE_MISMATCH | Returned bundle digest does not match. |
| SOURCE_CHANGED | Source changed during inspection. |
| INTERNAL_ERROR | Binding verification failed. |

Use a local `BindingError(code)` and a fixed `ERRORS` mapping. Translate existing helper failures at their boundary: path failures to INVALID_PATH, size failures to INPUT_LIMIT, drift to SOURCE_CHANGED, candidate lookup to INVALID_CANDIDATE, main selection to INVALID_REPOSITORY, remaining transfer failures to INVALID_HANDOFF. Unexpected exceptions become INTERNAL_ERROR without their text. Validate arguments, environment, repository, source safety, handoff, summary syntax/schema, then candidate/manifest/bundle bindings in that order; final drift checks precede PASS.

Summary root has exactly `schema status mode native_host native_evidence commit destination destination_created manifest_sha256 bundle_sha256 gate_summary gate_exit_code gate_log error_code error events`. Require schema `heleos.foundation-windows-native-import/v1`, status `PASS`, mode `native_suites`, all three boolean fields literally true, integer (not bool) zero gate exit, null errors, nonempty string destination/gate_log, complete lowercase commit/digests, and events a list of objects. Events and Windows paths remain opaque data and are never followed or printed.

Embedded receipt has exactly `schema manifest_sha256 suites status candidate_sha platform filesystem suite_count total_listed total_passed`. Require `heleos.native-suite-receipt/v1`, `pass`, candidate equal to summary commit, `windows-x86_64`, `NTFS`, integer suite_count 7, and seven suites in this exact order: `core-backup-restore`, `core-store`, `core-backup`, `platform-fs`, `cli-unit`, `cli-integration`, `workspace-all`. Each suite has exactly `id list_sha256 run_sha256 listed passed`; hashes are lowercase SHA256, listed/passed are real integers from 1 through 1000000 and equal. Totals are real integers equal to the sum. Embedded manifest digest is syntactically validated only: this command does not receive transcript files and cannot validate their hashes or execution.

Summary limit is 16 MiB; handoff text limit is the existing 64 KiB; bundle is streamed in chunks no larger than 1 MiB with a 512 MiB inspection ceiling. Reject oversize before allocation and during reads. Strict JSON rejects BOM, non-UTF8, duplicate keys at every level, trailing data, NaN/Infinity, nesting over 32, more than 100000 values, integer tokens longer than 16 characters and integers outside ±9007199254740991. Finite opaque event numbers are allowed. Enforce limits before recursive parsing/allocation, following the exporter's strict parser pattern without editing or invoking export execution.

Summary and inspected handoff files must be regular files, have link count one, and have no group/world write permission (`mode & 0o022 == 0`); read-only files are allowed, execute bits do not independently imply rejection. Open no-follow/nonblocking descriptors; compare lstat/fstat identity and retain descriptors until final verification. Pin lexical directory component identity plus file device, inode, mode, link count, size, mtime_ns and ctime_ns; recheck and rehash all consumed files after transfer and before output. A changed file, parent, importer or main registration/HEAD fails SOURCE_CHANGED. Extra unrelated files need not fail. The verifier-owned Git adapter imposes a 10-second subprocess timeout and 1 MiB output cap per pipe while draining, uses a sanitized environment, and replaces imported `state.git`/`state.git_text` before any inspection. Preserve their call signatures, argv and allowed-exit semantics. Pre-open and bound all consumed summary/handoff/importer sources and replace imported `read_file`/`hash_file` with descriptor-backed bounded adapters; enforce the bundle cap during initial and final hashing. Do not edit the helper or invoke its unbounded defaults.

## Planning Checkpoint

- [ ] Commit both completed planning documents before test or implementation changes:

```sh
git add -- docs/superpowers/specs/2026-09-10-foundation-native-return-binding-design.md docs/superpowers/plans/2026-09-10-foundation-native-return-binding.md
git diff --cached --check
git diff --cached --stat
git commit -m "docs: plan Foundation native return binding"
```

## Task 1: Implement the binding command through causal TDD

**Files:** Create `scripts/verify-foundation-native-return-binding.py`; create `tests/continuity/test_foundation_native_return_binding.py`.

**Interfaces:** `main(argv=None) -> int`; `verify(repo: str, candidate: str, handoff: str, summary: str) -> dict`; `validate_summary(value: object) -> dict`; `BindingError(code: str)`. CLI callers receive only the locked result. Dynamically import adjacent status helper using `importlib.util.spec_from_file_location`, then call `main_checkout(canonical(repo))`, `commit_exists(root, candidate)` and `transfer(root, observed["head"], handoff)`.

- [ ] Read applicable AGENTS.md, `scripts/foundation-release-status.py`, `scripts/verify-repo-state.py`, exporter strict parsing/source helpers, importer final summary, and existing continuity fixtures. Record initial Git status. Reuse fixture construction patterns, not inherited test classes that accidentally rerun unrelated tests.
- [ ] Create a self-contained disposable Git fixture with a registered main branch, ignored handoff, real candidate bundle, exact four checksum lines, committed synthetic importer, and a synthetic valid returned summary. Mark all fixtures synthetic. Fixture Git setup may write only inside its temporary directory; sanitize inherited Git environment for fixture setup and separately build the command's clean environment.
- [ ] Add the first causal contract test before creating the script:

```python
def test_success_binds_actual_outbound_bytes(self):
    completed = self.run_binding()
    self.assertEqual(completed.returncode, 0, completed.stderr)
    result = json.loads(completed.stdout)
    self.assertEqual(result["status"], "PASS")
    self.assertEqual(result["candidate_sha"], self.candidate)
    self.assertEqual(result["outbound_manifest_sha256"], self.sha256(self.manifest_path))
    self.assertEqual(result["bundle_sha256"], self.sha256(self.bundle_path))
    self.assertEqual(result["summary_sha256"], self.sha256(self.summary_path))
    self.assertTrue(all(value is False for value in result["authority"].values()))
    self.assertEqual(completed.stderr, "")
```

Define `run_binding(self, *extra)` to invoke `sys.executable`, the absolute SCRIPT, all required arguments and extra arguments with `capture_output=True, text=True` and sanitized environment. Define `sha256(self, path)` as hashlib.sha256(path.read_bytes()).hexdigest() for bounded synthetic test files. Fixtures expose candidate, manifest_path, bundle_path and summary_path.
- [ ] Run `python3 -B -m unittest discover -s tests/continuity -p test_foundation_native_return_binding.py -v`. Save the RED evidence: interpreter reports the missing script and the exit-zero assertion fails. An import/setup failure is not the expected causal RED.
- [ ] Commit the test-only causal RED before creating the script; record that this intentional intermediate commit fails because the CLI is absent:

```sh
git add -- tests/continuity/test_foundation_native_return_binding.py
git diff --cached --check
git diff --cached --stat
git commit -m "test: specify Foundation native return binding"
```
- [ ] Create minimal verifier with the locked contract; derive actual hashes from verified bytes and never trust a manifest's claimed bundle hash alone. Core comparison order:

```python
if summary["commit"] != candidate or transfer_result["candidate_sha"] != candidate:
    raise BindingError("CANDIDATE_MISMATCH")
if summary["manifest_sha256"] != transfer_result["manifest_sha256"]:
    raise BindingError("MANIFEST_MISMATCH")
if summary["bundle_sha256"] != transfer_result["bundle_sha256"]:
    raise BindingError("BUNDLE_MISMATCH")
```

- [ ] Run the focused test GREEN, then add one failing case per validation group below, implement its bounded check and rerun that group before moving to the next. Use `subTest` for type/schema variants with explicit expected error codes; do not assert failure alone.

| Test group | Cases and expected outcome |
| --- | --- |
| Bindings | Summary commit differs while embedded candidate follows it; selected candidate differs from valid handoff; root manifest digest differs; root bundle digest differs: corresponding mismatch code. Transcript manifest digest intentionally differs from outbound and still passes. |
| Handoff integrity | Mutate bundle, manifest bytes, checksum lines, copied importer, trusted importer; wrong advertised branch, nonignored/tracked handoff: INVALID_HANDOFF. A consistently regenerated handoff with stale returned digest fails binding. |
| Main selection | Invoke from sibling worktree and resolve main; candidate older than main remains valid; absent/ambiguous registered main fails INVALID_REPOSITORY; nonexistent candidate fails INVALID_CANDIDATE. |
| Summary schema | Missing/extra root and receipt keys, wrong schema/status/mode, bool-for-int, string-for-bool, wrong suite order/count, duplicate suite, count mismatch, malformed hashes, null/string events or nonobject event, empty destination/log: INVALID_SUMMARY. |
| Strict parsing | Invalid UTF8, BOM, trailing JSON, nested duplicate keys, NaN/Infinity: INVALID_JSON; every specified size/depth/value/integer bound crossed: INPUT_LIMIT. Valid Unicode, CRLF and opaque Windows strings pass. |
| Source safety | File and parent symlink, path traversal/alias: INVALID_PATH; summary/handoff hardlink, writable group/world mode, directory/FIFO input: UNSAFE_SOURCE. Oversized bundle/summary: INPUT_LIMIT. |
| Drift | Deterministically hook between initial observation and final checks; replace same-size summary, handoff README/checksums/importer/bundle, change parent identity, main HEAD or registration: SOURCE_CHANGED. No timing sleeps. |
| Secret safety | Put a unique secret sentinel in argument, malformed summary, event, Windows path and injected Git stderr; neither stdout nor stderr contains it in either output mode. |
| No side effects | Snapshot temporary repo refs, index, status, files/hashes/modes and handoff before/after success and failures; reject GIT_DIR/GIT_CONFIG_COUNT/GIT_OPTIONAL_LOCKS overrides; record subprocess argv and deny fetch, Cargo, checkout, extraction or write commands; assert no __pycache__. |
| Rendering | Exact result keys including trusted_importer_sha256 and transcript_manifest_sha256, false authority, all six identity fields null on failure, fixed messages, one LF, stable bytes across repeated calls, empty stderr, correct exits in JSON and human modes; fixed help, duplicate/unknown argument failures, and explicit duplicate `--human --human` rejection. |

- [ ] Use direct module tests with `unittest.mock.patch` for deterministic drift injection, and real subprocess CLI tests for all externally observable behavior. Patch descriptor reads to grow beyond caps and Git output to exceed bounds so the limits are tested causally without giant fixtures.
- [ ] Run focused tests with `python3` (3.14.6) and `/usr/bin/python3` (3.9.6). Capture `python3 --version` and `/usr/bin/python3 --version` alongside results; report any version drift explicitly.
- [ ] Review the entire diff and stage only the implementation and tests:

```sh
git add -- scripts/verify-foundation-native-return-binding.py tests/continuity/test_foundation_native_return_binding.py
git diff --cached --check
git diff --cached --stat
git commit -m "feat: verify Foundation native return binding"
```

## Task 2: Document the operator boundary and verify the complete slice

**Files:** Modify `docs/operations/foundation-native-evidence.md`, `README.md`, `SKILLS.md`; update `CURRENT_STATUS.md` only after verification. The design spec and plan are already in the planning commit.

**Interfaces:** Documentation consumes the exact CLI/result/errors above; it adds no runtime interfaces and does not alter exporter behavior or authority.

- [ ] Add the following operator explanation near the existing outbound/transcript digest distinction, followed by the exact CLI from Global Constraints: “Before packaging returned evidence, run the native return binding verifier against the original outbound handoff and the standalone final importer summary. PASS binds the candidate and the actual outbound manifest and bundle bytes. The embedded receipt manifest digest refers to the returned transcript manifest. This command does not inspect transcripts or authenticate execution. The Foundation importer always runs the full committed supply-chain gate.” Document input bounds, protected source modes, Git override rejection, both output modes, exits and all false authority fields.
- [ ] Add concise README.md and SKILLS.md discovery links to the operations section and verifier invocation. Explain that binding complements packaging and does not change exporter semantics or the acceptance ledger.
- [ ] Run the focused suite and full continuity suite under both discovered Python interpreters:

```sh
python3 -B -m unittest discover -s tests/continuity -p test_foundation_native_return_binding.py -v
/usr/bin/python3 -B -m unittest discover -s tests/continuity -p test_foundation_native_return_binding.py -v
python3 -B -m unittest discover -s tests/continuity -v
/usr/bin/python3 -B -m unittest discover -s tests/continuity -v
```

- [ ] Verify Python 3.9 AST grammar without writing bytecode, then inspect modes, hashes and scope:

```sh
python3 -B -c 'import ast,pathlib; paths=[pathlib.Path("scripts/verify-foundation-native-return-binding.py"),pathlib.Path("tests/continuity/test_foundation_native_return_binding.py")]; [ast.parse(p.read_text(),filename=str(p),feature_version=(3,9)) for p in paths]'
git diff --check
git diff --stat
git status --short
git ls-files --stage -- scripts/verify-foundation-native-return-binding.py tests/continuity/test_foundation_native_return_binding.py
shasum -a 256 scripts/verify-foundation-native-return-binding.py tests/continuity/test_foundation_native_return_binding.py
```

Expect tracked mode 100644 for the explicitly Python-invoked script and tests. Record interpreter versions, precise pass counts, hashes and unresolved checks. No Cargo or native gate execution is part of controller verification.
- [ ] Update the final CURRENT_STATUS.md checkpoint with current implemented behavior, recorded RED/GREEN evidence and both interpreter results; explicitly preserve native execution, CI, owner and release acceptance as separate outstanding gates. Do not copy secret fixture content or claim that synthetic tests prove native execution.
- [ ] Stage and commit only the documentation checkpoint:

```sh
git add -- docs/operations/foundation-native-evidence.md README.md SKILLS.md CURRENT_STATUS.md
git diff --cached --check
git diff --cached --stat
git commit -m "docs: record Foundation native return binding workflow"
git status --short
```

- [ ] Report the planning, test-only RED, implementation and documentation commit IDs, exact verification results and remaining native acceptance boundary. Any unrelated preexisting changes remain untouched and are disclosed separately.
