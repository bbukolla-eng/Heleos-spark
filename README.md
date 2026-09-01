# HELIOS Takeoff Core

Local-first, evidence-bound P0 foundation with a bounded P1A execution spine for an auditable Division 23 takeoff system.

It preserves the path from immutable bid documents to evidence, quantity assertions, reviewer decisions, approved takeoffs, quote inputs, estimates, and as-bid releases. It does **not** yet perform PDF/CAD extraction, AI vision, topology inference, labor assemblies, or live vendor integration.

P1A can run one real HELIOS Baseline Auditor subprocess against the metadata manifest of a frozen P0 revision set. That audit checks manifest completeness and metadata lineage only. It is **not** PDF ingestion, drawing interpretation, takeoff, pricing, external-provider acceptance, or bid authority.

P1B adds strict, immutable Division 23 definition packs and a deterministic rule/assembly kernel. It evaluates only supplied observations and evidence, and it cannot write project quantities, pricing, approvals, releases, or P1A execution state.

## Run locally

Python 3.12 is the only runtime requirement.

```bash
cd helios-takeoff-core
PYTHONPATH=src python3 -m helios_takeoff_core.cli init --database ./helios-p0.sqlite3
PYTHONPATH=src python3 -m helios_takeoff_core.cli serve --database ./helios-p0.sqlite3
```

To install the local command instead of running from the checkout:

```bash
python3 -m pip install --no-deps .
helios-p0 init --database ./helios-p0.sqlite3
helios-p0 serve --database ./helios-p0.sqlite3
```

The API binds to `127.0.0.1:8787` by default. Check it with:

```bash
curl http://127.0.0.1:8787/health
```

## Guarantees in P0

- Source revisions, evidence, claims, quantities, takeoff lines, quotes, estimates, approvals, and releases are append-only.
- A frozen revision set cannot accept a new document.
- Each quantity assertion requires evidence from a document in its own frozen baseline.
- Approved takeoffs do not change when an addendum produces a successor baseline.
- Reviews and releases require a stored, active, project-scoped role grant.
- Estimate lines retain the selected quote line, UOM, price, and extended amount.
- Bid release requires approved takeoff and estimate snapshots, non-expired selected quotes, and both authorized-bidder and submitter decisions.

## App integration

Use the versioned `/v1` API and keep client requests idempotent with the `Idempotency-Key` header. Full route contracts are in [docs/openapi/p0-openapi.yaml](docs/openapi/p0-openapi.yaml), and the operator workflow is in [docs/operator/p0-operator-guide.md](docs/operator/p0-operator-guide.md).

The HTTP service is deliberately loopback-only and does **not** expose role-grant or role-revocation commands. Provision trusted actors and grants through a local administrator/integration before a client can submit review or release decisions; do not make this development P0 listener internet-facing.

The planned LangGraph, LangChain, and RAG integration boundary is documented in [docs/architecture/agentic-layer-boundary.md](docs/architecture/agentic-layer-boundary.md). They will propose evidence-backed work through P0; they will not own approvals, quantities, pricing, or bid release.

## Validation

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

For a completely fresh, evidence-to-release demonstration:

```bash
PYTHONPATH=src python3 examples/p0_demo.py --database /tmp/helios-p0-demo.sqlite3
```

The repeatable acceptance record is in [docs/verification/p0-acceptance.md](docs/verification/p0-acceptance.md).

## Run the real P1A baseline audit

Install the wheel/package and run acceptance directly from installed code in a fresh directory:

```bash
python3 -m venv .venv
.venv/bin/pip install --no-deps .
P1A_WORK_ROOT="$(mktemp -d /tmp/helios-p1a-baseline.XXXXXX)"
.venv/bin/helios-p1a acceptance --work-root "$P1A_WORK_ROOT"
```

The installed command creates a real P0 project, actor, supplied document-revision metadata, and frozen revision set; bootstraps the fixed seven-worker roster; submits one audit; and invokes the shipped worker through `AgentRunner.run_once()`. Its machine-readable JSON includes exact lifecycle events, attempt count, purpose-keyed artifact IDs/digests and verification state, `report_artifact_id`, the persisted report, and before/after P0 bid-table counts. It calls `run_once()` a second time and requires JSON `null`, proving there is no hidden worker loop.

