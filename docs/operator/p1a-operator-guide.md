# P1A baseline-audit operator guide

P1A runs one metadata-only audit of a frozen P0 revision set through a real,
bounded subprocess. It does not inspect drawing contents, create quantities,
approve takeoff or estimates, set prices, or release bids.

## Local setup and roster

All commands write one compact JSON value to stdout. Runtime errors also return
JSON and a nonzero exit status.

```bash
helios-p1a init --database /srv/helios/helios.sqlite3
helios-p1a bootstrap-roster --database /srv/helios/helios.sqlite3
```

`init` returns `{"status":"INITIALIZED", ...}`. `bootstrap-roster` returns
`{"status":"BOOTSTRAPPED","workers":{...}}` with exactly Codex, Claude,
Kimi, Grok, Cursor, Grokbot, and the HELIOS Baseline Auditor.

Create a strict host-controlled worker configuration even when only the built-in
worker is used:

```json
{"protocol":"helios.p1a.workers-config/v1","workers":[]}
```

Each external entry has exactly these fields: `worker_code`, `adapter_name`,
`adapter_kind` (`SUBPROCESS`), `adapter_revision`, `capability`
(`BASELINE_AUDIT`), `result_protocol` (`helios.p1a.worker-result/v1`), `argv`,
`preflight_argv`, `timeout_seconds` (1–300), and `environment_variables` (names
only). Commands and environment values are never written to SQLite. P1A stores
only the normalized configuration SHA-256.

## Submit and execute one item

The project, actor, and frozen revision set must already exist in the P0
database. Submit the built-in worker with:

```bash
helios-p1a submit-baseline-audit \
  --database /srv/helios/helios.sqlite3 \
  --artifact-root /srv/helios/p1a-artifacts \
  --project-id PROJECT_ID \
  --revision-set-id REVISION_SET_ID \
  --requested-by-actor-id ACTOR_ID \
  --worker-code helios-baseline-auditor
```

The result includes `job_id`, `workflow_run_id`, `work_item_id`, immutable
input-artifact metadata, and `"state":"QUEUED"`.

Run one eligible item:

```bash
helios-p1a run-once \
  --database /srv/helios/helios.sqlite3 \
  --artifact-root /srv/helios/p1a-artifacts \
  --attempt-root /srv/helios/p1a-attempts \
  --workers-config /etc/helios/p1a-workers.json
```

A valid built-in audit returns an outcome containing
`"state":"SUCCEEDED"`, `"reason_code":"WORKER_COMPLETED"`, and the real
execution-attempt identity. `run-once` selects exactly one ordered candidate and
exits. If that candidate is unavailable, it returns its structured `BLOCKED`
outcome and does not continue to a later candidate. It is not a daemon and
contains no polling loop. When no item is eligible, it prints JSON `null`.

Inspect the append-only job state and lineage:

```bash
helios-p1a job show \
  --database /srv/helios/helios.sqlite3 \
  --job-id JOB_ID
```

The projection includes worker code/name; exact adapter name/kind/revision and
configuration hash; timestamped events; attempts; purpose-keyed artifact IDs,
digests, media types, and schemas; and `report_artifact_id` on success. Normal
state progress is `QUEUED`, `LEASED`, `STARTED`, then `SUCCEEDED`.
Honest non-success states include `BLOCKED`, `RETRY_SCHEDULED`, and
`ESCALATED`; they are not converted to synthetic success.

## Artifact integrity and tamper response

```bash
helios-p1a artifact verify \
  --database /srv/helios/helios.sqlite3 \
  --artifact-root /srv/helios/p1a-artifacts \
  --artifact-id ARTIFACT_ID
```

The command reconstructs the immutable SHA-256, byte size, media type, schema
version, and content-addressed store key from SQLite. Matching bytes return
`"valid":true,"integrity_status":"VERIFIED"` with exit status 0. Missing,
modified, replaced, or unsafe bytes return
`"valid":false,"integrity_status":"TAMPERED"` with exit status 1. Stop using
the affected job output and restore or investigate the artifact store; never
relabel tampered bytes as a successful report.

