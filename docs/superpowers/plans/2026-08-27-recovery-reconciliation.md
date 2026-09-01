> Copied unchanged from `origin/docs/recovery-reconciliation-2026-08-27` (commit 7d7c97c) on 2026-09-01 so the candidate plan of record is visible from `main`. It is Option A in `ROADMAP.md`; the owner has not yet chosen a plan of record (Decision 1).

# Foundation 0.1 Recovery Reconciliation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the real clean-room Foundation 0.1 intake, evidence, job, and recovery core while preserving useful recovered P0/P1A concepts without transplanting their defective or parallel schema.

**Architecture:** One local Python process owns a controlled SQLite writer and a SHA-256 content-addressed filesystem vault. Deterministic PDF intake creates immutable content, document revision, sheet, evidence, job, correction, and audit identities; AI, research, pricing, takeoff measurement, and worker execution remain outside this milestone. Recovered behavior enters only through newly written contracts and regression tests against canonical identities.

**Tech Stack:** Python 3.12, SQLite in WAL mode, SHA-256 filesystem vault, a PDFium-backed adapter selected by the Task 1 proof, `pytest`, `ruff`, `mypy`, Windows CI, macOS Apple-Silicon verification.

**Spec:** `docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md`

## Global Constraints

- The former `HELEO_HELIOS_2.0` repository remains quarantined and must never be inspected, imported, referenced, or reused.
- The recovered misplaced-chat repository is preserved only by the recorded owner exception in `docs/recovery/2026-08-27-misplaced-chat-provenance.md`.
- Do not cherry-pick recovered commits or copy migrations 001–013 into the canonical lineage.
- Source bytes are read-only inputs and remain byte-identical; SHA-256 mismatch is a hard failure.
- SQLite uses foreign keys, WAL mode, explicit forward migrations, checksummed migration history, and one controlled writer.
- Windows and macOS share the same authoritative core; Apple Silicon and Windows CI are mandatory proof targets.
- The default Foundation test path performs zero external network calls.
- A mock PDF parser, in-memory fake vault, canned worker result, or demonstration script cannot satisfy acceptance.
- Models, NotebookLM, LangGraph, LangChain, RAG, coding workers, n8n, cloud services, pricing, and bid release cannot delay Foundation 0.1.
- Verification is bounded: run the new failing check once, implement the behavior, run the affected check once, repair concrete failures, and run the full suite once at the task or milestone gate. Do not repeat an unchanged suite without a new failure hypothesis.
- Every task ends in a reviewable commit and may not change files outside its declared paths.

---

## File Structure

```text
pyproject.toml
src/heleos_spark/
  __init__.py
  cli.py
  config.py
  contracts.py
  errors.py
  platform_probe.py
  audit.py
  evidence.py
  intake.py
  jobs.py
  repositories.py
  services.py
  vault.py
  backup.py
  reconciliation.py
  pdf/
    __init__.py
    contracts.py
    pdfium_adapter.py
  db/
    __init__.py
    database.py
    migrations.py
  migrations/
    0001_foundation_content_identity.sql
    0002_foundation_sheets_scales.sql
    0003_foundation_evidence_jobs_audit.sql
    0004_foundation_recovery_guards.sql
tests/
  conftest.py
  fixtures/
    manifest.json
    valid-two-page.pdf
    near-duplicate-two-page.pdf
    encrypted-two-page.pdf
    corrupt.pdf
  test_package_contract.py
  test_platform_probe.py
  test_migrations.py
  test_identity_contracts.py
  test_vault.py
  test_backup_restore.py
  test_vault_reconciliation.py
  test_pdf_intake.py
  test_sheets_and_scales.py
  test_evidence_contracts.py
  test_audit_events.py
  test_job_lifecycle.py
  test_interrupted_ingest_recovery.py
  test_cli.py
  test_foundation_acceptance.py
docs/architecture/foundation-0.1-contracts.md
docs/verification/platform-proof.md
docs/verification/foundation-0.1-acceptance.md
dependencies/manifest.toml
```

Each module owns one responsibility. `intake.py` coordinates domain actions but does not contain SQL, filesystem publication logic, or PDF implementation details. `repositories.py` is the only domain persistence layer. `database.py` owns connection/transaction behavior. `migrations.py` owns immutable schema advancement. `vault.py` owns content publication and verification.