The equivalent operator commands, after a real frozen P0 baseline and requester actor have been created, are:

```bash
helios-p1a init --database "$DATABASE"
helios-p1a bootstrap-roster --database "$DATABASE"
helios-p1a preflight \
  --database "$DATABASE" \
  --workers-config "$WORKERS_CONFIG"
helios-p1a submit-baseline-audit \
  --database "$DATABASE" \
  --artifact-root "$ARTIFACT_ROOT" \
  --project-id "$PROJECT_ID" \
  --revision-set-id "$REVISION_SET_ID" \
  --requested-by-actor-id "$ACTOR_ID" \
  --worker-code helios-baseline-auditor
helios-p1a run-once \
  --database "$DATABASE" \
  --artifact-root "$ARTIFACT_ROOT" \
  --attempt-root "$ATTEMPT_ROOT" \
  --workers-config "$WORKERS_CONFIG"
helios-p1a job show --database "$DATABASE" --job-id "$JOB_ID"
helios-p1a artifact verify \
  --database "$DATABASE" \
  --artifact-root "$ARTIFACT_ROOT" \
  --artifact-id "$ARTIFACT_ID"
```

`WORKERS_CONFIG` is strict JSON using protocol `helios.p1a.workers-config/v1`; use an empty `workers` array for built-in-only execution. External entries declare exact execution/preflight argv, adapter revision, timeout, result protocol, and names of host environment variables. P1A stores only the normalized configuration hash—never commands or credential values—and runs an external worker only after a real `AVAILABLE` preflight for that exact adapter hash. These configured processes are trusted local executables, not OS-sandboxed programs. Output capture is capped and timeout termination targets the entire process group.

Codex, Claude, Kimi, Grok, Cursor, and Grokbot remain registered but unavailable until configured and preflighted; no provider command is guessed. Only HELIOS Baseline Auditor is statically available. `run-once` handles exactly the first ordered candidate, including returning its `BLOCKED` outcome without continuing. Orphaned `STARTED` attempts can be terminally reconciled only by `reconcile-attempt` after a human actor positively asserts the process is dead; the audited result is `ESCALATED` / `OUTCOME_UNKNOWN`, never success. Artifact-backed execution requires POSIX directory-descriptor and no-follow filesystem primitives and fails closed when unavailable. P1A has no drawing/spec ingestion, daemon, hidden polling loop, bid-impacting authority, or unsafe storage fallback.

See [docs/operator/p1a-operator-guide.md](docs/operator/p1a-operator-guide.md) for the bounded operator surface and [docs/verification/p1a-acceptance.md](docs/verification/p1a-acceptance.md) for clean-wheel acceptance evidence.

## Run the HELIOS engine

Install the package, then use the finite compact-JSON operator surface:

```bash
helios-engine compile --input ./pack.json --output ./compiled.json
helios-engine import --database ./helios.sqlite3 --input ./pack.json
helios-engine pack show --database ./helios.sqlite3 --pack-code DIV23-HVAC --version 1.0.0
helios-engine item show --database ./helios.sqlite3 --pack-code DIV23-HVAC --version 1.0.0 --item-code CMP-AHU
helios-engine evaluate --database ./helios.sqlite3 --pack-code DIV23-HVAC --version 1.0.0 --input ./context.json
helios-engine assembly resolve --database ./helios.sqlite3 --pack-code DIV23-HVAC --version 1.0.0 --item-code ASM-AHU
```

Evaluation context is exact JSON with `subject_code`, `observations`, and `evidence_kinds`. Missing rule inputs produce explicit blocked results; the engine never invents them.

Run the installed blank-database proof in a fresh directory:

```bash
P1B_WORK_ROOT="$(mktemp -d /tmp/helios-p1b-engine.XXXXXX)"
helios-engine acceptance --work-root "$P1B_WORK_ROOT"
```

The acceptance pack is clearly marked `NON_AUTHORITATIVE_DEMO`. The proof compiles, imports, queries, evaluates, resolves a nested assembly, replays immutable content, rejects a same-version conflict, and reports unchanged P0/P1A authority-table snapshots with zero providers, workers, or background loops.
