# P1A Agent Execution Spine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a real, local-first, bounded worker execution spine that audits a frozen P0 baseline through a genuine subprocess and preserves a hash-bound, immutable execution trail.

**Architecture:** P1A is a separate append-only execution ledger in the same SQLite database. It reads frozen P0 document metadata, materializes a content-addressed manifest, invokes one strict JSON subprocess protocol, and records artifacts, attempts, and derived terminal state. P0 stays the exclusive owner of takeoff evidence, quantities, approvals, pricing, and releases.

**Tech Stack:** Python 3.12 standard library (`sqlite3`, `subprocess`, `json`, `hashlib`, `pathlib`, `unittest`, `wsgiref`); no third-party runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-08-27-p1a-execution-spine-design.md`

## Global Constraints

- Keep the existing P0 API and append-only authority semantics intact.
- Use immutable ordered migrations `009_agent_execution_spine.sql` and, where
  an execution-lifecycle hardening change is discovered after 009 is frozen,
  a new numbered migration. Never modify migrations 001–009 after commit.
- Never write credentials, provider command strings, project drawing bytes, or P0 database paths into P1A database rows or worker envelopes.
- A worker success requires a real process, schema-valid stdout, and hash-verified artifact; fixtures have no production acceptance credit.
- Invoke trusted local subprocesses with `shell=False`, private attempt directories, finite timeout, capped output, process-group termination, and bounded retry policy; do not claim OS sandboxing.
- `run-once` handles one work item only. Same input hash + adapter hash + failure signature must escalate rather than retry.
- P1A must never create P0 quantity reviews/approvals, pricing, estimates, or bid releases.
- Runtime remains Python 3.12 standard library only; all tests use `unittest`.

---

## File structure

- `src/helios_takeoff_core/migrations/009_agent_execution_spine.sql` — P1A append-only schema, views, indexes, and integrity triggers.
- `src/helios_takeoff_core/agentic/contracts.py` — enums, schema identifiers, strict JSON validation, and worker configuration parsing.
- `src/helios_takeoff_core/agentic/artifacts.py` — content-addressed artifact store and integrity verifier.
- `src/helios_takeoff_core/agentic/repository.py` — SQLite persistence and state projections for P1A resources.
- `src/helios_takeoff_core/agentic/service.py` — frozen-baseline manifest creation, job lifecycle, retry/escalation policy, and roster bootstrap.
- `src/helios_takeoff_core/agentic/runner.py` — bounded subprocess dispatch and output capture/validation.
- `src/helios_takeoff_core/agentic/manifest_worker.py` — real deterministic worker process for `BASELINE_AUDIT`.
- `src/helios_takeoff_core/p1a_cli.py` — P1A operator CLI.
- `src/helios_takeoff_core/api.py` — bounded P1A job/status/artifact routes only.
- `docs/openapi/p1a-openapi.yaml` — versioned P1A API contract.
- `docs/operator/p1a-operator-guide.md` — real run path, provider onboarding, limitations, and recovery procedure.
- `docs/verification/p1a-acceptance.md` — recorded acceptance command/result.

### Task 1: P1A schema and immutable projections

**Files:**
- Create: `src/helios_takeoff_core/migrations/009_agent_execution_spine.sql`
- Create: `tests/test_agent_execution_schema.py`
- Modify: `src/helios_takeoff_core/repository.py`

**Interfaces:**
- Produces `TakeoffRepository.export_revision_set_manifest(revision_set_id: str) -> dict[str, Any]`.
- Produces `AgentRepository`-consumable tables and state views.

- [ ] **Step 1: Write failing migration tests**

```python
def test_p1a_schema_rejects_a_job_for_an_open_revision_set(self) -> None:
    with self.assertRaises(sqlite3.IntegrityError):
        self.connection.execute("INSERT INTO agent_jobs (...) VALUES (...)")

def test_p1a_rows_are_append_only(self) -> None:
    with self.assertRaises(sqlite3.IntegrityError):
        self.connection.execute("UPDATE agent_artifacts SET media_type = 'text/plain' WHERE id = ?", (artifact_id,))
