# P1A new-chat handoff

Copy this entire file into a new implementation chat.

## Repository identity

- Repository: `/workspace/scratch/acbbff425cf0/helios-takeoff-core`
- Branch: `feat/p1a-execution-spine`
- Package: `helios-takeoff-core` version `0.1.0`
- Reviewed Task 6 base: `95fc0ec`
- Task 6 implementation/report commits follow that base. The committed document cannot truthfully name its own final commit; the controller must provide the verified final `git rev-parse HEAD` in the user-facing handoff.

## Architecture lock

Preserve the original named multi-agent architecture: **Codex, Claude, Kimi, Grok, Cursor, and Grokbot**. They are first-class P1A workers but start `UNAVAILABLE`/`UNCONFIGURED` until an operator supplies a strict host configuration and an actual finite preflight succeeds for the exact immutable adapter/configuration hash. Do not guess commands, fabricate receipts, or silently substitute the built-in worker for them.

HELIOS Baseline Auditor is the only currently `AVAILABLE` worker. It is a deterministic local subprocess using the same strict worker-result protocol and content-addressed artifact trail required of future adapters.

## Ownership boundary

P0 remains the sole authority for projects, actors, document revisions and frozen baselines, evidence, extraction claims, quantities, reviews, takeoff snapshots and approvals, suppliers and quotes, estimate snapshots and approvals, and bid releases. P0 facts are append-only according to the existing migrations and service rules.

P1A owns the worker roster, immutable adapter revisions and availability events, content-addressed artifact metadata, jobs, workflow runs, bounded work items, attempts, lifecycle events, retry/escalation history, real trusted-local subprocess dispatch, strict result validation, and baseline-audit reports. P1A reads a frozen P0 manifest; it does not write P0 bid-impacting facts or repurpose P0 extraction runs as a queue.

## Real capability now

P1A can take supplied metadata, create a real blank P0 baseline, persist its immutable manifest, submit one `BASELINE_AUDIT`, run `python -m helios_takeoff_core.agentic.manifest_worker` as an actual bounded OS subprocess through `AgentRunner.run_once()`, validate stdout and lineage, and persist hash-verifying `INPUT`, `STDOUT`, `STDERR`, and `REPORT` artifacts. The exact success progression is `QUEUED → LEASED → STARTED → SUCCEEDED`, with one attempt; a second `run_once()` returns `None`.

Run acceptance directly from the installed package:

```bash
cd /workspace/scratch/acbbff425cf0/helios-takeoff-core
python3 -m venv .venv
.venv/bin/pip install --no-deps .
P1A_WORK_ROOT="$(mktemp -d /tmp/helios-p1a-baseline.XXXXXX)"
.venv/bin/helios-p1a acceptance --work-root "$P1A_WORK_ROOT"
```

Run the operator surface against an already-created real frozen baseline:

```bash
helios-p1a init --database "$DATABASE"
helios-p1a bootstrap-roster --database "$DATABASE"
helios-p1a preflight --database "$DATABASE" --workers-config "$WORKERS_CONFIG"
helios-p1a submit-baseline-audit --database "$DATABASE" --artifact-root "$ARTIFACT_ROOT" \
  --project-id "$PROJECT_ID" --revision-set-id "$REVISION_SET_ID" \
  --requested-by-actor-id "$ACTOR_ID" --worker-code helios-baseline-auditor
helios-p1a run-once --database "$DATABASE" --artifact-root "$ARTIFACT_ROOT" \
  --attempt-root "$ATTEMPT_ROOT" --workers-config "$WORKERS_CONFIG"
helios-p1a job show --database "$DATABASE" --job-id "$JOB_ID"
helios-p1a artifact verify --database "$DATABASE" --artifact-root "$ARTIFACT_ROOT" \
  --artifact-id "$ARTIFACT_ID"
```

The loopback-only P1A API exposes exactly `POST /v1/agent-jobs`, `GET /v1/agent-jobs/{job_id}`, and `GET /v1/agent-artifacts/{artifact_id}`. It exposes no worker-completion or P0 authority route.

## Acceptance evidence

Installed acceptance lives in `helios_takeoff_core.agentic.acceptance`; the example is only a compatibility wrapper. The test independently queries the blank database, checks the exact roster and event sequence, proves one attempt/no hidden loop, reconstructs and verifies all four success artifacts, requires a public `report_artifact_id`, parses the stored report, and proves every enumerated P0 bid-impacting table count is unchanged and zero.

The clean-package gate builds a fresh wheel without network dependency resolution, installs it into a fresh virtual environment, checks installed `helios-p1a --help`, executes installed `helios-p1a acceptance` without a copied checkout file, parses OpenAPI, compiles source/examples, runs the full test suite, and checks the diff.

## Honest blockers and limitations

- The accepted report audits **frozen revision-set manifest metadata only**. It is not PDF ingestion, drawing interpretation, takeoff, pricing, or external-provider acceptance.
- Codex, Claude, Kimi, Grok, Cursor, and Grokbot have no built-in provider command. Operators may supply trusted local executables, but execution remains disabled until exact real preflight succeeds.
- Configured workers are trusted local executables, not OS-sandboxed programs. Capture is capped, and timeout cleanup terminates the process group.
- A storage outage or crash can leave a truthful orphaned `STARTED` attempt. After restoring storage and positively confirming process death, `reconcile-attempt` records outcome-unknown artifacts and terminal audited `ESCALATED`; it never invents success.
- Artifact-backed execution requires macOS/Linux POSIX directory-descriptor, `O_NOFOLLOW`, and related secure filesystem primitives. It fails closed on unsupported storage/platforms and has no unsafe pathname fallback.
- There is no daemon, polling loop, hidden retry loop, hosted queue, RAG, LangGraph/LangChain execution layer, autonomous quantity approval, or bid-release authority.
- P1A acceptance is not a real project takeoff and must never be reported as one.

## P1A rulings and cost if wrong

- **Separate append-only P1A ledger beside P0.** If wrong, worker orchestration could corrupt or masquerade as bid authority.
- **Success requires a real process plus exact schema/lineage validation and verified artifacts.** If wrong, canned or tampered output could be called accepted work.
- **One-item `run_once()` and finite retry/escalation.** If wrong, hidden loops could duplicate work, spend, and audit events.
- **External adapters remain unavailable until exact real configuration and preflight.** If wrong, provider names could be mistaken for functioning integrations.
- **Secure POSIX storage fails closed.** If wrong, symlink/path races could break artifact identity and containment; the tradeoff is no P1A artifact runner on unsupported platforms.
- **P1A report is advisory metadata review only.** If wrong, an estimator could mistake missing-document hints for interpreted scope, measured quantities, or a priced bid.

## Instructions for the next chat

Do **not** rebuild P0 or P1A. Do not add stubs, demos, canned success, fake provider work, guessed adapters, or duplicated authority paths. Do not get trapped in repeated full-suite/test loops: use one focused RED/GREEN cycle per real behavior and one fresh final verification after substantive changes. Preserve migrations and prior review hardening.

The next phase is **P1B — real drawing/spec ingestion and sheet intelligence**. Start only from a permissioned real M-drawing/spec packet whose files may lawfully be processed. Establish source custody and storage permissions first, then design genuine PDF/spec ingestion, page/sheet identity, and evidence-producing sheet intelligence without granting agents quantity, pricing, approval, or release authority.