## Loopback API

The embedding server configures the artifact root when it creates the WSGI app;
clients never submit raw filesystem paths. Every POST continues to require
`Content-Type: application/json` and an `Idempotency-Key` header.

- `POST /v1/agent-jobs` accepts `project_id`, `revision_set_id`,
  `requested_by_actor_id`, and `worker_code`.
- `GET /v1/agent-jobs/{job_id}` reads state, events, and immutable lineage.
- `GET /v1/agent-artifacts/{artifact_id}` reads database metadata and reports
  `VERIFIED` or `TAMPERED`; it never accepts or returns an arbitrary path.

If the WSGI app was created without a server-owned `artifact_root`, artifact
inspection returns HTTP `409 Conflict` with error code `PRECONDITION_FAILED`.

There are no P1A routes for raw worker completion, success, retry, quantity or
takeoff approval, pricing, estimate approval, or bid release. The API does not
start a runner or daemon.

## Provider onboarding status

Only `helios-baseline-auditor` is statically `AVAILABLE`. External workers start
`UNCONFIGURED`/`UNAVAILABLE`. To onboard an exact user-supplied adapter:

```bash
helios-p1a preflight \
  --database /srv/helios/helios.sqlite3 \
  --workers-config /etc/helios/p1a-workers.json
```

P1A runs the configured `preflight_argv` as a real subprocess with a finite
timeout, registers and assigns its exact immutable adapter revision, and appends
an availability receipt for that same adapter/configuration hash. `run-once`
executes external `argv` only when the claimed adapter fields and normalized
hash match the supplied configuration and that exact adapter's latest receipt
is `AVAILABLE`. No provider command or login is guessed. Named host environment
values may be passed to the child but are neither emitted in receipts nor
stored in the database.

Configured host workers are explicitly trusted local executables. P1A gives
them a private attempt working directory and a constructed environment, but it
does not provide an OS sandbox, container, user-namespace boundary, or defense
against a malicious executable. Stdout/stderr capture is capped at 1 MiB per
stream. Every child starts in its own process session; timeout handling signals
and then kills the process group so descendants do not survive the attempt.

## Crash and artifact-store recovery

If the process crashes after a durable `STARTED` event, or the artifact store is
unavailable while mandatory outcome artifacts are being written, the attempt
remains visibly `STARTED`. Restore and verify the artifact store, positively
confirm from the host process table that the worker and descendants are dead,
then reconcile exactly that attempt:

```bash
helios-p1a reconcile-attempt \
  --database /srv/helios/helios.sqlite3 \
  --artifact-root /srv/helios/p1a-artifacts \
  --attempt-id ATTEMPT_ID \
  --operator-actor-id ACTOR_ID \
  --reason "Confirmed process death from the host process table after storage recovery." \
  --assert-process-dead
```

Without the explicit assertion the command fails and writes no terminal event.
With it, P1A persists outcome-unknown `STDOUT`, `STDERR`, and `ERROR` artifacts
and appends terminal `ESCALATED` / `OUTCOME_UNKNOWN` with the immutable operator
actor, reason, and assertion. Reconciliation never fabricates success.

## Baseline-audit supersession semantics

`superseded_document_references_not_included` lists an included successor whose
`supersedes_document_revision_id` points to a predecessor absent from the same
frozen baseline. If both successor and predecessor are included, the pair is
not listed. The field is a baseline-completeness audit fact, not a decision to
exclude or replace documents.

## Secure-storage platform limitation

P1A artifact storage currently requires POSIX directory-descriptor and
no-follow filesystem primitives (`O_DIRECTORY`, `O_NOFOLLOW`, `O_CLOEXEC`, and
`dir_fd` operations), as provided on supported macOS/Linux filesystems. On a
platform without those primitives, artifact setup fails closed. There is no
unsafe fallback; P0 database operations remain separate from this P1A storage
limitation.
