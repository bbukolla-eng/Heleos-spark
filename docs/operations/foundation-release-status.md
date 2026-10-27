# Foundation release status

`scripts/foundation-release-status.py` inspects local Foundation release evidence
and reports the first unfinished release gate. It is read-only and offline: it
makes no file, network, or Git mutations. It does not publish a workflow, push a
commit, run CI, create acceptance evidence, or accept a release. Its evidence
ledger supports routing; it is not self-authorization.

Run the status command against the visible repository:

```sh
python3 /Users/bekim/Heleos-spark/scripts/foundation-release-status.py \
  --repo /Users/bekim/Heleos-spark
```

The default output is compact JSON. Add `--human` for an operator-readable
report. To select explicit local evidence and the known native-transfer handoff:

```sh
python3 /Users/bekim/Heleos-spark/scripts/foundation-release-status.py \
  --repo /Users/bekim/Heleos-spark \
  --handoff /Users/bekim/Heleos-spark/WINDOWS_NATIVE_HANDOFF_58ab1e3 \
  --evidence /absolute/path/to/foundation-release-evidence.json \
  --human --require-ready
```

Replace the evidence path with a real, independently supported local ledger.
The command does not create that file. `--repo` selects the repository to
inspect; `--handoff` selects its native-transfer package; `--evidence` selects
the release evidence ledger. `--require-ready` changes the exit behavior for a
valid inspection whose release gates remain blocked.

| Inspection result | Default exit | With `--require-ready` |
| --- | --- | --- |
| All gates ready | 0 | 0 |
| Normal blocked status | 0 | 2 |
| Invalid inspection | 1 | 1 |

Exit zero without `--require-ready` means the inspection completed; inspect the
reported status before treating a release as ready.

## Ordered release gates

The exact gate order is:

| Gate | Required evidence |
| --- | --- |
| `github_apps` | Resolved owner dispositions and required supporting evidence for the governed GitHub Apps. |
| `workflow_candidate` | An explicit release candidate, the candidate's `.github/workflows/core-ci.yml` blob identity, and a passing macOS ARM64 local supply-chain receipt bound to that candidate. |
| `owner_git_handoff` | Owner Git authorization and a push reference bound to the same release candidate. |
| `same_sha_platforms` | macOS ARM64 and Windows x86_64 CI evidence for the same candidate, including native Windows NTFS seven-suite receipt identity. |
| `acceptance_dossier` | A committed, unchanged owner acceptance record for that candidate after all preceding gates pass. |

The first unfinished gate determines the next release action. A later record
cannot bypass an earlier gate. A valid native-transfer handoff establishes
transfer readiness only; it never establishes Windows execution or NTFS proof.
The known `WINDOWS_NATIVE_HANDOFF_58ab1e3` package names a native-test candidate,
not an automatically selected release candidate.

The expected baseline result for this tooling is blocked first on four owner
dispositions: Azure Pipelines, AWS Connector for GitHub, Amazon Q Developer, and
ECC Tools. The designated release candidate, push authorization, same-SHA CI
evidence, and acceptance dossier remain absent in that baseline. This is an
expected starting condition, not a live status assertion; run the command for
the current inspection. Completed local implementation and native-transfer
preparation do not close these release gates.

## Evidence ledger contract

The strict Draft 2020-12 contract is
[`governance/foundation-release-evidence.schema.json`](../../governance/foundation-release-evidence.schema.json).
Every object rejects additional properties and requires every listed field.
The root has exactly `schema`, `release_candidate_sha`, `workflow`,
`git_handoff`, and `platforms`; `schema` is
`heleos.foundation-release-evidence/v1`.

`workflow` requires `status: "pass"`, `candidate_sha`,
`path: ".github/workflows/core-ci.yml"`, `blob_sha1`, and `local_supply_chain`.
That nested object requires `status: "pass"`, `candidate_sha`,
`platform: "macos-arm64"`, and `receipt_sha256`.

`git_handoff` requires `status: "pass"`, `candidate_sha`, a nonblank
`authorization_reference` without leading/trailing whitespace or control
characters, and `push_reference`. References and URLs are limited to 2,048
characters.

`platforms` requires `candidate_sha`, `macos`, and `windows`. Each platform
object requires `status: "pass"`, `candidate_sha`, `platform`, `run_url`, and
`artifact_sha256`. The macOS platform is exactly `macos-arm64`. The Windows
platform is exactly `windows-x86_64` and additionally requires
`filesystem: "NTFS"`, `suite_count: 7`, and `native_receipt_sha256`.

SHA-1 strings contain exactly 40 lowercase hexadecimal characters; SHA-256
strings contain exactly 64. Run URLs have exactly this structure:
`https://github.com/bbukolla-eng/Heleos-spark/actions/runs/<numeric-id>`.
The push reference has exactly this structure:
`https://github.com/bbukolla-eng/Heleos-spark/commit/<40-lowercase-hex-sha>`.
No query, fragment, trailing slash, or newline is allowed. The command checks
URL structure but never fetches these URLs or remote artifacts. A supplied URL
and digest do not independently prove that a remote run or artifact exists.

JSON Schema cannot express cross-field equality here. The Python command
enforces that every `candidate_sha`, the commit in `push_reference`, and the
acceptance dossier's candidate agree with `release_candidate_sha`. It also
performs the local repository checks. Passing schema validation alone does not
establish release readiness or authorize a Git operation.

## Owner acceptance dossier

The dossier is `docs/verification/foundation-0.1.md` under the resolved main
checkout. It must be a regular, non-symlink file tracked at main HEAD, with
working bytes identical to its committed bytes. It contains exactly one machine
block with these delimiters and an exact five-field JSON object:

````markdown
<!-- foundation-acceptance:v1 -->
```json
{"schema":"heleos.foundation-acceptance/v1","status":"accepted","release_candidate_sha":"<same 40-lowercase-hex candidate>","decision_owner":"Bekim Bukolla","accepted_at":"2026-09-09T14:30:00Z"}
```
<!-- /foundation-acceptance -->
````

The candidate placeholder is illustrative and is not valid evidence. The shown
timestamp is also illustrative: `accepted_at` must record the actual owner
decision using strict UTC RFC3339 seconds ending in `Z`. Only the owner can
provide the accepted decision. Do not manufacture this block to satisfy the
checker. Even a structurally valid, committed acceptance block cannot override
any preceding gate, and the status command never writes or grants acceptance.
