# Worker protocol implementation evidence

Worker: `agent_protocol_code` (sole implementation writer).
Checkout: `/Users/bekim/Heleos-spark/.worktrees/agent-control-foundation`.
Branch: `build/agent-control-foundation`.
Base: `af526a3c9c7ad93f360b6629e9b592a81787b341`.
Status: implementation finished; required local package checks passed; no worker commit, merge, push, provider dispatch, or review.

## Instruction identities

| Input | SHA-256 |
| --- | --- |
| `AGENTS.md` | `8c836bb2aeec5249edd993c0909aa162cabbea144e6d4234c3ab18ea71544e4d` |
| `CURRENT_STATUS.md` | `387d0cee5d8f830285eef8318c4da01f2491ad930e1395e78d48cf5ea88a79bc` |
| `docs/superpowers/plans/2026-09-08-agent-coordination-foundation.md` | `42873f3ac3e23c198399467f3de8d515555cde6ff0d80398d5968d9ebb7108be` |

## RED evidence

Tests were written before functional implementation. The initial library placeholder admitted all inputs and returned empty canonical bytes/digests; the initial CLI was an empty `main`. Both suites compiled and failed on missing behavior, not a missing package or import.

- `cargo +1.96.1 test --locked --offline -p heleos-worker-protocol --test validation`: exit **101**, **0 passed, 11 failed, 0 ignored**. Output is preserved in `initial-red-validation.txt`.
- `cargo +1.96.1 test --locked --offline -p heleos-worker-protocol --test cli`: exit **101**, **0 passed, 4 failed, 0 ignored**. Output is preserved in `initial-red-cli.txt`.

Implementation iteration encountered two separate corrective issues after initial RED: SHA-256's pinned array type does not implement `LowerHex`, so hexadecimal encoding now explicitly converts digest bytes; a synthetic positive test candidate commit was accidentally 42 characters and was corrected to 40. These were implementation/fixture corrections, not additional expected RED evidence. The final suite passed after both corrections.

## GREEN evidence

All commands ran in the checkout above using Rust `1.96.1`.

| Exact command | Terminal result |
| --- | --- |
| `cargo +1.96.1 fmt --package heleos-worker-protocol` | exit 0 |
| `cargo +1.96.1 fmt --package heleos-worker-protocol -- --check` | exit 0 |
| `cargo +1.96.1 test --locked --offline -p heleos-worker-protocol` | exit 0; 11 library integration tests and 4 black-box CLI tests passed; zero failed/ignored |
| `cargo +1.96.1 clippy --locked --offline -p heleos-worker-protocol --all-targets --all-features -- -D warnings` | exit 0; no warnings |
| `cargo +1.96.1 metadata --locked --offline --format-version 1 > /dev/null` | exit 0 |
| `git diff --check` | exit 0 |

The CLI tests compare byte-identical validation envelopes from two independently created physical roots, including reformatted task JSON. They verify rejection of bad arguments, directories, missing/oversized JSON, unknown fields, and task mismatch; invalid input yields empty stdout and one bounded diagnostic. A literal `touch COMMAND_EXECUTED` acceptance command and a semicolon-containing input filename remain data; the input file is unchanged and no extra file is created.

The library tests cover strict nested fields, duplicate keys including the instruction map, required fields, malformed/trailing JSON, schema/identity validation, provider-mode and data-egress combinations, sorted unique portable paths, subtree boundaries/forbidden precedence, task/provider handoff identity, check order/membership, completion evidence, and optional candidate commits. The hand-written canonical task's SHA-256 was independently calculated with `shasum` as `688d224c29cbd81267681fe61efd20eef39a99ababbde7b61049f18658efb903` and asserted literally.

All seven controller-owned task fixtures under `tests/fixtures/agents/` were also validated by the compiled CLI with exit 0. Exact Claude checks:

```text
target/debug/heleos-worker-protocol task tests/fixtures/agents/claude-code-implementation.task.json
exit 0; digest a58ce9c4301b3b64870b3e78865d5b866ba325b6c26580358d31ac1252ffa964

target/debug/heleos-worker-protocol handoff --task tests/fixtures/agents/claude-code-implementation.task.json tests/fixtures/agents/claude-code-completed.handoff.json
exit 0; digest 01fd0118a278613ed1c326ff962f110f353f78cfa600997680e1e650fa9cb60c
```

## Scope and dependency change

Owned changes are the root workspace membership entry, the single local package lock record, and this new crate. `cargo +1.96.1 metadata --offline --format-version 1 > /dev/null` performed the necessary initial local-package lock update. Inspection of `git diff -- Cargo.lock` confirms exactly 13 added lines for `heleos-worker-protocol`; no existing package identity, version, source, checksum, or dependency list changed. Subsequent metadata/tests/Clippy use `--locked --offline`.

No existing crate, Foundation source or manifest, release script, SBOM, workflow, GitHub App registry, provider document, or roadmap was written by this worker. Concurrent controller-owned documentation and fixture changes exist outside this worker's ownership.

## Changed-file identities

Hashes below identify completed implementation/test bytes and initial RED evidence. This report is also a new owned file; its own hash is reported separately in the handoff to avoid self-reference.

| Path | SHA-256 |
| --- | --- |
| `Cargo.toml` | `8680fcedec6fbfc3c6bb1a1e3f70628ec72e12d5dbf8c0302eace323990633ec` |
| `Cargo.lock` | `b07c7c19c26aa1eedc28a5cbcf397ad804c62e5f9b49aed59db62a82a61e0bfd` |
| `crates/heleos-worker-protocol/Cargo.toml` | `a494cc249a60882d9c6048bef7b8b909bb0df435aea026372283264946535f4f` |
| `crates/heleos-worker-protocol/src/lib.rs` | `9616f5a565a859d53ca02961a6157f080b917f36e27aae1ad8d51494c03c8578` |
| `crates/heleos-worker-protocol/src/main.rs` | `9ae3af33086fa17c2ad791fdd7ea062eb7c4576ee1a2484bad0a8dcccf868660` |
| `crates/heleos-worker-protocol/tests/cli.rs` | `020e543800748866bf9af0ce8bcef94e4a6b4044b8c591deebf2271a998635a9` |
| `crates/heleos-worker-protocol/tests/common/mod.rs` | `b3360d07883ba66371968ce2f7c80acb0b01b739cc1fd3ad6aa9b06b6dbfb7c1` |
| `crates/heleos-worker-protocol/tests/validation.rs` | `96938763480684629a34fbbcdbadc828cdbc94443587b09a4184930d284a1943` |
| `crates/heleos-worker-protocol/evidence/initial-red-cli.txt` | `adb4aefc0ac9d24f0cb6bfac7b6b893bbb46411076f5a558b9097af8a8a76581` |
| `crates/heleos-worker-protocol/evidence/initial-red-validation.txt` | `604823c9386c535fcb83e18c4d8e8bc1c483ea56abb87e4c46a25d0094d26745` |

## Limits and next action

These checks validate packet claims. They do not prove that a command ran, that output evidence exists, that a candidate Git commit exists, or that a changed-file listing is complete. Path containment is lexical and case-sensitive, not a filesystem sandbox; a controller must independently verify actual filesystem and Git state before accepting worker output. Missing optional `candidate_commit` canonicalizes as null. The package neither executes packet commands nor contacts providers, a network, or production data stores.

No process remains running and no implementation blocker remains. Next action: the controller runs the assembled-branch provenance/changed-file checks and performs the authorized isolated-branch handoff. These package checks do not establish Foundation release or native Windows acceptance.
