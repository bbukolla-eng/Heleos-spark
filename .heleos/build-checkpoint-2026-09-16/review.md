# BUILD-CHECKPOINT-GUARD-1 independent review

Date: 2026-09-16. Reviewer: Codex agent `/root/checkpoint_review`, independent of implementation writers. Base: `db695750d095c6845f0689e3cc4da79d09333935`; checkout `/Users/bekim/Heleos-spark`, branch `main`.

**Code candidate accepted. No remaining blocking findings against V01–V06.** The coordinator retains V07 integration: finalize the status/receipt, verify staged bytes and commit through the installed hook. This report does not authorize or claim a remote push, remote CI execution or product acceptance.

## Findings and disposition

1. V02 initially omitted existing agent workflow, policy and configuration surfaces. The explicit trigger set now includes `.claude/`, `.codex/`, `governance/`, `docs/policies/`, `docs/roadmap.md` and existing root configuration/instruction names. The archival exemption is limited to documentation extensions; executable files there still require a checkpoint. Regression fixtures pass.
2. V03 initially allowed a completed task to retain the same next task with surrounding whitespace. Completion now compares normalized identities; the coherent same-task fixture is rejected.
3. V04 initially permitted a local replacement object to substitute for the staged validator. The hook loader and diff gate now use `--no-replace-objects`; the new real-Git test rejects the replacement and preserves HEAD. The validator and CI graph reads also pin underlying objects.

## Contract assessment

- V01: source bytes come from stage-zero index objects or named commit trees. Dirty/untracked repairs do not satisfy validation. Missing/malformed receipts, unsafe/nonregular proof, invalid outcomes, duplicate or mismatched manifest entries and unresolved index conflicts fail. No receipt command is executed.
- V02: protected changes require changed status and receipt. Exact manifests include status/evidence and all changed paths except the receipt. Deletions require null hashes; renames are represented as deletion/addition through `--no-renames`. The actual parent/HEAD binds the base.
- V03: a single JSON status block and visible continuation fields agree with the receipt. Completion requires advancing task identity, successful check evidence and accepted review. Blocked checkpoints require the missing prerequisite and independent action. These are consistency checks, not proof that a human-authored claim is true.
- V04: actual Git tests show rejection and acceptance through the hook, staged checker isolation and existing hook preservation. The actual repository currently reports local `core.hooksPath=.githooks`. Empty staging is fast; archival-only changes do not launch product checks.
- V05: parent-first traversal covers every newly reachable ordinary commit after the pinned adoption baseline. A later good receipt cannot cover an earlier bad commit. Actual PR head/base are selected, automatic merges reuse checked parents, and novel resolution/manual changes are checked. Missing history and unsupported octopus merges fail explicitly. Workflow permissions remain read-only.
- V06: CSI runs only for changed register/contracts/cards/coverage views. Its declared file closure is materialized from the selected Git tree into temporary scratch; working-tree repairs cannot satisfy it. The actual shipped checker rejects an invalid selected register.
- V07: independent review and meaningful temporary-repository tests are complete for these candidate bytes. Final staged receipt/status and the coordinator's integration commit remain the coordinator's terminal checks; no application suites were reopened.

## Independently executed checks

- `python3 -m unittest discover -s tests -p 'test_build_checkpoint*.py' -v`: 38 passed, exit 0 (30 validator + then-current 8 hook tests).
- `python3 -m unittest discover -s tests -p test_checkpoint_ci.py -v`: 22 passed, exit 0, including real-validator integrations.
- After the final validator changes, both real-validator CI integration tests passed independently again, exit 0.
- After the object-replacement loader repair: `python3 -m unittest discover -s tests -p test_build_checkpoint_hooks.py -v`: 9 passed, exit 0.
- After the final two explicit scope additions: `PYTHONPATH=tests python3 -m unittest test_build_checkpoint.CheckpointTests.test_existing_configuration_and_instruction_surfaces_are_protected -v`: passed, exit 0.

The unchanged broad checks were reused; the final deltas received scoped checks. All reviewer processes terminated. Only this review file was written by the reviewer; no implementation, test, status, Git configuration or commit mutations were performed by this reviewer.

## Reviewed file identities

| Path | SHA-256 |
| --- | --- |
| `.githooks/pre-commit` | `440ee1ef17a27d7fd204d7904fafaedc55577d961126f6c04d0a81c811c4f12d` |
| `scripts/install-build-hooks.py` | `afa8779b562a14cf45f989da3c22617151cb4858c59cec832df9ed8ffde26888` |
| `scripts/verify-build-checkpoint.py` | `049790f57545fbb4f5ac11db2b20a3fa0c2256e81113436d0a89557dad5f78b2` |
| `tests/test_build_checkpoint.py` | `569a33cd7d62adfbfb9308085e0ae5750cd82055114ec9d8ec90060be8e666ba` |
| `tests/test_build_checkpoint_hooks.py` | `392b32c9fa9a3a6861201c6b3bc5bb161537e839896e02778a50d15447d05b70` |
| `tools/ci/check_build_checkpoints.py` | `8d1f054f81a35f328b9de884b18d25c3fea1d340e971f9cddb04836108bf730c` |
| `tests/test_checkpoint_ci.py` | `d5d92c6940c08ad9a15e84b3f704eb81d823bb00199ce2498ed8c6d784add6da` |
| `.github/workflows/ci.yml` | `e583060f6d8be862f7d01ba753c3732e60fadf7da34f75b6ba315a1444686fe3` |
| `docs/operations/build-checkpoints.md` | `4f51e0f767a83dd889d17db2f120ce1514a74fc99be4cbd9457750a7f9547435` |
| `AGENTS.md` | `02f3b73c6f6e18367d055b9796f4ee0f177cf295a46c6fc5f9fb36ef4c4d039a` |

