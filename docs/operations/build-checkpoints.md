# Build completion checks

The checkpoint guard catches missing or inconsistent continuation records. The
checkout controller reviews the candidate, runs the relevant checks, and commits
only when the owner explicitly asks. The guard does not calculate acceptance, approve work, write status, commit, push,
launch agents or schedule the next task.

## Normal workflow

1. Finish the bounded action and record its actual result in the task ledger.
   Keep the independent review and command output as files in this repository.
2. Update `CURRENT_STATUS.md`: completed work, remaining limits, primary product
   task and the next executable action. Keep the full CSI scope and Mac priority.
3. Update `docs/operations/build-checkpoint.json` and the status checkpoint block.
   Use `complete`, `in_progress` or `blocked` honestly. An in-progress checkpoint
   can keep the same task; it does not require claiming acceptance. A blocked
   checkpoint names its missing prerequisite and an independent action.
4. Stage only the owned changes, including status, receipt and evidence. Populate
   `changed_files` with each staged changed path and its SHA-256, except the
   receipt itself. Include status and reports; a deleted file has a null hash.
   Hash Git's staged blobs, not the working copies. Untracked evidence is not proof.
5. Run `python3 scripts/verify-build-checkpoint.py --staged`. Fix the named problem
   if it fails. The checkout controller reviews the staged inventory and commits only when the owner explicitly asks; the installed hook
   runs the same validator automatically.
6. After an authorized push or PR, the CI workflow checks new committed
   checkpoints and the existing repository checks. Local integration does not
   authorize a push or establish a successful GitHub run.

Completed work advances to a different next task, with successful check evidence
and accepted independent review. Expected limitations remain explicit. Partial
work and unrelated open sections do not need to become complete to save progress.

## Receipt and status binding

The receipt has `schema_version: 1`, a `task_id`, `outcome`, exact `base_commit`,
`summary`, nonempty `remaining_limits`, `next_task_id` and `next_action`. It records
the exact `changed_files` list and `checks` (command, integer exit code and an
evidence object containing path and SHA-256). Completed work has an accepted
`review` with reviewer identity and an evidence object. Other checkpoints may
have no review yet. `missing_prerequisite` and `independent_action` are populated
for blocked work and null otherwise. The current committed receipt is an example.

The single `build-checkpoint:v1` JSON block in `CURRENT_STATUS.md` names the
canonical receipt and repeats its task, outcome and next fields. The visible
**Primary product task** and **Next executable action** must agree with those
next fields after whitespace normalization. This binds the machine check to the
human continuation queue. It cannot prove that the prose captures every decision.

Each ordinary commit records its immediate parent as the base. All changed paths
are included once in the manifest; additions, modifications and deletions count.
Evidence must be regular files in the selected index or tree and match its hash.
Proof cannot cite the receipt or status as its own acceptance evidence.

Code, tests, build configuration, agent instructions, current plans and operation
records require checkpoints. Empty staging and unrelated archival documentation
can skip. The CSI structural checker runs only for changes to the section
register, task contracts, section cards or generated coverage views. It reads a
temporary materialization of the selected Git tree; local untracked files cannot
fill missing section evidence. It does not re-evaluate mechanical requirements.

## Installation and CI

Run `python3 scripts/install-build-hooks.py` once per clone. It installs the
repository-owned executable `.githooks/pre-commit` using repository-local
`core.hooksPath`. It refuses to replace a custom hook path or hide executable
default hooks. Repeat installation is harmless when this path is already active.
No global Git settings are changed. New clones need their own installation.

The hook reads the staged validator and returns its exit status. Git's documented
pre-commit behavior blocks the commit on nonzero exit; local hooks can be bypassed,
so they are an error-prevention aid rather than a tamper-proof boundary.
[Git hook reference](https://git-scm.com/docs/githooks).

`python3 tools/ci/check_build_checkpoints.py --base BASE --head HEAD` checks a
commit range. In GitHub Actions the adapter reads the event payload directly:
the actual PR head/base or push before/after. It checks each new ordinary commit,
including earlier commits when the tip has a valid receipt. Historical ancestors
of adoption baseline `db695750d095c6845f0689e3cc4da79d09333935` are not retroactively
required to have the new schema. Missing history or ambiguous adoption ancestry
fails with a diagnostic. Ordinary merges reuse validated parent checkpoints;
manual merge-resolution changes require their own checkpoint. No event value is
interpolated into a shell command.

The workflow retains read-only permissions and the standard PR/push events.
CI execution and branch-protection enforcement on GitHub require publication and
repository settings; this local setup does not claim those remote outcomes.

Once a checkpoint passes, resume its recorded next product action. Do not turn
this check into a recurring status rewrite or a reason to reopen accepted work.

Retained Markdown snapshots keep their original bytes. The repository link check
uses `docs/operations/retained-document-origins.json` to verify each named copy's
hash and resolve its links from the recorded original location. Missing targets
and changed copies still fail. Current documents use their normal location.