---

### Task 1: Prove and freeze the cross-platform Foundation runtime

**Files:**
- Create: `pyproject.toml`
- Create: `src/heleos_spark/__init__.py`
- Create: `src/heleos_spark/platform_probe.py`
- Create: `tests/test_package_contract.py`
- Create: `tests/test_platform_probe.py`
- Create: `dependencies/manifest.toml`
- Create: `docs/verification/platform-proof.md`

**Interfaces:**
- Consumes: Public/synthetic two-page PDF fixture bytes generated for this repository.
- Produces: `PlatformProbeResult`, a selected PDF adapter package/version/digest, and a recorded Windows/macOS go/no-go decision.

- [ ] **Step 1: Write the package contract test**

```python
from importlib.metadata import version

def test_distribution_and_import_name_are_canonical() -> None:
    import heleos_spark
    assert heleos_spark.__version__ == version("heleos-spark")
    assert "helios_takeoff_core" not in heleos_spark.__dict__
```

- [ ] **Step 2: Run the contract test and confirm the package is absent**

Run: `python -m pytest tests/test_package_contract.py -q`

Expected: failure because `heleos_spark` is not installed.

- [ ] **Step 3: Create the package and strict tool configuration**

Define `heleos-spark` with Python `>=3.12`, no cloud SDK, and development extras containing `pytest`, `ruff`, and `mypy`. Add:

```toml
[project.scripts]
heleos = "heleos_spark.cli:main"
```

Create `src/heleos_spark/__init__.py` with one authoritative version import and no side effects.

- [ ] **Step 4: Write the platform probe contract**

```python
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class PlatformProbeResult:
    platform: str
    architecture: str
    sqlite_version: str
    wal_supported: bool
    pdf_backend: str
    pdf_backend_version: str
    page_count: int
    rendered_sha256: str
```

The probe opens the fixture through the candidate PDFium adapter, records two pages, renders page 1 deterministically, computes its SHA-256, creates a temporary SQLite database in WAL mode, and emits canonical JSON sorted by key.

- [ ] **Step 5: Run the probe on macOS Apple Silicon and Windows**

```bash
python -m pip install -e '.[dev]'
python -m pytest tests/test_package_contract.py tests/test_platform_probe.py -q
python -m heleos_spark.platform_probe tests/fixtures/valid-two-page.pdf
```

Record exact OS build, architecture, Python, SQLite, PDF adapter version, wheel hash, page count, render hash, runtime, and peak resident memory in `docs/verification/platform-proof.md`. The selected PDF dependency enters `dependencies/manifest.toml` only after both platforms pass.

- [ ] **Step 6: Run bounded task verification**

```bash
python -m ruff check .
python -m mypy src
python -m pytest tests/test_package_contract.py tests/test_platform_probe.py -q
```

Expected: zero failures on both platforms. A platform failure blocks Task 2 and is resolved in this task; it is not waived.

- [ ] **Step 7: Commit the runtime proof**

```bash
git add pyproject.toml src/heleos_spark tests/test_package_contract.py \
  tests/test_platform_probe.py dependencies/manifest.toml \
  docs/verification/platform-proof.md
git commit -m "build: prove cross-platform foundation runtime"
```

---

### Task 2: Build an atomic checksummed migration runner

**Files:**
- Create: `src/heleos_spark/db/__init__.py`
- Create: `src/heleos_spark/db/database.py`
- Create: `src/heleos_spark/db/migrations.py`
- Create: `tests/conftest.py`
- Create: `tests/test_migrations.py`

**Interfaces:**
- Consumes: A filesystem directory of immutable `NNNN_name.sql` files.
- Produces: `Database.connect()`, `Database.transaction()`, `Migration`, `discover_migrations()`, and `apply_migrations()`.

- [ ] **Step 1: Write migration tamper and partial-failure tests**

```python
def test_changed_applied_migration_is_rejected(database, migration_dir) -> None:
    apply_migrations(database, migration_dir)
    migration_dir.joinpath("0001_test.sql").write_text(
        "CREATE TABLE altered(id TEXT);", encoding="utf-8"
    )
    with pytest.raises(MigrationIntegrityError):
        apply_migrations(database, migration_dir)

def test_failed_migration_leaves_no_partial_schema(database, migration_dir) -> None:
    migration_dir.joinpath("0001_fail.sql").write_text(
        "CREATE TABLE partial(id TEXT); INSERT INTO missing VALUES (1);",
        encoding="utf-8",
    )
    with pytest.raises(MigrationApplyError):
        apply_migrations(database, migration_dir)
    assert database.scalar(
        "SELECT count(*) FROM sqlite_master WHERE name='partial'"
    ) == 0
```

