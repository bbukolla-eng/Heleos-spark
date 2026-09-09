# GitHub App owner decision packets

`scripts/apply-github-app-decisions.py` validates an owner-supplied decision
packet and prepares the corresponding `governance/github-apps.toml` bytes. Its
default mode is a dry run. Explicit `--apply` atomically replaces that one
registry file after validation and stale-state checks.

This records an owner decision. It does **not** configure, retain, restrict,
suspend, or remove an actual GitHub App installation, and it does not prove an
installation action happened. Account changes and the evidence supporting them
are separate owner actions. The command performs no network request, commit,
push, workflow publication, or account mutation. Its result always reports
`account_changes_performed: false`.

At this implementation's starting point, the four registry dispositions remain
`owner_decision_required`. Foundation's first release gate remains pending until
the owner supplies real choices and substantive decision and installation
evidence. This tool supplies the local recording mechanism; it does not make
those choices. Run the release-status command for the current repository state.

## Decisions and supporting evidence

Every packet covers exactly these four Apps, each once: Azure Pipelines, AWS
Connector for GitHub, Amazon Q Developer, and ECC Tools. The owner is exactly
`Bekim Bukolla`. Each App requires one of these decisions:

| Disposition | Meaning recorded in the registry | Egress policy |
| --- | --- | --- |
| `retain` | Owner chooses to keep the installation within the evidenced permissions and scope. | `prohibited...`, or `approved:` followed by substantive approved scope. |
| `restrict` | Owner chooses a narrower installation scope or permissions, supported by evidence. | `prohibited...`, or `approved:` followed by substantive approved scope. |
| `suspend` | Owner chooses suspension, with the installation state separately evidenced. | Must start with `prohibited`. |
| `remove` | Owner chooses removal, with the installation state separately evidenced. | Must start with `prohibited`. |

For each App, supply `version_or_digest`, actual installation `permissions`, an
`egress` policy, and an `evaluation` explaining the evidenced decision. Supply
`decision_evidence` identifying the owner's decision record and
`installation_evidence` identifying the relevant installation-state record.
Public application metadata, an App ID, a slug, an installation ID, historical
activity, or a successful schema check alone is insufficient runtime, grant, or
installation evidence. Keep secrets, tokens, cookies, and credentials out of
both the packet and referenced records.

Resolved fields must be substantive. Placeholder prefixes such as `pending`,
`unknown`, `not inventoried`, `not evaluated`, `tbd`, `unversioned`, `latest`,
`owner decision required`, and `unavailable` do not satisfy the command. It also
rejects unresolved wording within version, permissions, and evaluation fields,
and rejects App/installation identity text as version evidence.

There is one narrow exception: for `suspend` and `remove` only,
`version_or_digest` or `permissions` may begin with exact `unavailable:` followed
by a substantive reason. For example, a completed removal may prevent inspection
of the former installation's runtime. This does not waive installation evidence,
the evaluation, the owner decision record, or any other required field. The
exception does not apply to `retain` or `restrict`.

## Packet contract and state binding

The [Draft 2020-12 JSON Schema](../../governance/github-app-decisions.schema.json)
rejects additional properties at the root and inside every App record. The root
requires exactly `schema`, `repository`, `expected_head`,
`expected_registry_sha256`, `decision_owner`, `decision_date`, and `apps`.
`schema` is `heleos.github-app-decisions/v1`; `repository` is
`bbukolla-eng/Heleos-spark`.

`expected_head` is the full 40-character lowercase main commit SHA;
`expected_registry_sha256` is the 64-character lowercase SHA-256 of the registry
bytes the owner inspected. `decision_date` uses `YYYY-MM-DD`; the command also
validates the actual calendar date, including leap years. These fields bind the
decision to the inspected main state. A changed HEAD or different registry
requires a fresh inspection and a newly supported packet; do not merely replace
the hashes to bypass a stale-state failure.

Each App record has exactly `name`, `disposition`, `version_or_digest`,
`permissions`, `egress`, `evaluation`, `decision_evidence`, and
`installation_evidence`. Free strings must contain printable ASCII, have no
leading or trailing whitespace, and be nonblank. Maximum lengths are 4,096
characters for version and egress, 8,192 for permissions and evaluation, and
2,048 for each evidence reference.

