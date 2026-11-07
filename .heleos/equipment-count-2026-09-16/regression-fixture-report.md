# EQUIPMENT-COUNT-1 regression fixture repair

Worker: `/root/equipment_regression_fixtures`. Checkout: `/Users/bekim/Heleos-spark`, branch `main`, base/HEAD `05c55645fdf01a3238c13097c2f100dfa653799a`. Task: repair pre-existing fixture drift exposed by the controller's 1,260-test run. Production files were read-only. No commits or external submissions were made.

## Diagnosis and pre-existing evidence

The original failures are retained in `python-suite.log`: one obsolete document pipeline version assertion and nine workspace tests rejected before reaching their intended behavior because the fake Foundation CLI omitted required coordinate metadata.

- `scripts/document_pipeline.py` is byte-identical to the base (SHA-256 `c2f62881865039655ad0f28d9c196269ca59eca01da850658edb59cf720bbc2f`), already declaring `document-reading-4`.
- `scripts/sheet_geometry.py` is byte-identical to the base (SHA-256 `6bc7c42a7fa4263bef77525ca9c726ad27b1dc068947471fabf659c62f5f1d7a`). It requires paper unit `pt`, a revision-matching `parent_content_sha256`, and the complete Foundation transform.
- `tests/drawing-workspace/test_drawing_workspace.py` was byte-identical to the base before this repair (SHA-256 `c4dbb3ff4a399743042253ebbf4095cc12439fdf4a8c3c94b777483e6b49158a`). Its fake CLI did not provide these fields; its renderer argument expectation also predated `-cropbox`.
- `scripts/drawing-workspace.py` is concurrently changed by the controller for the equipment UI asset, so whole-file identity is not claimed. The relevant `state()` and `rendered_page()` method text is exactly equal to the committed base. Method SHA-256 values are respectively `73cf2c88bc3375e9b491814e30543c379e2cc771b5cc55e0bcb50cc951a780c0` and `035d0a573ecded411eb74f09a0c7baeba37639dd95bc259ec412d95f97cce665`.
- `tests/drawing-workspace/test_document_layout_geometry.py` was already untracked at assignment start. It cannot be represented as committed base content. Its pre-edit SHA-256 was `1ee900b4b065861c9018066c1f48a3a65ac78a8d732146ac3364b8706383eba2`; only its obsolete version assertion was changed.

## Candidate changes

1. Correct the literal pipeline expectation to the existing `document-reading-4` contract.
2. Supply a complete, mathematically consistent zero-rotation, letter-page Foundation transform in the fake CLI: PDF bottom-left points map to display top-left using `(1, 0, 0, -1, 0, 792000000)` micropoints. Preserve the real revision digest as the parent identity.
3. Assert those coordinate fields survive into the HTTP document state.
4. Include the existing `-cropbox` flag in the exact renderer argument assertion.

No production behavior or error expectations were weakened. Membership, original byte/hash verification, corruption rejection before renderer invocation, quarantine retention, renderer timeout/failure, invalid/oversized output, host/origin/token boundaries, and reopen behavior retain their original assertions.

## Verification

- `python3 -m unittest discover -s tests/drawing-workspace -p test_document_layout_geometry.py -v`: exit 0, 11 tests passed in 0.047s.
- `python3 -m unittest discover -s tests/drawing-workspace -p test_drawing_workspace.py -v`: exit 0, 15 tests passed in 4.461s.
- `git diff --check -- tests/drawing-workspace/test_drawing_workspace.py`: exit 0.

No full-suite rerun was performed by this worker. Controller independent review/checks remain required.

Final candidate SHA-256 values:

| Path | SHA-256 |
| --- | --- |
| `tests/drawing-workspace/test_document_layout_geometry.py` | `98be7e0a64306fea7508e7e75ac0b9c17740bf46fcc83ce24fb527f2770fd9a0` |
| `tests/drawing-workspace/test_drawing_workspace.py` | `cd6edc5ccb1b8465ebfe4ac802542747d6affb5a69dda7298dec9ff1c856bcbd` |

Terminal state: bounded fixture repair finished; no worker process is running. Next action: controller reviews the two candidate diffs and independently runs the affected modules. The pre-existing untracked geometry test must remain distinguished from newly created product implementation when selecting completion-commit paths.

Per controller direction, leave the entire pre-existing untracked geometry test uncommitted. Its only patch, sufficient to reconstruct the original bytes and check the pre-edit hash, is:

```diff
--- tests/drawing-workspace/test_document_layout_geometry.py (pre-existing untracked)
+++ tests/drawing-workspace/test_document_layout_geometry.py (local correction)
@@
-        self.assertEqual(run["version"], "document-reading-3")
+        self.assertEqual(run["version"], "document-reading-4")
```
