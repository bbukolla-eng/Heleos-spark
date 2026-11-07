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

## Research and continuation enforcement

The policy at `docs/operations/checkpoint-evidence-policy.json` activates these additional checks for protected work. Policy-free historical commits retain their original validation contract. Once the policy is present in any parent (including a pending merge parent), it cannot be removed, malformed or disabled in the candidate. Receipt and status schema versions remain 1; the policy is a separate forward activation, not a history rewrite.

Every active-policy receipt includes `notebooklm_research` with the disposition and string explanations specified in the [research workflow](notebooklm-research.md#required-task-evidence-owner-reaffirmed-2026-09-22). Its `records` are path/SHA-256 proofs in the selected Git tree. Unstaged or untracked evidence does not count.

For `new_verified` and `reused_verified`, each record is a JSON packet with `schema_version: 1`, `kind: notebooklm_verified_findings`, pinned `query` and `verification` proof objects, and a nonempty `findings` array. Each finding contains a unique `id`, `notebook_id`, `source_id`, `locator`, `finding`, `passage`, `verification_status: verified_applicable`, nonempty `behaviors` and `checks` string arrays, and a pinned `source` proof. The passage must occur verbatim in the retained UTF-8 source bytes. Source bytes may be a retained verified excerpt; the query and verification receipts preserve its provenance. Packet/status/receipt self-proof, unsafe paths, symlinks, mismatched hashes and malformed structures fail. The [current normalized packet](evidence-enforcement-2026-09-22/research.json) demonstrates reuse of the original verified NotebookLM findings without claiming a new query.

For `unavailable`, the receipt must name a missing prerequisite and independent action, and cannot be complete. A partial checkpoint preserves progress without accepting unsupported work. `administrative_no_new_claims` is restricted to documentation and operations-evidence paths; code, tests, configuration, policy activation and their deletion cannot use that exemption. Only non-executable regular documentation files qualify; deletions do not use this exemption. A filename check cannot prove that narrative evidence is honest or relevant. Reviewers must still inspect the actual source passages, scope and behavior/test bindings.

The validator also checks completed task IDs in reachable ancestor checkpoint receipts. If `next_task_id` refers to one, require `next_task_reopen` containing that exact task ID, a nonempty reason and a hash-pinned evidence object. New defects or changed inputs justify reopening; routine continuation does not. The existing prohibition on a completed task selecting itself remains. A blocked checkpoint must select independent next work rather than itself. These checks cover recorded checkpoint history, not unrecorded work or every external task tracker.

Query and verification proofs are opaque retained provider/reviewer records: the guard validates their path and byte identity, not their response schema, origin, notebook/source cross-links or truth. Independent review must inspect those records and verify those relationships. The normalized findings packet and exact source-passage presence are structurally checked.

Direct staged/commit validation checks all merge parents, even when the selected merge tree equals its first parent. The unchanged CI adapter reuses individually validated parent checkpoints for clean automatic merges; manual merge resolutions invoke the validator.

The existing local pre-commit hook and CI range adapter invoke the updated validator; no hook, workflow or repository settings changes are needed. Local hooks can still be bypassed using Git options, and CI merge enforcement depends on repository protections. The checker never runs receipt commands, chooses tasks, edits status or queries NotebookLM. The controller still advances the live queue and remains its sole general writer.