- [ ] **Step 2: Run the migration tests and confirm failure**

Run: `python -m pytest tests/test_migrations.py -q`

Expected: import or missing-symbol failure.

- [ ] **Step 3: Implement controlled SQLite connections**

Every connection executes and verifies:

```sql
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;
```

`Database.transaction()` uses `BEGIN IMMEDIATE`, rolls back on any exception, and does not use `executescript` for migration application.

- [ ] **Step 4: Implement immutable migration discovery**

```python
@dataclass(frozen=True, slots=True)
class Migration:
    sequence: int
    filename: str
    sha256: str
    sql: str

def discover_migrations(path: Path) -> Sequence[Migration]:
    files = sorted(path.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    migrations: list[Migration] = []
    for expected, file in enumerate(files, start=1):
        sequence = int(file.name[:4])
        if sequence != expected:
            raise MigrationSequenceError(expected=expected, actual=sequence)
        raw = file.read_bytes()
        migrations.append(Migration(
            sequence=sequence,
            filename=file.name,
            sha256=hashlib.sha256(raw).hexdigest(),
            sql=raw.decode("utf-8"),
        ))
    return tuple(migrations)
```

`apply_migrations(database: Database, path: Path) -> Sequence[Migration]` calls
`discover_migrations`, compares every applied ledger row to sequence, filename and
SHA-256, splits unapplied SQL with `sqlite3.complete_statement`, executes every
statement inside one transaction, then inserts sequence, filename, SHA-256, applied
UTC time, and application version into `schema_migrations`. Reject duplicate
sequences, missing predecessors, filename drift, checksum drift, and a database
version ahead of packaged migrations.

- [ ] **Step 5: Verify and commit the migration runner**

```bash
python -m pytest tests/test_migrations.py -q
git add src/heleos_spark/db tests/conftest.py tests/test_migrations.py
git commit -m "feat: add atomic checksummed migrations"
```

Expected: fresh apply, replay, checksum tamper, missing predecessor, and partial failure checks pass.

---

### Task 3: Establish canonical content and document identity

**Files:**
- Create: `src/heleos_spark/migrations/0001_foundation_content_identity.sql`
- Create: `src/heleos_spark/contracts.py`
- Create: `src/heleos_spark/errors.py`
- Create: `src/heleos_spark/repositories.py`
- Create: `tests/test_identity_contracts.py`
- Create: `docs/architecture/foundation-0.1-contracts.md`

**Interfaces:**
- Consumes: Verified SHA-256, media type, byte length, logical document identity, project association, and intake request identity.
- Produces: `ProjectId`, `ContentObject`, `Document`, `DocumentRevision`, `ProjectDocument`, `IngestEvent`, and repository create/read methods.

- [ ] **Step 1: Write identity and cross-project integrity tests**

```python
def test_same_bytes_new_filename_reuses_content_and_revision(service, fixture_pdf) -> None:
    first = service.ingest(project_id="P1", source_name="M-001.pdf", source=fixture_pdf)
    second = service.ingest(project_id="P1", source_name="Mechanical.pdf", source=fixture_pdf)
    assert first.content_object_id == second.content_object_id
    assert first.document_revision_id == second.document_revision_id
    assert first.ingest_event_id != second.ingest_event_id

def test_project_document_rejects_foreign_project_context(repository) -> None:
    with pytest.raises(ProjectBoundaryError):
        repository.associate_foreign_revision_for_test()
```

- [ ] **Step 2: Run the identity tests and confirm failure**

Run: `python -m pytest tests/test_identity_contracts.py -q`

Expected: missing migration/contracts failure.

- [ ] **Step 3: Create migration 0001**

Create normalized tables for:

```text
projects
content_objects
documents
document_revisions
project_documents
ingest_events
```