```

- [ ] **Step 2: Run the focused tests and observe expected failures**

Run: `PYTHONPATH=src python3 -m unittest tests.test_agent_execution_schema -v`

Expected: failing imports/schema assertions because migration 009 and manifest helper do not yet exist.

- [ ] **Step 3: Implement migration 009 and the read-only P0 manifest helper**

```python
def export_revision_set_manifest(self, revision_set_id: str) -> dict[str, Any]:
    """Return canonical document/evidence metadata only for one frozen P0 baseline."""
```

The manifest must include revision-set/project IDs, document IDs/types/numbers/titles/
SHA-256/issue dates/supersession IDs, and evidence counts. It must reject non-frozen
revision sets.

- [ ] **Step 4: Run focused tests and commit**

Run: `PYTHONPATH=src python3 -m unittest tests.test_agent_execution_schema -v`

Expected: PASS.

Commit:

```bash
git add src/helios_takeoff_core/migrations/009_agent_execution_spine.sql src/helios_takeoff_core/repository.py tests/test_agent_execution_schema.py
git commit -m "feat: add immutable P1A execution ledger"
```

### Task 2: Content-addressed artifacts and strict worker contracts

**Files:**
- Create: `src/helios_takeoff_core/agentic/__init__.py`
- Create: `src/helios_takeoff_core/agentic/contracts.py`
- Create: `src/helios_takeoff_core/agentic/artifacts.py`
- Create: `tests/test_agent_artifacts.py`

**Interfaces:**
- Produces `ArtifactStore(root: Path)`, `put_json(payload, *, media_type, schema_version)`, `put_file(path, ...)`, and `verify(record)`.
- Produces `validate_worker_result(payload, expected_input_sha256) -> dict[str, Any]`.

- [ ] **Step 1: Write failing artifact and protocol tests**

```python
def test_store_deduplicates_by_sha256_and_detects_tampering(self) -> None:
    record = store.put_json({"stable": True}, media_type="application/json", schema_version="test/v1")
    self.assertTrue(store.verify(record))
    record.path.write_text("tampered", encoding="utf-8")
    self.assertFalse(store.verify(record))

def test_worker_result_rejects_a_mismatched_input_digest(self) -> None:
    with self.assertRaises(ValidationError):
        validate_worker_result({"input_sha256": "0" * 64}, expected_input_sha256="1" * 64)
```

- [ ] **Step 2: Run focused tests and observe expected failures**

Run: `PYTHONPATH=src python3 -m unittest tests.test_agent_artifacts -v`

Expected: FAIL because the agentic package does not yet exist.

- [ ] **Step 3: Implement hash store and exact JSON result contract**

Store artifacts beneath a digest-derived relative key, reject paths outside its root,
canonicalize JSON before hashing, and retain only metadata/relative key in SQLite.
Require worker result protocol, `SUCCEEDED` status, input digest, report kind/schema,
and JSON payload; no extra result shapes are accepted in P1A.

- [ ] **Step 4: Run focused tests and commit**

Run: `PYTHONPATH=src python3 -m unittest tests.test_agent_artifacts -v`

Expected: PASS.

Commit:

```bash
git add src/helios_takeoff_core/agentic tests/test_agent_artifacts.py
git commit -m "feat: add P1A artifact integrity and worker protocol"
```

### Task 3: Durable job service and no-progress policy

**Files:**
- Create: `src/helios_takeoff_core/agentic/repository.py`
- Create: `src/helios_takeoff_core/agentic/service.py`
- Create: `tests/test_agent_job_lifecycle.py`
- Create: `src/helios_takeoff_core/migrations/010_agent_worker_preflight.sql`

**Interfaces:**
- Produces `AgentRepository` methods for worker roster/adapter/availability/artifact/job/run/item/attempt/event records and state projections.
- Produces `AgentExecutionService.bootstrap_roster()`, `submit_baseline_audit(...)`, `claim_next_work_item()`, `record_blocked(...)`, `record_failure_or_retry(...)`, and `get_job(job_id)`.

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_submit_requires_frozen_p0_baseline_and_builds_a_manifest_artifact(self) -> None:
    job = service.submit_baseline_audit(...)
    self.assertEqual(job["state"], "QUEUED")
    self.assertTrue(store.verify(job["input_artifact"]))

def test_identical_failure_escalates_instead_of_creating_a_third_attempt(self) -> None:
    service.record_failure_or_retry(..., failure_signature="timeout:same")
    service.record_failure_or_retry(..., failure_signature="timeout:same")
    self.assertEqual(service.get_job(job_id)["state"], "ESCALATED")
```

