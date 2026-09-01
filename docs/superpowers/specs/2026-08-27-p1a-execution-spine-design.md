# P1A Agent Execution Spine Design

## Purpose

P1A adds the real, durable execution layer that sits beside the immutable P0
takeoff store. It is the bridge through which Codex, Claude, Kimi, Grok,
Cursor, Grokbot, and HELIOS-owned deterministic workers can receive bounded
work and return auditable artifacts. It does not replace the original
multi-agent design and it does not grant an AI worker authority over quantity
approval, pricing, estimate approval, or bid release.

The first runnable P1A capability is **BASELINE_AUDIT**. Given a real frozen
P0 revision set, it builds a content-addressed manifest of the issued
documents, runs a bounded local worker process, validates its report, and
persists its complete trail. This is a useful pre-takeoff control: it finds
missing document classes, documents excluded by a supersession relationship,
and missing issue metadata. It is deliberately not described as drawing
ingestion, OCR, vision, or takeoff; those start in P1B.

## Non-negotiable operating rules

- No hard-coded success, canned agent finding, mock provider receipt, or
  in-memory fallback is a production success path.
- A worker is successful only when a real process finishes with exit code zero,
  emits a schema-valid result, and its content-addressed report artifact is
  persisted and hash-verifies.
- P1A tests protect a shipped transition or a real failure mode. They are
  release gates, not the product loop.
- `run-once` processes at most one work item. It never contains a hidden
  infinite polling/retry loop.
- Automatic retries are finite (default two attempts). Repeating an identical
  failure against unchanged input and adapter hashes produces `ESCALATED`, not
  another retry.
- A missing executable, missing login/configuration, malformed response,
  timeout, or nonzero exit records `BLOCKED`, `FAILED`, or `ESCALATED`. It
  never becomes synthetic work.
- External workers receive only the immutable input artifact and a private
  attempt working directory. Configured host workers are explicitly trusted
  local executables; P1A does not claim an OS sandbox or malicious-code
  boundary. They never receive the P0 SQLite path, P0 credentials, or
  approval/pricing/release commands.
- P1A creates no P0 quantity assertion, review, takeoff, price, estimate, or
  bid release. P1B may later import validated evidence and claims through the
  existing P0 boundary.

## Ownership boundary

| P1A owns | P0 continues to own |
|---|---|
| Worker roster, adapter revisions, availability probes, artifacts, jobs, runs, attempts, events, retry/escalation history | Document revisions, evidence, extraction claims, quantities, approvals, quotes, estimates, and as-bid releases |
| Private-directory subprocess dispatch and output validation | Bid-impacting authority and immutable takeoff history |
| Baseline-audit report artifacts | Any human decision that changes scope, quantities, price, or release state |

P1A reads frozen P0 revision-set facts through a read-only repository helper.
It never repurposes P0 `extraction_runs` as a queue.

## Worker roster and availability

The following remain first-class workers:

| Worker | Intended role | Initial P1A availability |
|---|---|---|
| Codex | Orchestration, implementation review, verification | `UNCONFIGURED` until a real local CLI/API preflight passes |
| Claude | Document/reasoning review | `UNCONFIGURED` until a real local CLI/API preflight passes |
| Kimi | Implementation worker | `UNCONFIGURED` until a real local CLI/API preflight passes |
| Grok | Research and adversarial review | `UNCONFIGURED` until a real local CLI/API preflight passes |
| Cursor | Local IDE/handoff worker | `UNCONFIGURED` until a supported local automation interface passes preflight |
| Grokbot | Operational bot/API handoff | `UNCONFIGURED` until an authorized platform integration passes preflight |
| HELIOS Baseline Auditor | Deterministic baseline integrity worker | `READY` through the shipped, real subprocess protocol |

No provider command is guessed or embedded. A user-controlled worker
configuration declares exact `argv`, preflight command, capability, and result
protocol. It holds no credential value; it may reference names of environment
variables supplied by the host. P1A records only a normalized configuration
hash.

## Durable data model

Migration 009 introduces append-only P1A records:

- `agent_workers` and `agent_worker_capabilities`: immutable identity and
  allowed work capability.
- `agent_adapter_revisions`: immutable adapter type (`BUILTIN` or
  `SUBPROCESS`), protocol version, and configuration hash.
- `agent_worker_availability_events`: immutable worker/adapter preflight
  history; the latest event for the exact adapter revision derives current
  availability.
- `agent_artifacts`: content-addressed local artifact metadata. The database
  stores SHA-256, size, media type, schema version, and a relative store key;
  it never stores the project document bytes.