`content_objects.sha256` is a 64-character lowercase hexadecimal primary identity. `document_revisions` references one immutable content object. `project_documents` holds project-local role, authority, display name, and issue context without duplicating bytes. `ingest_events` records `ACCEPTED`, `DUPLICATE`, `REJECTED`, or `QUARANTINED`, source filename, idempotency key, content hash when available, typed reason, and request time. Database triggers reject UPDATE/DELETE on immutable rows.

- [ ] **Step 4: Implement typed contracts and repository boundaries**

Use frozen dataclasses or strict Pydantic models. Parse IDs, hashes, UTC timestamps, media types, byte lengths, and enumerations before SQL. Repository methods accept a typed project context; they do not accept arbitrary project IDs for related rows.

- [ ] **Step 5: Verify and commit canonical identity**

```bash
python -m pytest tests/test_migrations.py tests/test_identity_contracts.py -q
python -m ruff check src/heleos_spark tests/test_identity_contracts.py
python -m mypy src
git add src/heleos_spark/migrations/0001_foundation_content_identity.sql \
  src/heleos_spark/contracts.py src/heleos_spark/errors.py \
  src/heleos_spark/repositories.py tests/test_identity_contracts.py \
  docs/architecture/foundation-0.1-contracts.md
git commit -m "feat: establish canonical content identity"
```

Expected: zero failures.

---

### Task 4: Implement the immutable content vault and recovery inventory

**Files:**
- Create: `src/heleos_spark/vault.py`
- Create: `src/heleos_spark/reconciliation.py`
- Create: `src/heleos_spark/backup.py`
- Create: `src/heleos_spark/migrations/0004_foundation_recovery_guards.sql`
- Create: `tests/test_vault.py`
- Create: `tests/test_vault_reconciliation.py`
- Create: `tests/test_backup_restore.py`

**Interfaces:**
- Consumes: A readable binary stream and expected/derived SHA-256.
- Produces: `VaultObject`, `VaultVerification`, `ReconciliationReport`, `BackupManifest`, and verified restore results.

- [ ] **Step 1: Write atomic publication and corruption tests**

```python
def test_interrupted_write_never_publishes_partial_object(vault, failpoints) -> None:
    failpoints.raise_after_fsync()
    with pytest.raises(SimulatedInterruption):
        vault.put_bytes(b"complete source bytes")
    assert vault.list_published() == ()

def test_read_detects_hash_mismatch(vault) -> None:
    stored = vault.put_bytes(b"original")
    vault.corrupt_for_test(stored.key, b"changed")
    with pytest.raises(ContentHashMismatch):
        vault.read_verified(stored.sha256)
```

- [ ] **Step 2: Run vault tests and confirm failure**

Run: `python -m pytest tests/test_vault.py -q`

Expected: missing vault implementation.

- [ ] **Step 3: Implement digest-derived vault keys and atomic writes**

```python
def object_key(sha256: str) -> PurePosixPath:
    return PurePosixPath("sha256", sha256[:2], sha256)
```

`ContentVault.put_stream(source: BinaryIO) -> VaultObject` writes a same-filesystem
temporary file, stream-hashes while writing, flushes and syncs, atomically replaces
the final digest path, syncs the parent directory where supported, then re-reads and
verifies the final object. `ContentVault.read_verified(sha256: str) -> bytes` rejects
a mismatch before returning bytes. `ContentVault.verify(sha256: str) ->
VaultVerification` reports expected/actual hash, expected/actual size, key, and
verification time. Windows and POSIX implementations provide equivalent traversal
and reparse/symlink protection without disabling a required platform.

- [ ] **Step 4: Implement non-destructive reconciliation and verified backup**

Classify each inconsistency as `UNREFERENCED_VALID`, `MISSING_REFERENCED`, `HASH_MISMATCH`, `ABANDONED_TEMP`, or `UNEXPECTED_PATH`. Reconciliation records an audit result and never deletes or repairs bytes silently. Backup uses the SQLite backup API plus a sorted content manifest; restore verifies schema and every object hash before acceptance.

- [ ] **Step 5: Verify and commit vault/recovery**

```bash
python -m pytest tests/test_vault.py tests/test_vault_reconciliation.py \
  tests/test_backup_restore.py -q
git add src/heleos_spark/vault.py src/heleos_spark/reconciliation.py \
  src/heleos_spark/backup.py \
  src/heleos_spark/migrations/0004_foundation_recovery_guards.sql \
  tests/test_vault.py tests/test_vault_reconciliation.py tests/test_backup_restore.py
git commit -m "feat: add verified content vault and recovery"
```