- [ ] **Step 2: Run focused tests and observe expected failures**

Run: `PYTHONPATH=src python3 -m unittest tests.test_agent_job_lifecycle -v`

Expected: FAIL because the P1A repository/service do not yet exist.

- [ ] **Step 3: Implement immutable lifecycle commands**

Bootstrap the named six external workers plus `helios-baseline-auditor`.
Bind every availability/preflight event to the exact adapter revision through
the new ordered migration rather than treating a worker as available merely
because another adapter once succeeded. 
Create one frozen-baseline job, workflow run, and work item with a finite max
attempt count. Derive state from events and record blockers/escalations as
immutable events; never update a status column.

- [ ] **Step 4: Run focused tests and commit**

Run: `PYTHONPATH=src python3 -m unittest tests.test_agent_job_lifecycle -v`

Expected: PASS.

Commit:

```bash
git add src/helios_takeoff_core/agentic/repository.py src/helios_takeoff_core/agentic/service.py tests/test_agent_job_lifecycle.py
git commit -m "feat: add durable bounded P1A jobs"
```

### Task 4: Real subprocess worker and bounded runner

**Files:**
- Create: `src/helios_takeoff_core/agentic/runner.py`
- Create: `src/helios_takeoff_core/agentic/manifest_worker.py`
- Create: `tests/test_agent_runner.py`

**Interfaces:**
- Produces `AgentRunner.run_once(*, workers_config: Path | None) -> dict[str, Any]`.
- Produces a module worker executable with `python -m helios_takeoff_core.agentic.manifest_worker`.

- [ ] **Step 1: Write failing real-process tests**

```python
def test_runner_executes_baseline_auditor_as_a_subprocess_and_persists_report(self) -> None:
    result = runner.run_once(workers_config=None)
    self.assertEqual(result["state"], "SUCCEEDED")
    self.assertEqual(result["report"]["kind"], "BASELINE_AUDIT_REPORT")

def test_runner_records_malformed_external_stdout_as_non_success(self) -> None:
    result = runner.run_once(workers_config=config_with_bad_worker)
    self.assertIn(result["state"], {"FAILED", "ESCALATED"})
```

- [ ] **Step 2: Run focused tests and observe expected failures**

Run: `PYTHONPATH=src python3 -m unittest tests.test_agent_runner -v`

Expected: FAIL because no subprocess runner exists.

- [ ] **Step 3: Implement real process dispatch**

Run a single work item with `shell=False`, a finite timeout, capped output, and
process-group cleanup in a private attempt directory. Persist stdout/stderr, validate exact JSON,
persist the report, and record `SUCCEEDED`. Missing config/command, nonzero
exit, timeout, malformed JSON, or digest mismatch must be recorded as
non-success and must not mutate P0 bid-impacting tables.

- [ ] **Step 4: Run focused tests and commit**

Run: `PYTHONPATH=src python3 -m unittest tests.test_agent_runner -v`

Expected: PASS.

Commit:

```bash
git add src/helios_takeoff_core/agentic tests/test_agent_runner.py
git commit -m "feat: run bounded P1A workers through subprocess protocol"
```

### Task 5: Operator API, CLI, and real handoff path

**Files:**
- Create: `src/helios_takeoff_core/p1a_cli.py`
- Modify: `src/helios_takeoff_core/api.py`
- Modify: `pyproject.toml`
- Create: `docs/openapi/p1a-openapi.yaml`
- Create: `docs/operator/p1a-operator-guide.md`
- Create: `tests/test_p1a_api.py`
- Create: `tests/test_p1a_cli.py`

**Interfaces:**
- Produces `helios-p1a` commands listed in the spec.
- Produces `POST /v1/agent-jobs`, `GET /v1/agent-jobs/{job_id}`, and `GET /v1/agent-artifacts/{artifact_id}` only.

- [ ] **Step 1: Write failing API/CLI contract tests**

