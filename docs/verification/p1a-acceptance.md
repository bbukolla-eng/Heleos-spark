# P1A clean-wheel acceptance

## Scope

This acceptance proves that a clean installation can create a blank SQLite
database, build a real P0 project/actor/document-revision/frozen-baseline from
supplied metadata, and run the shipped HELIOS Baseline Auditor through the
actual bounded subprocess path. It accepts frozen manifest metadata auditing
only—not PDF ingestion, drawing interpretation, takeoff, pricing, an external
provider, or a real project packet. P1A writes no P0 bid-impacting facts.

## Installed command

Acceptance is packaged code, not a checkout-only example:

```bash
helios-p1a acceptance --work-root <fresh-directory>
```

The command emits one machine-readable JSON object. A success includes
`"acceptance":"P1A_BASELINE_AUDIT_SUCCEEDED"`, the exact event sequence,
attempt count, purpose-keyed artifact IDs/digests/verification, a non-null
`report_artifact_id`, report content, and all 24 P0 before/after table counts.

## Clean install gate

The release gate uses fresh temporary wheel, virtual-environment, and work
directories and runs this sequence once after substantive changes:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m compileall -q src examples
python3 -m pip wheel --no-index --no-deps --no-build-isolation --wheel-dir <wheel-dir> .
python3 -m venv <venv>
<venv>/bin/pip install --no-index --no-deps <wheel>
<venv>/bin/helios-p1a --help
<venv>/bin/helios-p1a acceptance --work-root <fresh-work-root>
python3 -m json.tool docs/openapi/p1a-openapi.yaml
git diff --check
```

No example, script, document, or source checkout is copied into the clean
environment. The installed console entrypoint must provide every acceptance
resource itself.

## What is proved

- The exact seven-worker roster is present; only the built-in auditor is
  statically available.
- One real subprocess attempt reaches
  `QUEUED → LEASED → STARTED → SUCCEEDED`; a second `run_once()` returns `null`.
- The completed projection exposes worker and exact adapter metadata,
  timestamped events, attempts, purpose-keyed artifacts, and report artifact ID.
- `INPUT`, `STDOUT`, `STDERR`, and `REPORT` bytes hash-verify from SQLite
  metadata and the CAS.
- All 24 P0 bid-impacting table counts are identical before and after.
- External workers execute only after a real preflight receipt for their exact
  normalized configuration hash. The built-in acceptance does not claim an
  external provider integration.

## Failure and recovery acceptance

Focused regressions additionally prove that two baselines can reference the
same physical CAS digest without cross-baseline metadata conflict; one
unavailable ordered candidate blocks one `run_once` without consuming the next;
output capture is capped; timeout kills descendants; and a restored store can
reconcile an orphaned `STARTED` attempt only after a human actor positively
asserts process death. Reconciliation persists outcome-unknown artifacts and an
audited terminal `ESCALATED`, never success.

Configured external workers are trusted local executables, not OS-sandboxed
programs. Secure artifact storage requires POSIX directory-descriptor and
no-follow semantics and has no unsafe fallback. P1B still requires a
permissioned real M-drawing/spec packet.