Expected: atomic interruption, duplicate, near-duplicate, missing object, orphan, corruption, backup, and restore checks pass.

---

### Task 5: Implement deterministic PDF intake and sheet identity

**Files:**
- Create: `src/heleos_spark/pdf/__init__.py`
- Create: `src/heleos_spark/pdf/contracts.py`
- Create: `src/heleos_spark/pdf/pdfium_adapter.py`
- Create: `src/heleos_spark/intake.py`
- Create: `src/heleos_spark/migrations/0002_foundation_sheets_scales.sql`
- Create: `tests/fixtures/manifest.json`
- Create: `tests/fixtures/valid-two-page.pdf`
- Create: `tests/fixtures/near-duplicate-two-page.pdf`
- Create: `tests/fixtures/encrypted-two-page.pdf`
- Create: `tests/fixtures/corrupt.pdf`
- Create: `tests/test_pdf_intake.py`
- Create: `tests/test_sheets_and_scales.py`

**Interfaces:**
- Consumes: Local path or binary stream, project/document context, source filename, idempotency key.
- Produces: `PdfDocumentFacts`, `PdfPageFacts`, immutable sheet rows, typed intake outcome, and quarantine record.

- [ ] **Step 1: Record fixture provenance and exact hashes**

`tests/fixtures/manifest.json` records filename, SHA-256, size, generator/source, license, page count, page dimensions, rotation, encryption status, and expected intake outcome. Only public or repository-generated synthetic fixtures are allowed.

- [ ] **Step 2: Write actual-adapter intake tests**

```python
def test_valid_pdf_is_preserved_and_pages_are_stable(app, fixture_manifest) -> None:
    first = app.ingest_fixture("valid-two-page.pdf", key="ingest-1")
    second = app.ingest_fixture("valid-two-page.pdf", key="ingest-2")
    assert first.original_sha256 == fixture_manifest.valid.sha256
    assert second.content_object_id == first.content_object_id
    assert [p.identity for p in second.pages] == [p.identity for p in first.pages]
    assert len(app.ingest_events()) == 2

@pytest.mark.parametrize(
    ("filename", "outcome"),
    [("corrupt.pdf", "QUARANTINED_CORRUPT"),
     ("encrypted-two-page.pdf", "QUARANTINED_ENCRYPTED")],
)
def test_unsafe_pdf_has_typed_quarantine(app, filename, outcome) -> None:
    assert app.ingest_fixture(filename).outcome == outcome
```

- [ ] **Step 3: Run the PDF tests and confirm failure**

Run: `python -m pytest tests/test_pdf_intake.py tests/test_sheets_and_scales.py -q`

Expected: missing PDF contracts/adapter.

- [ ] **Step 4: Implement page facts and migration 0002**

```python
@dataclass(frozen=True, slots=True)
class PdfPageFacts:
    ordinal: int
    width_points: Decimal
    height_points: Decimal
    rotation_degrees: int
    page_identity: str
```

`sheets` references one document revision and stores deterministic page ordinal, identity, dimensions, rotation, units, title, and discipline. `scales` stores separate `DECLARED`, `DETECTED`, `CALIBRATED`, `VERIFIED`, and `REJECTED` facts. Intake does not fabricate scale.

- [ ] **Step 5: Implement one-transaction intake authority**

The service streams source bytes into the vault, verifies the published object, parses PDF metadata/pages, then commits content/document/revision/project/sheet/intake records in one database transaction. Failure after vault publication leaves a valid unreferenced object for reconciliation, not partial authoritative rows.

- [ ] **Step 6: Verify and commit deterministic intake**

```bash
python -m pytest tests/test_pdf_intake.py tests/test_sheets_and_scales.py \
  tests/test_vault_reconciliation.py -q
git add src/heleos_spark/pdf src/heleos_spark/intake.py \
  src/heleos_spark/migrations/0002_foundation_sheets_scales.sql \
  tests/fixtures tests/test_pdf_intake.py tests/test_sheets_and_scales.py
git commit -m "feat: add deterministic PDF intake"
```

Expected: stable duplicate intake, same bytes/new filename, near-duplicate, corrupt, and encrypted cases pass through the real adapter and vault.

---

### Task 6: Add evidence, correction, audit, and finite job contracts

