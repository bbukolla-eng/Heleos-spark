# Foundation 0.1 Native Windows Transfer Plan

**Status:** Implemented and packaged; native execution pending

**Date:** 2026-09-09

**Repository:** `/Users/bekim/Heleos-spark`

**Build worktree:** `/Users/bekim/Heleos-spark/.worktrees/foundation-native-transfer`

**Base:** `58e4fbedc17d661c600af687dedb41c75f00f013`

## Purpose

Create a deterministic, offline transfer path for an exact clean Foundation 0.1 candidate and a trusted native-Windows importer that accepts only the canonical seven-suite receipt. This tooling prepares native execution; it does not alter the tested candidate or claim Windows evidence.

## Global constraints

- Preserve exact tested candidate `58ab1e36c1f0cfccaf69c3a3c78b41495168fbbe` unchanged.
- The transferable ref is `release/foundation-0.1-native-<full-lowercase-candidate-sha>` and must terminate at that exact candidate.
- All package and import identities are independently pinned, exact, case-sensitive, and fail closed.
- The package contains complete Git history and exactly one advertised branch ref. It contains source history only, never dependencies, credentials, or claimed native results.
- The candidate must contain committed regular file `scripts/verify-supply-chain.ps1`.
- The Foundation manifest schema is `heleos.foundation-windows-native-candidate/v1`; its native gate is exactly `scripts/verify-supply-chain.ps1`, filesystem is exactly `NTFS`, and gate status is exactly `pending`.
- Existing worker-containment packaging/import behavior and schema remain unchanged.
- Native evidence requires a zero-exit candidate gate, exactly one canonical `heleos.native-suite-receipt/v1` object attesting the expected commit, `windows-x86_64`, `NTFS`, and seven suites, plus exactly one exact native Windows `SUPPLY_CHAIN_LOCAL_PASS` line.
- No overwrite, deletion, cleanup, network operation, dependency installation, remote Git operation, workflow write, push, release, or acceptance claim.
- Use test-first RED/GREEN development. Portable tests must not simulate or claim native Windows execution.

## Task 1: Add the Foundation packaging profile

**Owned paths:**

- `scripts/package-windows-candidate.py`
- `tests/packaging/test_windows_candidate.py`

Add explicit profile `foundation-supply-chain` while preserving default `worker-containment` behavior. The Foundation profile requires the exact branch/commit relationship and committed gate above, emits the Foundation schema with the existing exact nine-field manifest shape, and produces operator instructions for `import-foundation-native-candidate.ps1`. Add real isolated-Git black-box tests for success, determinism, wrong branch/commit binding, missing Foundation gate, and unchanged default behavior.

## Task 2: Add the trusted Foundation importer

**Owned paths:**

- `scripts/import-foundation-native-candidate.ps1`
- `tests/provider-adapters/test_foundation_native_candidate_import.ps1`

Create a PowerShell 7 importer with parameters `CandidateDirectory`, `Destination`, `ExpectedManifestSha256`, and `ExpectedCommit`. It must reject non-Windows before path probes or writes; require absolute local fixed NTFS paths without reparse ancestors; validate exact manifest bytes, schema, field set, branch/commit binding, bundle name/hash/length, gate, filesystem, and pending state; advertise exactly one expected bundle head; create a new checkout without hooks, remote protocols, local-object shortcuts, overwrite, or cleanup; verify exact clean HEAD; run the committed Foundation gate without preflight flags; and accept only the native evidence described above. Emit a final JSON summary with schema `heleos.foundation-windows-native-import/v1` and retain all failed or successful output for diagnosis.

Portable AST-loaded tests must exercise the real pure validators and prove manifest, bundle-head, receipt, PASS-line, exit-code, platform, filesystem, suite-count, duplicate/malformed JSON, and non-Windows fail-before-write behavior. They do not satisfy the native Windows gate.

## Acceptance

- Both task-specific RED failures are recorded before implementation.
- Existing 13 Python packaging tests remain green, with new Foundation cases green.
- Existing 27 portable worker importer checks remain green.
- New Foundation importer portable checks pass on the current macOS host.
- Python compilation, PowerShell parsing, repository whitespace checks, and changed-path inventory pass.
- The exact candidate is packaged only after this tooling is committed and integrated, using a dedicated exact ref/worktree; bundle reconstruction and `git fsck --strict` pass.
- Native Windows/NTFS execution and Foundation acceptance remain pending until real retained evidence exists.

## Execution checkpoint

Implementation commit `cbf936afd59e563e7e20f8a2ed446ea4047f6b13` is integrated into visible local `main`. Both required RED failures were observed before production implementation. Controller verification passed 19 packaging tests on Python 3.14 and 3.9, 62 portable Foundation importer checks, 27 unchanged worker importer checks, the complete 101-test Python repository suite, Python compilation, PowerShell parsing, and whitespace checks.

Exact release ref `release/foundation-0.1-native-58ab1e36c1f0cfccaf69c3a3c78b41495168fbbe` points to clean candidate `58ab1e36c1f0cfccaf69c3a3c78b41495168fbbe`. The generated bundle contains complete history and exactly that ref. Its manifest SHA-256 is `2b84da624fbc0e8549d3bb009ed898dd454a58d7e6e68226a2ab14e9295778ff`; bundle size is 4,210,962 bytes with SHA-256 `401f2c67970177c76825f42b485601f06ee300becbb3c59d8ec6ef93ab9d4765`.

The Finder-visible ignored handoff is `/Users/bekim/Heleos-spark/WINDOWS_NATIVE_HANDOFF_58ab1e3`. All four copied-file checks and `git bundle verify` pass. This is transfer evidence only. Native Windows x64/NTFS execution, same-candidate CI, GitHub App dispositions, workflow publication, and Foundation acceptance remain pending.