- `agent_jobs` and `agent_workflow_runs`: an immutable request attached to one
  frozen P0 revision set and an immutable execution of `baseline-audit/v1`.
- `agent_work_items` and `agent_work_item_dependencies`: bounded dispatch
  units and explicit predecessor relationships.
- `agent_execution_attempts`: immutable lease/attempt receipts.
- `agent_work_item_events`: append-only lifecycle events. State is derived
  from the most recent legal event, not overwritten in a mutable status field.

The initial event vocabulary is `QUEUED`, `LEASED`, `STARTED`, `SUCCEEDED`,
`RETRY_SCHEDULED`, `FAILED`, `BLOCKED`, `ESCALATED`, and `CANCELLED`.
Terminal work items reject further events or attempts. SQL triggers enforce
frozen baseline scope, immutable P1A facts, valid attempt count, matching
worker/adapter linkage, and terminal-state locks.

## Subprocess protocol

All runnable workers use one JSON protocol:

1. P1A writes a JSON envelope containing only job/work-item/attempt IDs and
   the input artifact path, digest, media type, and schema version.
2. It starts the configured argv with `shell=False`, a private working
   directory, a finite timeout, capped output capture, a dedicated process
   session/group, and no P0 database path in the environment.
3. The worker writes exactly one JSON result to stdout.
4. P1A records stdout/stderr as artifacts, verifies the result schema and
   input digest, then stores the canonical report as a content-addressed
   artifact.

The built-in `HELIOS Baseline Auditor` is run through the same actual
subprocess protocol (`python -m helios_takeoff_core.agentic.manifest_worker`).
It audits the passed immutable manifest; it does not read a canned fixture.
External tools must use the same protocol, which makes their integration
honest and swappable.

## BASELINE_AUDIT workflow

```text
frozen P0 revision set
  → deterministic manifest artifact
  → durable P1A job/run/work item
  → one actual worker process
  → schema-validated report artifact
  → SUCCEEDED, BLOCKED, FAILED, or ESCALATED audit record
```

The report identifies document totals by type, required types missing from the
baseline, included successor documents whose referenced predecessor is absent
from that same baseline, missing issue dates, and P1A limitations. The field
`superseded_document_references_not_included` is empty when both successor and
predecessor are included. It is a reviewer artifact, not an automatic scope,
exclusion, or bid decision.

## Public operating surface

P1A uses the existing local package and database:

```text
helios-p1a init --database <path>
helios-p1a acceptance --work-root <fresh-path>
helios-p1a bootstrap-roster --database <path>
helios-p1a preflight --database <path> --workers-config <path>
helios-p1a submit-baseline-audit --database <path> --artifact-root <path> \
  --project-id <id> --revision-set-id <id> --requested-by-actor-id <id> \
  --worker-code helios-baseline-auditor
helios-p1a run-once --database <path> --artifact-root <path> --attempt-root <path> \
  --workers-config <path>
helios-p1a reconcile-attempt --database <path> --artifact-root <path> \
  --attempt-id <id> --operator-actor-id <id> --reason <text> --assert-process-dead
helios-p1a job show --database <path> --job-id <id>
helios-p1a artifact verify --database <path> --artifact-root <path> --artifact-id <id>
```

The loopback API adds only bounded job creation and read-only status/artifact
inspection routes. Raw worker completion routes are intentionally absent; the
local runner owns protocol validation and persistence.

## Acceptance gates

P1A is complete only when all of these are verified from a blank installed
package/database:

1. A frozen P0 baseline creates a real P1A manifest artifact and job.
2. The built-in worker executes as an OS subprocess and produces a
   hash-verified baseline-audit report.
3. The job record shows worker, adapter, input/output digests, attempt, timing,
   stdout/stderr artifacts, and terminal state.
4. A malformed worker output, missing command, and repeat-identical failure
   become non-success states and do not create fake P0 data.
5. Tampering with a stored artifact is detected by `artifact verify`.
6. A configured worker is dispatchable only after a successful real preflight
   for its exact adapter revision.
7. The P0 quantity, approval, pricing, estimate, and bid-release tables remain
   unchanged through P1A execution.
8. The package provides a machine-readable acceptance record and a tested
   handoff command. A permissioned real project packet remains the required
   operational proof for P1B.

## Explicit non-goals for P1A

- PDF/CAD file vault, OCR, vector/raster extraction, drawing geometry,
  schedule parsing, topology, or quantity measurement.
- Fake provider connectors or provider-specific command guesses.
- Hosted queues, cloud execution, n8n, CrewAI, LangGraph, LangChain, or RAG
  dependencies. They attach to this durable spine later; none may become the
  source of truth.
- Autonomous quantity approval, pricing, proposal generation, or bid release.