**Files:**
- Create: `src/heleos_spark/migrations/0003_foundation_evidence_jobs_audit.sql`
- Create: `src/heleos_spark/evidence.py`
- Create: `src/heleos_spark/audit.py`
- Create: `src/heleos_spark/jobs.py`
- Create: `tests/test_evidence_contracts.py`
- Create: `tests/test_audit_events.py`
- Create: `tests/test_job_lifecycle.py`

**Interfaces:**
- Consumes: Canonical content/revision/sheet/run identities and typed geometry.
- Produces: `EvidenceObject`, `Correction`, append-only `AuditEvent`, `JobRun`, `Lease`, and deterministic state transitions.

- [ ] **Step 1: Write evidence-lineage and no-mutation tests**

```python
def test_evidence_requires_resolvable_source_lineage(service) -> None:
    with pytest.raises(EvidenceLineageError):
        service.record_evidence(document_revision_id="missing", sheet_id="missing")

def test_correction_does_not_mutate_original(service) -> None:
    original = service.record_evidence(**VALID_EVIDENCE)
    correction = service.correct(original.id, after={"rotation": 90}, reason="verified")
    assert service.get_evidence(original.id) == original
    assert correction.before == original.decision_value
```

- [ ] **Step 2: Write finite lease and idempotency tests**

```python
def test_expired_lease_is_reclaimed_once(job_service, clock) -> None:
    job = job_service.submit(request_hash="a" * 64, idempotency_key="K1")
    first = job_service.lease(job.id, owner="worker-1", ttl_seconds=30)
    clock.advance(seconds=31)
    second = job_service.lease(job.id, owner="worker-2", ttl_seconds=30)
    assert second.attempt == first.attempt + 1

def test_same_key_with_changed_request_is_conflict(job_service) -> None:
    job_service.submit(request_hash="a" * 64, idempotency_key="K1")
    with pytest.raises(IdempotencyConflict):
        job_service.submit(request_hash="b" * 64, idempotency_key="K1")
```

- [ ] **Step 3: Run the contract tests and confirm failure**

Run: `python -m pytest tests/test_evidence_contracts.py tests/test_job_lifecycle.py -q`

Expected: missing schema/services.

- [ ] **Step 4: Create migration 0003 and deterministic state control**

Create `evidence_objects`, `corrections`, `audit_events`, `job_runs`, `job_events`, and `job_leases`. Evidence stores source object, revision, sheet, coordinate space, transform, bbox/polygon, derivative parents, extraction method/parameters, runtime/model fields when applicable, rule IDs, confidence components, run, reviewer, and disposition. Leases have owner tokens, expiry, heartbeat, deadlines, attempt/action/cost budgets, checkpoint hash, cancellation, exit reason, and resume lineage. SQL rejects attempts/events after terminal state and rejects `SUCCEEDED` without required output artifacts.

- [ ] **Step 5: Verify and commit evidence/jobs**

```bash
python -m pytest tests/test_evidence_contracts.py tests/test_audit_events.py \
  tests/test_job_lifecycle.py -q
git add src/heleos_spark/migrations/0003_foundation_evidence_jobs_audit.sql \
  src/heleos_spark/evidence.py src/heleos_spark/audit.py src/heleos_spark/jobs.py \
  tests/test_evidence_contracts.py tests/test_audit_events.py \
  tests/test_job_lifecycle.py
git commit -m "feat: add evidence lineage and finite jobs"
```

Expected: terminal-attempt rejection, success-artifact gate, lease expiry, heartbeat loss, retry exhaustion, cancellation, and idempotency conflict pass.

---

### Task 7: Add restart-safe service and operator CLI

**Files:**
- Create: `src/heleos_spark/services.py`
- Create: `src/heleos_spark/config.py`
- Create: `src/heleos_spark/cli.py`
- Create: `tests/test_interrupted_ingest_recovery.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Consumes: Database, vault, PDF adapter, intake service, backup/reconciliation services.
- Produces: `heleos init`, `ingest`, `inspect`, `verify`, `reconcile`, `backup`, `restore`, and `restore-verify` commands.

- [ ] **Step 1: Write restart failpoint tests**

```python
def test_restart_after_vault_publish_before_metadata_commit(app, failpoints) -> None:
    failpoints.raise_before_metadata_commit()
    with pytest.raises(SimulatedInterruption):
        app.ingest("valid-two-page.pdf", key="interrupt-1")
    restarted = app.restart()
    report = restarted.reconcile()
    assert report.unreferenced_valid_count == 1
    assert restarted.authoritative_revision_count() == 0
