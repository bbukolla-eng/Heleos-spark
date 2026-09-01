# P1B engine foundation acceptance

**Verified code commit:** `dfac38f8b019b200bc2c60c3a76126a416176aa2`

Final evidence was collected from a clean `feat/p1b-engine-foundation` worktree in a fresh workspace-owned directory. This supersedes the earlier pre-review gate recorded for `340f8d9`.

## Installed-package proof

```bash
VERIFY_ROOT=/workspace/scratch/b1fae67c6a97/p1b-engine-final2.EP0Xvg
mkdir -p "$VERIFY_ROOT/wheel" "$VERIFY_ROOT/tmp"
TMPDIR="$VERIFY_ROOT/tmp" PIP_NO_INDEX=1 python3 -m pip wheel \
  --no-deps --no-build-isolation . --wheel-dir "$VERIFY_ROOT/wheel"
TMPDIR="$VERIFY_ROOT/tmp" python3 -m venv "$VERIFY_ROOT/venv"
PIP_NO_INDEX=1 "$VERIFY_ROOT/venv/bin/python" -m pip install --no-deps \
  "$VERIFY_ROOT/wheel/helios_takeoff_core-0.1.0-py3-none-any.whl"
"$VERIFY_ROOT/venv/bin/helios-engine" --help
"$VERIFY_ROOT/venv/bin/helios-engine" acceptance \
  --work-root "$VERIFY_ROOT/acceptance-work-root"
```

The fresh wheel built and installed without network dependency resolution. Its SHA-256 was `732d5b11aa813e0ba85c11c10ba4bec61bb34c4c7a690d51290a6ba9a63c3818`. Installed `helios-engine --help` exposed only the finite `compile`, `import`, `pack`, `item`, `evaluate`, `assembly`, and `acceptance` commands.

Installed acceptance returned `P1B_ENGINE_FOUNDATION_SUCCEEDED` and proved:

- deterministic compilation with domain-pack digest `cbb6545bef2bf7762888de66e8e57cc5b9b16af590605ea839249c30133edc3e`;
- one immutable pack identity across exact replay and rejection of conflicting same-version content;
- exact catalog and relation queries;
- ready rule `A-READY-RULE` and explicitly blocked rule `Z-BLOCKED-RULE`, including output lineage and missing inputs;
- the exact four-item assembly rooted at `ASM-ROOT`;
- zero P1A worker-attempt delta;
- all 24 P0 bid-impacting tables and all 13 P1A execution tables unchanged, with zero rows before and after.

The database additionally requires the exact strict-compiler JSON and matching SHA-256 at the append-only parent seal. It validates bidirectional agreement with all normalized projections, uses deferred foreign keys for atomic parent-last assembly, stores all projection tables `WITHOUT ROWID`, and fails closed when the canonical validator is unavailable.

## Branch gates

```bash
TMPDIR="$VERIFY_ROOT/tmp" python3 -m compileall -q src tests examples
TMPDIR="$VERIFY_ROOT/tmp" PYTHONPATH=src python3 -m unittest discover -s tests
git diff --check
git status --short
git rev-parse HEAD
```

Results:

- source, tests, and examples compiled with exit code 0;
- the complete suite ran 141 tests in 14.844 seconds and returned `OK`;
- `git diff --check` returned no errors;
- `git status --short` returned no entries before this evidence update;
- `git rev-parse HEAD` returned the verified code commit above.

Two independent whole-tree/adversarial reviews returned `READY` after the persistence corrections.

## Scope limits

This proves the deterministic reusable engine-definition foundation and pure rule/assembly evaluation. It does not claim project drawing or specification ingestion, project quantities, pricing, approval or bid release, external provider execution, worker execution, or background processing. ATHENA, NotebookLM, and external AI builders remain outside the HELIOS runtime.