```python
def test_cli_blank_database_can_bootstrap_submit_and_run_one_baseline_audit(self) -> None:
    completed = subprocess.run([...], check=True, capture_output=True, text=True)
    self.assertIn('SUCCEEDED', completed.stdout)

def test_api_cannot_expose_raw_worker_completion_or_p0_approval_commands(self) -> None:
    status, body = call_wsgi(app, 'POST', '/v1/agent-jobs/job-id/complete', {})
    self.assertEqual(status, 404)
```

- [ ] **Step 2: Run focused tests and observe expected failures**

Run: `PYTHONPATH=src python3 -m unittest tests.test_p1a_api tests.test_p1a_cli -v`

Expected: FAIL because P1A CLI/routes do not yet exist.

- [ ] **Step 3: Implement the usable local surface and documentation**

Add the console script and commands, local-only API routes with the existing
idempotency policy, OpenAPI contract, and an operator guide that separates a
real local P1A success from still-blocked provider onboarding. Document the
exact no-loop recovery path and workers configuration format.

- [ ] **Step 4: Run focused tests and commit**

Run: `PYTHONPATH=src python3 -m unittest tests.test_p1a_api tests.test_p1a_cli -v`

Expected: PASS.

Commit:

```bash
git add src/helios_takeoff_core/api.py src/helios_takeoff_core/p1a_cli.py pyproject.toml docs/openapi/p1a-openapi.yaml docs/operator/p1a-operator-guide.md tests/test_p1a_api.py tests/test_p1a_cli.py
git commit -m "feat: expose P1A local operator surface"
```

### Task 6: End-to-end acceptance, packaging, and forward-progress record

**Files:**
- Create: `tests/test_p1a_end_to_end.py`
- Create: `examples/p1a_baseline_audit.py`
- Create: `docs/verification/p1a-acceptance.md`
- Modify: `README.md`

**Interfaces:**
- Produces a blank-database path from P0 project/baseline to P1A audited report.
- Produces a machine-readable job/status and artifact verification output.

- [ ] **Step 1: Write the failing installed-package acceptance test**

```python
def test_blank_database_runs_real_baseline_audit_without_p0_bid_mutation(self) -> None:
    outcome = build_baseline_audit(...)
    self.assertEqual(outcome["state"], "SUCCEEDED")
    self.assertEqual(count_bid_impacting_rows(database), 0)
```

- [ ] **Step 2: Run the acceptance test and observe expected failure**

Run: `PYTHONPATH=src python3 -m unittest tests.test_p1a_end_to_end -v`

Expected: FAIL before the complete P1A path exists.

- [ ] **Step 3: Implement only the missing integration glue and record actual acceptance evidence**

Use P0 repository/services to create a real frozen baseline from supplied
metadata, then exercise the actual `helios-baseline-auditor` subprocess path.
Do not label fixture metadata or a test report as a real project takeoff.

- [ ] **Step 4: Run full verification, build a wheel, install it cleanly, and commit**

Run:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m compileall -q src examples
python3 -m pip wheel --no-index --no-deps --no-build-isolation --wheel-dir /tmp/helios-p1a-wheel .
python3 -m venv /tmp/helios-p1a-venv
/tmp/helios-p1a-venv/bin/pip install --no-index --no-deps /tmp/helios-p1a-wheel/*.whl
/tmp/helios-p1a-venv/bin/helios-p1a --help
git diff --check
```

Expected: all tests pass, installed `helios-p1a` responds, and the documented
baseline-audit path is executable.

Commit:

```bash
git add README.md examples/p1a_baseline_audit.py tests/test_p1a_end_to_end.py docs/verification/p1a-acceptance.md
git commit -m "test: verify P1A real baseline audit path"
```

## Plan self-review

- Spec coverage: Tasks 1–4 implement append-only records, artifact integrity,
  frozen-baseline audit, bounded subprocess dispatch, honest blocking, and
  no-progress escalation. Task 5 exposes the operator surface; Task 6 verifies
  a blank-install end-to-end path and records that it is not P1B takeoff.
- Placeholder scan: no `TODO`/`TBD` implementation steps remain; worker
  provider integration is explicitly host-configured rather than guessed.
- Type consistency: P0 manifest export feeds artifact creation; artifact
  records feed job/service; job/service feeds runner; runner feeds API/CLI and
  end-to-end acceptance.