```

- [ ] **Step 2: Write CLI contract tests and confirm failure**

Invoke the installed `heleos` script through
`subprocess.run(command, check=False, capture_output=True, text=True, shell=False)`.
Assert JSON schemas, exit codes, stderr redaction, idempotent replay, typed
quarantine, and verified backup/restore.

Run: `python -m pytest tests/test_interrupted_ingest_recovery.py tests/test_cli.py -q`

Expected: missing CLI/service failure.

- [ ] **Step 3: Implement configuration and stable commands**

Configuration accepts explicit database, vault, quarantine, and backup paths. It rejects network URLs, provider credentials, cloud settings, or paths escaping the approved project root. Each command returns typed JSON and a nonzero typed exit code on failure. `verify` independently reads and hashes bytes. `reconcile` is read-only by default. `restore` writes only to an explicitly empty destination and requires `restore-verify` before acceptance.

- [ ] **Step 4: Verify one real operator workflow**

```bash
heleos init --root ./foundation-test
heleos ingest --root ./foundation-test --project P1 \
  --file tests/fixtures/valid-two-page.pdf --idempotency-key acceptance-1
heleos inspect --root ./foundation-test --project P1
heleos verify --root ./foundation-test
heleos backup --root ./foundation-test --output ./foundation-backup
heleos restore --backup ./foundation-backup --output ./foundation-restored
heleos restore-verify --root ./foundation-restored
```

Expected: all commands exit zero, source fixture hash remains unchanged, and no network socket is opened.

- [ ] **Step 5: Commit service and CLI**

```bash
git add src/heleos_spark/services.py src/heleos_spark/config.py \
  src/heleos_spark/cli.py tests/test_interrupted_ingest_recovery.py tests/test_cli.py
git commit -m "feat: add restart-safe foundation CLI"
```

---

### Task 8: Execute and record Foundation 0.1 acceptance

**Files:**
- Create: `tests/test_foundation_acceptance.py`
- Create: `docs/verification/foundation-0.1-acceptance.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: Clean checkout, immutable fixture manifest, complete Foundation package.
- Produces: Machine-readable and human-readable acceptance bound to commit, dependency lock, platform, fixture hashes, schema, and command outputs.

- [ ] **Step 1: Write the full real-boundary acceptance test**

Install into an empty environment and use the real CLI, SQLite file, filesystem vault, PDF adapter, interruption failpoints, backup, and restore. Assert the exact Foundation criteria from the controlling spec and fail any outbound socket creation.

- [ ] **Step 2: Run the acceptance test once**

Run: `python -m pytest tests/test_foundation_acceptance.py -q`

Expected before final fixes: the first concrete unmet gate fails with an exact typed reason. Repair only that defect and rerun the affected check.

- [ ] **Step 3: Run the complete milestone gate once**

```bash
python -m ruff check .
python -m mypy src
python -m pytest -q
python -m build
```

Install the exact produced wheel in a fresh environment and run `heleos --help`. Record the exact test count, duration, wheel hash, and install result.

- [ ] **Step 4: Independently record acceptance facts**

Record source/vault/restored hashes; content, revision, ingest, sheet, job, evidence, correction, and audit row counts; migration checksums; interruption/restart result; corrupt/encrypted/near-duplicate outcomes; zero-network result; Windows/macOS proof; exact commit; and dependency manifest hash.

- [ ] **Step 5: Update bounded README and commit acceptance**

README states exactly what Foundation 0.1 does and explicitly states that HVAC measurement, OCR/vision, topology, quantities, pricing, bid release, agents, cloud, and native interfaces are not present.

```bash
git add tests/test_foundation_acceptance.py \
  docs/verification/foundation-0.1-acceptance.md README.md
git commit -m "test: record Foundation 0.1 acceptance"
```

---

### Task 9: Reconcile recovered capabilities after Foundation acceptance

**Files:**
- Create: `docs/superpowers/specs/frozen-revision-baseline-design.md`
- Create: `docs/superpowers/specs/worker-execution-design.md`
- Create: `tests/regressions/test_recovered_p0_invariants.py`
- Create: `tests/regressions/test_recovered_p1a_invariants.py`

