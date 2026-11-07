# Retained-document CI repair candidate

Task: BUILD-CHECKPOINT-GUARD-1 / scoped retained-link repair.
Worker: `/root/ci_retained_links`.
Checkout: `/Users/bekim/Heleos-spark`, branch `main`.
Base: `db695750d095c6845f0689e3cc4da79d09333935`.
Ownership: only `tools/ci/checks.py`, `tests/test_ci_checks.py`, and this report.
No commits, external requests, approved packet edits, archive edits, or other worker edits.

## Frozen repair criteria

- R01: Named retained Markdown files keep their exact SHA-256 and resolve their existing links from the recorded original source directory. A mapping never skips target checks. Hash drift, unsafe paths, non-regular files, duplicate copies, and malformed registry entries fail with diagnostics.
- R02: Unmapped live-document link behavior and existing workflow registry, JSON, prose and egress checks remain intact. The existing CI entrypoint also accepts an explicit repository root for isolated verification.

## Candidate

The optional `docs/operations/retained-document-origins.json` registry is validated as schema version 1 with an array of exact records: retained_path, source_path, sha256, basis. All mapped copies are checked even if they are absent from the supplied Markdown subset. Both paths and the registry must be regular repository files with canonical relative paths and no symlink components. Only an unchanged, explicitly mapped document changes its link-resolution base. The recorded source location does not claim that today's source body equals the retained copy.

The root coordinator owns the four-record registry and the separately discovered roadmap prose repair. Historical and approved source bytes were not modified by this worker. This is CI link provenance, not mechanical source authority or new section acceptance.

## Test-first evidence and terminal results

1. Added nine real filesystem regression cases before production changes. `python3 -m unittest discover -s tests -p test_ci_checks.py`: exit 1, 22 tests, 31 expected assertion failures from missing retained-origin support. The 13 existing tests passed.
2. Implemented the bounded origin reader and link-base selection. Same command: exit 0, 22 tests passed.
3. Added a real temporary Git repository test for `main(root=...)`, including retained-byte drift: same command exit 0, 23 tests passed. The main test intentionally prints a passing fixture check and a failing hash-drift fixture check while asserting their respective 0/1 results.
4. `git diff --check -- tools/ci/checks.py tests/test_ci_checks.py`: exit 0.
5. `python3 tools/ci/checks.py`: exit 0, 958 tracked files checked, 0 failures, with the coordinator's registry and prose repair present. The original 60-failure log is retained separately; it was not overwritten.

Test coverage includes unchanged copies whose current source bodies differ; changed retained bytes; missing mapped destinations; ordinary unmapped live links; mappings validated independently of selected files; absolute, traversal, separator and NUL paths; symlink escape; duplicate and malformed entries; malformed schema and JSON; non-regular files; and the real main entrypoint.

## Exact candidate identities

| Path | SHA-256 |
| --- | --- |
| tools/ci/checks.py | c02306c3a2d5ac929a95a9a672f2ea96247abda2d53673c398c415d9cf7f855e |
| tests/test_ci_checks.py | 994320844cff4f236a2d0364f61f93d55579a06a580e92593ec4a72c810b9346 |
| docs/operations/retained-document-origins.json (coordinator-owned input) | c148441cea91cfb3880c0ed039a7cefcfca8221cce3974fe251ef4c5fd61a79e |

All worker processes are terminal. Candidate ready for independent review and coordinator verification; no self-acceptance or integration claimed. Next action: reviewer checks R01/R02 against these bytes, then coordinator includes accepted repair in the existing completion checkpoint. The main guard and unrelated product checks are not reopened.