Schema validation is a structural check. The command additionally enforces
unique names, substantive and unresolved-text rules, the exact reasoned
unavailability exception, real dates, repository identity, and cross-state
relationships between the packet, committed registry, and working bytes.
The packet must be a regular, non-symlink UTF-8 JSON file no larger than 64 KiB;
duplicate JSON keys are rejected. Normal application requires unchanged tracked
main files and a registry/index matching the committed state. The only dirty
registry exception is an unstaged exact result from the same packet; staged,
mode, rename, and unrelated tracked changes do not qualify.
Neither the schema nor the command can substitute for authentic owner evidence.

## Prepare an unresolved draft

The exact preparation CLI is:

```text
python3 scripts/prepare-github-app-decisions.py [--repo PATH] --output PATH [--human]
```

`--repo` defaults to `.` and resolves the unique registered visible-main checkout,
including from a nested linked worktree. `--output` is required: use a canonical
direct `.json` child of that main checkout's visible
[OWNER_ACTION_REQUIRED](../../OWNER_ACTION_REQUIRED/README.md) directory. The
directory must already exist and be a real, non-symlink directory; the target
must be ignored, untracked, and absent. The generator never creates directories.
After integrating the generator and its directory README into main, prepare a
draft with a new filename:

```sh
python3 /Users/bekim/Heleos-spark/scripts/prepare-github-app-decisions.py \
  --repo /Users/bekim/Heleos-spark \
  --output /Users/bekim/Heleos-spark/OWNER_ACTION_REQUIRED/github-app-decisions.json \
  --human
```

The indented UTF-8 JSON draft has one terminal newline and the seven root fields
in the contract order above. It fills the schema, repository, inspected full
main HEAD, SHA-256 of the exact committed registry bytes, and intended recipient
`Bekim Bukolla`. Its `apps` records follow the canonical registry order listed
above. Only each App's `name` is populated: all seven remaining fields and the
root `decision_date` are JSON `null`, exactly 29 unresolved fields. No current
registry decisions, metadata, permissions, evaluations, dates, or evidence
references are copied. The untouched draft must fail the schema and applier.

Preparation requires unchanged tracked main files and a regular working registry
whose identity, Git executable classification, bytes, and index entry match
committed state. Its full live mode must remain stable during the operation; Git
does not attest arbitrary owner/group permission bits. It rechecks
HEAD, registry, and destination-parent identity before publication. A private
same-directory temporary regular file is written and fsynced, then published
atomically without replacement. Every existing target type, even identical bytes,
and competing creation is rejected. Final bytes, identity, hash, and private mode
are verified; the containing directory is fsynced where supported. There is no
force, overwrite, update, refresh, apply, network, or evidence-discovery mode.

Default output is deterministic compact JSON with exactly `schema_version`,
`status`, `main`, `registry`, `packet`, `unresolved_fields`, `write_performed`,
`account_changes_performed`, `owner_authenticity_verified`, `release_authority`,
and `errors`. Success is `PREPARED` (exit 0); failure is `FAIL` (exit 1), with
fixed diagnostics. `unresolved_fields` is 29; `write_performed` becomes true only
after publication. The three authority booleans remain false. `--human` provides
concise output with the same success/failure semantics.

The generator makes no decisions, validates no evidence, does not authenticate
the owner, and grants no release authority. Naming the recipient is not a
signature. It changes no accounts, workflows, registry, Git HEAD, or index;
its only intended persistent write is the requested ignored draft.

Inspect the bound state and have the owner fill all 29 unresolved fields with
actual choices, date, and supporting evidence. If HEAD or registry changes,
inspect the new state and prepare a new filename; preserve the old draft and do
not merely replace its hashes. The command cannot refresh an existing draft.
Then follow the dry-run and explicit application flow below, using the completed
draft's absolute path as `--packet`.

## Inspect, dry-run, then apply

Inspect the visible main checkout and compute the identities without changing
files. Use the resulting values only after inspecting the referenced state:

```sh
git -C /Users/bekim/Heleos-spark rev-parse --verify HEAD
git -C /Users/bekim/Heleos-spark status --short -- governance/github-apps.toml
python3 -c 'import hashlib; from pathlib import Path; print(hashlib.sha256(Path("/Users/bekim/Heleos-spark/governance/github-apps.toml").read_bytes()).hexdigest())'
```

Prepare a local JSON packet containing the owner's actual decisions and evidence
references. The packet may be outside the checkout. Replace the example path
below with that file's absolute path. Run the default dry run first:

```sh
python3 /Users/bekim/Heleos-spark/scripts/apply-github-app-decisions.py \
  --repo /Users/bekim/Heleos-spark \
  --packet /absolute/path/to/owner-github-app-decisions.json
```