## Limits

Local hooks remain bypassable and require installation per clone. CI publication and branch-protection settings are outside this local setup. The guard cannot infer undisclosed decisions, establish mechanical truth, approve its own evidence, schedule workers or preserve discipline automatically. Future new executable/configuration locations need explicit trigger coverage. The product queue remains EVIDENCE-PDF-1, Mac first, with the full CSI section lineup preserved.

## Final staged checkpoint review

Reviewed the staged `CURRENT_STATUS.md` and canonical receipt after the coordinator staged exactly 20 owned paths. The receipt's task/outcome/parent and next fields match the visible queue and its single checkpoint block. The completion summary accurately distinguishes an installed local hook and committed CI definition from unpublished remote CI. The full CSI scope, Mac-first delivery, accepted equipment/duct/air software, unresolved recognition/section gates and EVIDENCE-PDF-1 next action are preserved. No unrelated `SKILLS.md` or drawing-workspace edits are staged.

The staged guard returned exit 0, and `git diff --cached --check` returned exit 0. The coordinator's retained unit log records 190 passing top-level tests with exit 0, including the 61 guard cases; the installation record identifies the local hook configuration and concrete real-Git red/green experiments. Reviewed implementation hashes remain those in the table above.

**Independent acceptance: V01–V07 candidate requirements satisfied; no remaining blocking findings.** The final coordinator action is to refresh this report's hash/manifest entries, restage, commit through the actual hook and verify the resulting committed range. That metadata refresh does not require replaying unchanged test suites. The integration commit and post-commit receipt are not claimed as already performed by this reviewer. This report intentionally does not pin itself or the canonical receipt, avoiding a hash cycle.

## CI-RETAINED-LINKS-1 scoped follow-up acceptance

The required existing repository checker exposed 56 relative-link failures in retained documents and four prose-rule failures after the guard candidate had passed. This follow-up does not reopen the accepted checkpoint validator, hook or range dispatcher. Its frozen R01/R02 scope is recorded in `ci-compatibility-repair.md`.

**R01 and R02 accepted; no remaining blocking findings.** The four origin mappings were independently checked against their retained hashes. Both historical status files exactly match their claimed Git objects. The historical roadmap's original location and retained hash match the existing archive manifest without asserting original-commit equality. The approved air-device packet matches its existing rule-binding hash; its unchanged relative references and comparison with the original spec location support the recorded base. The current source files are not falsely required or claimed to equal their retained copies.

The checker only applies a mapping to exact retained bytes, validates every registry entry, rejects unsafe/symlink/nonregular paths, malformed mappings and duplicate retained identities, and still checks each relative link. A removed target fails even for a mapped copy. Unmapped live documents retain ordinary relative resolution. Registry, JSON, prose and external-submission checks remain in the main pipeline. The temporary schema mismatch between root registry and worker loader was reconciled to exact `schema_version` and `documents` fields before acceptance. No broad archive skip was introduced.

The roadmap changes replace prohibited dash punctuation and remove the obsolete immediate air-device assignment. They preserve settled rules, full Division 23 scope, acceptance distinctions and the live-status queue. The four retained document bodies have no working diff; no approved calculation packet changed.

Independent checks on the final child candidate:

- `python3 -m unittest discover -s tests -p test_ci_checks.py -v`: 23 tests passed, exit 0. Printed one-failure output belongs to the deliberate negative fixture.
- `python3 tools/ci/checks.py`: 958 tracked files checked, zero failures, exit 0.
- No checkpoint guard or product calculation suites were repeated. The earlier 190-test run remains historical evidence, not a claim that a later enlarged full suite was rerun.

Reviewed final child file identities:

| Path | SHA-256 |
| --- | --- |
| `tools/ci/checks.py` | `c02306c3a2d5ac929a95a9a672f2ea96247abda2d53673c398c415d9cf7f855e` |
| `tests/test_ci_checks.py` | `994320844cff4f236a2d0364f61f93d55579a06a580e92593ec4a72c810b9346` |
| `docs/operations/retained-document-origins.json` | `c148441cea91cfb3880c0ed039a7cefcfca8221cce3974fe251ef4c5fd61a79e` |
| `docs/roadmap.md` | `38faf318739abdd9cb7b11b7a883be00c030bbbbcb58a9487add3f118e4148e2` |

Coordinator updates status and the checkpoint manifest with this accepted child, then performs the already recorded integration action. All reviewer processes are terminal; this review file is the only path changed by this reviewer.
