# BUILD-CHECKPOINT-GUARD-1 validator candidate

Worker: `/root/checkpoint_validator`; Codex fallback authorized because the last
guarded Claude request had expired OAuth and no changed login evidence. No new
Claude request or external submission was made. NotebookLM was unavailable;
the coordinator supplied the contract and verified public Git/Actions packet.

Checkout `/Users/bekim/Heleos-spark`, branch `main`, base
`db695750d095c6845f0689e3cc4da79d09333935`. Read root AGENTS.md and reconciled its
new build-checkpoints instruction during implementation. Sole writes:

- `scripts/verify-build-checkpoint.py`
- `tests/test_build_checkpoint.py`
- this worker report

The validator reads index/tree modes and blob IDs, never working-tree evidence.
It rejects unmerged indexes, malformed receipts, missing or partial checkpoint
staging, manifest mismatch, unsafe/self-proof/untracked evidence, hash drift,
nonregular proof files, inconsistent visible next action and nonadvancing closed
tasks. No receipt command is executed. Complete, in-progress and blocked semantics
match contract v1. Exact parent/empty-tree baselines and no-replacement Git reads
preserve object identity. The CLI requires a full 40-character commit SHA.

The actual CSI card prefix is `docs/superpowers/plans/division23-sections/`.
Changes to that scope or register/contracts/coverage views run the selected-tree
CSI checker in temporary storage containing only its selected input/card closure.
Ordinary changes do not invoke CSI validation. The temporary snapshot never
uses tracked symlinks or dirty local replacements.

Coordinator approved narrow V02 trigger additions after independent review:
`.claude/`, `.codex/`, `governance/`, `deny.toml`, `rustfmt.toml`, `.gitignore`,
`.gitattributes`, `ROADMAP.md`, `SECURITY.md`, the canonical `docs/roadmap.md`,
and active `docs/policies/` instructions. Status-archive exemptions apply
only to `.md`, `.txt` and `.rst` documentation, not code hidden in that folder.

## Verification

1. Initial TDD red: `python3 -m unittest discover -s tests -p test_build_checkpoint.py`
   exited 1; 21 tests, 25 assertion failures because the validator did not exist.
2. First implementation: the same command exited 0; 21 tests passed.
3. Expanded exact-tree/actual-CSI/root-commit checks: same command exited 0;
   25 tests passed.
4. Review regression red: same command exited 1; 30 tests, 13 failures covering
   missed protection surfaces, archive code, full-hash mode and replacement refs.
   The focused coherent whitespace case also reproduced false acceptance:
   `python3 -m unittest discover -s tests -p test_build_checkpoint.py -k disguise`
   exited 1 (validator had incorrectly returned 0).
5. Root identified two additional canonical instruction surfaces; focused test
   `python3 -m unittest discover -s tests -p test_build_checkpoint.py -k existing_configuration`
   exited 1 with exactly those two missing-trigger failures before the scoped fix.
6. Final candidate: `python3 -m unittest discover -s tests -p test_build_checkpoint.py`
   exited 0; 30 tests passed in 6.759 seconds.
7. `python3 -m py_compile scripts/verify-build-checkpoint.py tests/test_build_checkpoint.py`
   exited 0. `git diff --check -- scripts/verify-build-checkpoint.py tests/test_build_checkpoint.py`
   exited 0 (files remain unstaged candidate additions; coordinator must inspect
   the staged diff too).

Final SHA-256:

- validator: `049790f57545fbb4f5ac11db2b20a3fa0c2256e81113436d0a89557dad5f78b2`
- tests: `569a33cd7d62adfbfb9308085e0ae5750cd82055114ec9d8ec90060be8e666ba`

All commands are terminal. No commits or index mutations were made in the real
repository. Git writes in tests were confined to disposable fixture repositories.
Candidate implementation and worker checks are complete; independent acceptance,
hook/CI integration, root status/receipt and completion commit belong to Codex
coordinator. Next action: independently review and run these exact candidate
bytes with hook/CI integration, then return the product queue to EVIDENCE-PDF-1.