The exact CLI is `--repo PATH` (default `.`), required `--packet PATH`, optional
`--human`, and explicit `--apply`. `--repo` can identify a registered checkout or
nested directory; the command resolves the unique registered visible-main
checkout. It writes only main's `governance/github-apps.toml`, even when invoked
from another checkout. Use `--human` for concise operator output. Default output
is deterministic compact JSON.

After checking the dry-run result against the owner-supplied packet, perform the
explicit local registry update:

```sh
python3 /Users/bekim/Heleos-spark/scripts/apply-github-app-decisions.py \
  --repo /Users/bekim/Heleos-spark \
  --packet /absolute/path/to/owner-github-app-decisions.json \
  --apply --human
```

| Status | Exit | Effect |
| --- | --- | --- |
| `DRY_RUN` | 0 | Packet and proposed registry bytes are valid; no file is written. |
| `APPLIED` | 0 | One atomic replacement of the main registry completed. |
| `ALREADY_APPLIED` | 0 | Under the same HEAD, working registry bytes already exactly equal this packet's desired result; no file is written. |
| `FAIL` | 1 | Arguments, packet, evidence semantics, or repository-state checks failed. |

`ALREADY_APPLIED` makes an exact rerun safe while HEAD is unchanged. It is not a
general exemption for a stale packet or permission to overwrite other edits.
Inspect `status`, `write_performed`, and `errors`; exit zero alone does not mean
an account action or release acceptance occurred. JSON also identifies main's
path and HEAD, the packet path and SHA-256, registry path and before/after
SHA-256, and the four Apps in canonical order.

The resulting change is an ordinary, recoverable Git working-tree diff; the
committed registry remains available for comparison. Inspect it without changing
Git state:

```sh
git -C /Users/bekim/Heleos-spark diff -- governance/github-apps.toml
python3 /Users/bekim/Heleos-spark/scripts/foundation-release-status.py \
  --repo /Users/bekim/Heleos-spark --human
```

The release-status command requires committed registry bytes. Immediately after
`--apply`, its uncommitted-registry failure is expected and does not mean the
atomic update failed. The diff needs the separate owner-authorized integration
workflow before release-status can recognize the recorded decisions. This guide
does not authorize or perform that integration, a commit, a push, or any account
change. Even after the first gate passes, workflow, Git handoff, platform, and
acceptance gates remain independently enforced.

## Deliberately invalid illustrative packet

The following is complete in field coverage but deliberately invalid. Every
`<...>` value is an unfilled placeholder; the dispositions, date, and digests do
not satisfy the schema. No owner choice is implied. Do not run it as an owner
packet or turn it into a passing packet by inventing choices or evidence.

```json
{
  "schema": "heleos.github-app-decisions/v1",
  "repository": "bbukolla-eng/Heleos-spark",
  "expected_head": "<actual inspected main HEAD>",
  "expected_registry_sha256": "<actual inspected registry SHA-256>",
  "decision_owner": "Bekim Bukolla",
  "decision_date": "<actual owner decision date>",
  "apps": [
    {
      "name": "Azure Pipelines",
      "disposition": "<owner choice required>",
      "version_or_digest": "<verified runtime version or digest>",
      "permissions": "<verified installation permissions and scope>",
      "egress": "<owner-evidenced egress policy>",
      "evaluation": "<substantive evaluation>",
      "decision_evidence": "<actual owner decision record>",
      "installation_evidence": "<actual installation state record>"
    },
    {
      "name": "AWS Connector for GitHub",
      "disposition": "<owner choice required>",
      "version_or_digest": "<verified runtime version or digest>",
      "permissions": "<verified installation permissions and scope>",
      "egress": "<owner-evidenced egress policy>",
      "evaluation": "<substantive evaluation>",
      "decision_evidence": "<actual owner decision record>",
      "installation_evidence": "<actual installation state record>"
    },
    {
      "name": "Amazon Q Developer",
      "disposition": "<owner choice required>",
      "version_or_digest": "<verified runtime version or digest>",
      "permissions": "<verified installation permissions and scope>",
      "egress": "<owner-evidenced egress policy>",
      "evaluation": "<substantive evaluation>",
      "decision_evidence": "<actual owner decision record>",
      "installation_evidence": "<actual installation state record>"
    },
    {
      "name": "ECC Tools",
      "disposition": "<owner choice required>",
      "version_or_digest": "<verified runtime version or digest>",
      "permissions": "<verified installation permissions and scope>",
      "egress": "<owner-evidenced egress policy>",
      "evaluation": "<substantive evaluation>",
      "decision_evidence": "<actual owner decision record>",
      "installation_evidence": "<actual installation state record>"
    }
  ]
}
```
