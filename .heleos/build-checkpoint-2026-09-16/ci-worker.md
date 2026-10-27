# BUILD-CHECKPOINT-GUARD-1 CI candidate

Worker: Codex checkpoint_ci. Base: db695750d095c6845f0689e3cc4da79d09333935.
Checkout: /Users/bekim/Heleos-spark, main. Candidate only; no commit or remote changes by this worker.

Assigned sole write paths: tools/ci/check_build_checkpoints.py, tests/test_checkpoint_ci.py, .github/workflows/ci.yml, and this report. Existing unrelated work preserved. Shared contract and public primary-source research packet read before implementation. No external provider submission. Actual local execution used the authorized Codex fallback; provider unavailability did not block this independent work.

## Behavior

- The CLI consumes either both explicit full base/head SHA values or the push/pull_request event JSON. It does not interpolate event values into shell commands or use a PR's synthetic merge SHA.
- Every newly reachable ordinary commit after the pinned adoption baseline is checked separately, in parent-first order, against the shared validator. Later valid checkpoints cannot hide earlier failures. Guard deletion from a target tree does not exempt that commit.
- Missing commit objects, incomplete event fields, unsupported events and absent local history fail clearly. The adapter neither fetches nor changes the index/worktree.
- A zero-before branch creation uses the baseline. A branch that cannot establish adopted-baseline ancestry needs a separately defined import/reconciliation policy and fails explicitly. Historical ancestors of the baseline remain grandfathered.
- Two-parent merges run Git's real remerge diff with binary output, no external diff or text conversion. Empty automatic merge differences reuse the validated parent work. Conflict-resolution or other novel merge differences invoke the checkpoint validator. Git errors fail; octopus merges are explicitly unsupported.
- The existing read-only GitHub workflow checks out the actual PR head with full history, then validates the complete event range. Existing tests and repository checks remain in place. Standard-library test discovery includes the three new guard test modules once; there is no duplicate guard suite step.

## Verification

Initial red: `python3 -m unittest discover -s tests -p test_checkpoint_ci.py -v` exited 1 because the new adapter did not yet exist.

Final candidate checks:

- `python3 -m unittest discover -s tests -p test_checkpoint_ci.py -v`: exit 0; 22 tests passed. Tests use actual temporary Git histories, including multiple commits, missing checkpoint followed by valid checkpoint, dirty worktree isolation, guard deletion, historical bootstrap, missing/malformed objects, new branches, automatic merges, manual conflict resolutions, nonconflicting novel merge changes, octopus rejection, remerge failure, and event selection. Two tests invoke the real checkpoint validator with full authored progress receipts; graph dispatcher boundary cases use a subprocess fixture whose verdict reads the exact committed tree.
- `python3 -m py_compile tools/ci/check_build_checkpoints.py tests/test_checkpoint_ci.py`: exit 0.
- `git diff --check`: exit 0.

Candidate SHA-256 values:

| Path | SHA-256 |
| --- | --- |
| tools/ci/check_build_checkpoints.py | 8d1f054f81a35f328b9de884b18d25c3fea1d340e971f9cddb04836108bf730c |
| tests/test_checkpoint_ci.py | d5d92c6940c08ad9a15e84b3f704eb81d823bb00199ce2498ed8c6d784add6da |
| .github/workflows/ci.yml | e583060f6d8be862f7d01ba753c3732e60fadf7da34f75b6ba315a1444686fe3 |

Current process state: all worker commands terminal, no background process. Independent review and coordinator verification determine acceptance. No GitHub run, branch protection, automatic commit or automatic push has been claimed or performed.

Next action: coordinator verifies the shared guard candidate, records its acceptance and status transition, installs the local hook, and commits through that hook. Product work then returns to EVIDENCE-PDF-1.