**Interfaces:**
- Consumes: Accepted canonical identities and the recovered-invariant requirements
  enumerated in this plan.
- Produces: Two owner-reviewable component specifications and executable regression contracts; no production feature code.

- [ ] **Step 1: Write P0 defect regressions against canonical contracts**

Require one active successor per assertion lineage, exact evidence allocation reconciliation, late-critical-conflict release invalidation, frozen-baseline RFI response membership, single-currency estimates, authenticated distinct approval actors, and complete evidence/commercial export.

- [ ] **Step 2: Write P1A defect regressions against canonical jobs**

Reject attempts after terminal state, success without report artifacts, stale lease ownership, missing preflight receipts, external egress without policy approval, self-approval, and Windows-unavailable storage.

- [ ] **Step 3: Verify regression inventory**

Run: `python -m pytest --collect-only tests/regressions -q`

Expected: every named invariant is collected. These tests carry explicit post-Foundation markers and are not Foundation acceptance.

- [ ] **Step 4: Write component specifications using canonical identities**

The frozen-baseline specification references canonical content, document revision, project document, sheet, evidence, and job identities. The worker specification specializes canonical jobs/artifacts and includes real preflight, isolation, leases, budgets, stdout/stderr/report hashes, egress classification, no-self-approval, and Windows/macOS adapters. Neither creates parallel provenance tables.

- [ ] **Step 5: Obtain owner approval and commit contracts**

No takeoff/pricing/release or worker-execution feature code starts until the owner approves the applicable component specification.

```bash
git add docs/superpowers/specs/frozen-revision-baseline-design.md \
  docs/superpowers/specs/worker-execution-design.md tests/regressions
git commit -m "docs: reconcile recovered capability contracts"
```

---

## Recovered Migration Disposition

| Recovered item | Canonical disposition |
|---|---|
| `001_core.sql` | Do not copy. Re-derive content/document/project identity in canonical `0001`. |
| `002_governance.sql` | Adapt project-scoped authority only after authenticated identity design. |
| `003_evidence_quantities.sql` | Rebuild evidence on vault/revision/sheet/run identities; quantity assertions are outside Foundation. |
| `004_takeoff_conflicts.sql` | Preserve conflict/RFI requirements as post-Foundation specification input. |
| `005_pricing_release.sql` | Defer; require currency, quote provenance, role separation and late-conflict invalidation. |
| `006–008` gates | Re-derive only invariants needed by canonical tables; do not transplant triggers. |
| `009–013` agent ledger | Do not copy. Specialize canonical `job_runs` and content/evidence artifacts after Foundation. |
| `agentic/` package | Preserve in bundle; no canonical import until the worker specification is approved. |
| `demo.py` and demo acceptance | Preserve for provenance; never count as milestone acceptance. |
| P0/P1A `pyproject.toml` | Do not publish; package/version identity collides at `0.1.0`. |

## Done Criteria

1. Foundation 0.1 passes real-boundary acceptance on Windows and macOS.
2. Original fixture bytes and every stored/restored object hash verify independently.
3. Empty migration, replay, tamper, partial-failure, interruption, and restart cases pass.
4. Duplicate, renamed-identical, near-duplicate, corrupt, and encrypted inputs produce declared typed outcomes.
5. The default suite proves zero external network calls.
6. Acceptance is bound to exact commit, dependencies, fixtures, schema checksums, commands, counts, and outputs.
7. No recovered source or SQL was directly merged into canonical production paths.
8. Post-Foundation P0/P1A specifications and regressions exist, while their feature code remains owner-gated.

## Self-Review Record

- Spec coverage: Tasks 1–8 cover clean-room, canonical identities, evidence lineage, local storage, migration integrity, intake, jobs, idempotency, crash recovery, backup/restore, platform proof, and milestone acceptance.
- Scope separation: pricing, takeoff quantities, models, research adapters, agent execution, native UI, and cloud are excluded from Foundation and represented only by Task 9 contracts.
- Placeholder scan: dependency selection is an explicit Task 1 evidence gate with required recorded output; no production behavior is left unspecified within an implementation task.
- Type consistency: canonical content, revision, sheet, evidence, correction, audit, and job identities introduced in Tasks 2–6 are the only identities consumed by subsequent tasks.
