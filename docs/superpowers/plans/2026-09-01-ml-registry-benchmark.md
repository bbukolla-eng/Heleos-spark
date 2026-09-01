# Immutable ML Registry and Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an isolated, immutable model/dataset registry; local-only Hugging Face adapter boundary; supply-chain quarantine; dataset-rights and leakage intake; and a benchmark harness whose offline installed-code gate is independent from the real M5 Max acceptance receipt.

**Architecture:** Add a new `helios_takeoff_core.ml` package with its own SQLite store and migration ledger. Canonical JSON manifests and receipts are authoritative database records, while model weights, dataset archives, sample indexes, and benchmark inputs remain checksum-addressed files under an operator-owned external artifact root. The default wheel stays Python-standard-library-only; Hugging Face, image, and resource-measurement packages are lazy optional imports installed from platform-specific hash-locked environments and bound into receipts by lock-file digest.

**Tech Stack:** Python 3.12 standard library (`dataclasses`, `enum`, `hashlib`, `json`, `sqlite3`, `pathlib`, `zipfile`, `tarfile`, `time`, `platform`, `subprocess`, `unittest`); optional locally installed Hugging Face/Transformers, safetensors, image-decoding, PyTorch/MPS or PyTorch/Windows, and process-memory packages behind lazy imports; SQLite; setuptools.

**Spec:** `docs/superpowers/specs/2026-09-01-enriched-build-fabric-design.md`, especially sections 2, 3.4, 6, and 11–14.

## Global Constraints

- `CORE_CODE_COMPLETE` must be reachable from a clean installed wheel with no live Hugging Face/Kaggle request, no model download, no model-weight fixture, and no target-Mac claim.
- `TARGET_HOST_ACCEPTED` is separate. Only a real execution on the owner's Apple M5 Max with 18 CPU cores, 40 GPU cores, and 128 GB unified memory may produce the required target benchmark receipt.
- The current server must never emit `TARGET_MODEL_BENCHMARK_ACCEPTED`, even when unit tests inject a target-shaped fake host probe.
- The design names no exact Hugging Face model repository/revision/checksum and no exact Kaggle/Hugging Face dataset. Do not invent either. Real coordinates enter only through independently reviewed immutable manifests.
- The ML registry is not `helios_takeoff_core.db.Database`, the P0/P1A database, the v1 `EngineRepository`, or the v2 `EngineStore`. It has no cross-store foreign keys and no authority over quantities, rules, prices, estimates, approvals, or releases.
- Model weights and dataset archives are never stored as SQLite blobs or committed to Git. Only manifests, checksums, rights findings, scan results, benchmark results, and sanitized receipts are canonical registry facts.
- All registry facts are append-only. A correction, promotion, restriction, retirement, or rollback creates a successor manifest or receipt and never updates or deletes an earlier fact.
- A Hub branch, tag, `main`, `master`, or `latest` is not an immutable revision. Hugging Face revisions must be lowercase hexadecimal commit IDs.
- `trust_remote_code` is always false. The first admitted execution path accepts safetensors plus declarative configuration/tokenizer assets; pickle-derived weights and executable/code artifacts remain rejected.
- An unavailable scanner, dependency, artifact, Hub connection, or compatible device yields `UNAVAILABLE` or `BLOCKED`; it never yields success and never triggers an implicit download or substitute.
- Dataset rights are distinct for access, copying, redistribution, commercial use, and training. Permission in one category never implies permission in another.
- Evaluation data remains disjoint from training data. A required duplicate/leakage detector returning inconclusive blocks acceptance.
- Private bid drawings and specifications are not uploaded to Hugging Face or Kaggle and are not used for training without a separate explicit data-class authorization outside this phase.
- Windows and macOS use the same contracts and SQLite schema. Core storage cannot depend on POSIX-only `O_NOFOLLOW`, directory file descriptors, `killpg`, or chmod semantics.
- The existing default install remains free of third-party runtime dependencies. Optional ML environments are installed separately from platform-specific `--require-hashes` lock files and identified by SHA-256 in model and benchmark receipts.
- Focused checks target five minutes, affected integration checks ten minutes, and the full milestone gate twenty minutes. An unchanged failing command is not rerun.
- No test-only adapter, synthetic host descriptor, canned observation, or successful model import counts as the real model-execution acceptance.
- The Build Fabric must build itself: the first substantive model-registry/benchmark slice runs as `DELIVERABLE_C_MODEL_REGISTRY`, and dataset intake runs later as the separate dependent node `DELIVERABLE_D_DATASET_INTAKE`. Neither capability counts as installed production code until its honest handoff, distinct review, installed-code receipt, and Codex `IntegrationReceipt` exist.

---

## File and ownership map

| Path | Responsibility |
|---|---|
| `src/helios_takeoff_core/ml/contracts.py` | Frozen enums/dataclasses and protocol constants shared by the registry, intake, adapters, and benchmark harness |
| `src/helios_takeoff_core/ml/canonical.py` | Strict exact-field validation and deterministic canonical compilation for model, dataset, benchmark, and receipt documents |
| `src/helios_takeoff_core/ml/store.py` | Dedicated SQLite connection, migration runner, foreign-store rejection, immutable import/query operations |
| `src/helios_takeoff_core/ml/migrations/001_model_registry.sql` | ML store identity, model manifests, artifact/scan receipts, benchmark specs/results, promotion decisions |
| `src/helios_takeoff_core/ml/migrations/002_dataset_registry.sql` | Dataset manifests, sample-index receipts, rights reviews, overlap/leakage receipts, intake decisions |
| `src/helios_takeoff_core/ml/artifacts.py` | Portable external quarantine and admitted CAS with streaming hash verification and atomic publication |
| `src/helios_takeoff_core/ml/supply_chain.py` | Static format and archive inspection, scan policy, admission rules |
| `src/helios_takeoff_core/ml/adapters.py` | Provider-neutral local model adapter and host/resource probe protocols |
| `src/helios_takeoff_core/ml/huggingface_adapter.py` | Lazy optional, local-files-only image-to-text Hugging Face adapter |
| `src/helios_takeoff_core/ml/metrics.py` | Exact OCR CER/WER and exact-match metrics using integer edit distance and `Decimal` |
| `src/helios_takeoff_core/ml/host.py` | Honest macOS/Windows host discovery and target-profile comparison |
| `src/helios_takeoff_core/ml/benchmark.py` | Frozen benchmark execution, resource aggregation, threshold evaluation, and failure-set receipt construction |
| `src/helios_takeoff_core/ml/dataset_intake.py` | Dataset quarantine sequencing, rights/relevance decisions, sample-index validation, duplicate/leakage decisions |
| `src/helios_takeoff_core/ml/acceptance.py` | Finite offline installed-code proof and strict target-Mac proof |
| `src/helios_takeoff_core/ml_cli.py` | Finite JSON operator CLI; deliberately has no download command |
| `requirements/ml/README.md` | Optional-environment policy and reproducible lock-generation/install commands |
| `tests/test_ml_*.py` | Focused tests; test doubles remain inside tests and cannot emit production acceptance statuses |

## Canonical protocols and lifecycle values

Use these exact protocol strings:

```python
MODEL_MANIFEST_PROTOCOL = "helios.ml.model-manifest/v1"
DATASET_MANIFEST_PROTOCOL = "helios.ml.dataset-manifest/v1"
BENCHMARK_SPEC_PROTOCOL = "helios.ml.benchmark-spec/v1"
ARTIFACT_RECEIPT_PROTOCOL = "helios.ml.artifact-receipt/v1"
SCAN_RECEIPT_PROTOCOL = "helios.ml.scan-receipt/v1"
BENCHMARK_RECEIPT_PROTOCOL = "helios.ml.benchmark-receipt/v1"
DATASET_INTAKE_RECEIPT_PROTOCOL = "helios.ml.dataset-intake-receipt/v1"
CORE_ACCEPTANCE_PROTOCOL = "helios.ml.core-acceptance/v1"
TARGET_ACCEPTANCE_PROTOCOL = "helios.ml.target-acceptance/v1"
```

Use these closed values:

```python
class PromotionState(StrEnum):
    QUARANTINED = "QUARANTINED"
    CANDIDATE = "CANDIDATE"
    QUALIFIED = "QUALIFIED"
    PROMOTED = "PROMOTED"
    RETIRED = "RETIRED"
    REJECTED = "REJECTED"


class RightsVerdict(StrEnum):
    PERMITTED = "PERMITTED"
    PROHIBITED = "PROHIBITED"
    UNKNOWN = "UNKNOWN"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"


class ReceiptStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    UNAVAILABLE = "UNAVAILABLE"


class DatasetDisposition(StrEnum):
    ACCEPTED = "ACCEPTED"
    RESTRICTED = "RESTRICTED"
    REJECTED = "REJECTED"


class DatasetPurpose(StrEnum):
    EVALUATION = "EVALUATION"
    TRAINING = "TRAINING"
    RESEARCH_ONLY = "RESEARCH_ONLY"
```

Milestone-facing statuses are exact and non-overlapping:

| Status | Meaning | May satisfy |
|---|---|---|
| `ML_CORE_CODE_ACCEPTED` | Offline installed registry/intake/adapter/harness code gate passed; no live inference occurred | ML portion of `CORE_CODE_COMPLETE` |
| `DATASET_INTAKE_ACCEPTED` | A real HF/Kaggle candidate completed provenance, rights, quarantine, and leakage review | Dataset cycle criterion |
| `DATASET_INTAKE_RESTRICTED` | Candidate is retained with explicit permitted purposes but is not generally admitted | Evidence of honest intake, not unrestricted use |
| `TARGET_MODEL_BENCHMARK_ACCEPTED` | A real pinned model passed the frozen HELIOS benchmark on the exact M5 Max and failure review was accepted | Model portion of `TARGET_HOST_ACCEPTED` |
| `TARGET_MODEL_BENCHMARK_BLOCKED` | Host, artifact, runtime, rights, dataset, device, or threshold precondition failed | Nothing; unrelated core work continues |

## Build Fabric self-use gates

Before editing production files, commit an immutable `TaskManifest` and graph node for each deliverable through `python -m tools.helios_build task validate` and the graph validator:

| Node | Owned production scope | Dependency | Required installed-code receipt |
|---|---|---|---|
| `DELIVERABLE_C_MODEL_REGISTRY` | Tasks 1–5 plus the model/benchmark portions of Task 7 | Build Fabric MVP integrated | `DELIVERABLE_C_MODEL_REGISTRY_INSTALLED_CODE_ACCEPTED` |
| `DELIVERABLE_D_DATASET_INTAKE` | Task 6 plus the dataset portions of Task 7 | Accepted `IntegrationReceipt` for `DELIVERABLE_C_MODEL_REGISTRY` | `DELIVERABLE_D_DATASET_INTAKE_INSTALLED_CODE_ACCEPTED` |

The graph validator must reject overlapping active ownership of `ml/contracts.py`, `ml/canonical.py`, `ml/store.py`, `ml_cli.py`, `pyproject.toml`, and `tests/test_package_metadata.py`; Deliverable D receives those shared paths only after Deliverable C is integrated. Each manifest declares exact files, interfaces, migration stream/version, focused commands, installed-wheel command, test budgets, and forbidden P0/P1A/v1/v2 store paths.

Each node uses exactly the adapter kind that actually performed the work: `LOCAL_ADAPTER` for a real local finite execution or `EXTERNAL_SESSION` for a real collected external-provider session. An unavailable provider produces `UNAVAILABLE` and an explicit reroute; Codex work is never labeled as an external handoff. The handoff records its patch/commit SHA, changed files, commands/results, source packet IDs, dependency/security effects, duration, and unresolved findings.

A reviewer distinct from the builder produces the node's `ReviewReceipt`. Codex alone verifies the handoff and installed wheel, creates the `IntegrationReceipt`, and makes the integration commit. Earlier bounded implementation commits are handoff history only and do not count as accepted production capability until this gate closes. An `EXTERNAL_SESSION` receipt always has `target_host_eligible=false` and can never satisfy or contribute to `TARGET_MODEL_BENCHMARK_ACCEPTED` or `TARGET_HOST_ACCEPTED`.

---

### Task 1: Freeze strict ML manifest and receipt contracts

**Files:**
- Create: `src/helios_takeoff_core/ml/__init__.py`
- Create: `src/helios_takeoff_core/ml/contracts.py`
- Create: `src/helios_takeoff_core/ml/canonical.py`
- Create: `tests/test_ml_manifest_compiler.py`

**Interfaces:**
- Produces: `CompiledModelManifest`, `CompiledDatasetManifest`, `CompiledBenchmarkSpec`, `compile_model_manifest`, `compile_dataset_manifest`, `compile_benchmark_spec`, and `canonicalize_receipt`.
- Consumed later by: `MlRegistryStore`, artifact admission, dataset intake, benchmark execution, and CLI commands.

- [ ] **Build Fabric prerequisite: freeze and validate `DELIVERABLE_C_MODEL_REGISTRY`.**

Create `build_control/tasks/deliverable-c-model-registry.json` as an immutable `TaskManifest`, add the graph node `DELIVERABLE_C_MODEL_REGISTRY`, declare Tasks 1–5 and the C-owned Task 7 files/interfaces, and validate both manifest and acyclic/non-overlapping graph before the first production edit. Record the actual execution adapter as `LOCAL_ADAPTER` or `EXTERNAL_SESSION`; do not predeclare an unavailable provider as successful.

- [ ] **Step 1: Write focused RED tests for canonical identity and fail-closed model rules.**

Add tests with a complete in-memory candidate manifest and assert:

```python
def test_model_manifest_is_canonical_across_object_order() -> None:
    first = compile_model_manifest(valid_model_manifest())
    reordered = dict(reversed(list(valid_model_manifest().items())))
    second = compile_model_manifest(reordered)
    self.assertEqual(first.canonical_bytes, second.canonical_bytes)
    self.assertEqual(first.sha256, second.sha256)


def test_model_manifest_rejects_mutable_revision_and_remote_code() -> None:
    mutable = valid_model_manifest()
    mutable["revision"] = "main"
    with self.assertRaisesRegex(ValidationError, "immutable lowercase hexadecimal commit"):
        compile_model_manifest(mutable)

    remote_code = valid_model_manifest()
    remote_code["runtime"]["trust_remote_code"] = True
    with self.assertRaisesRegex(ValidationError, "trust_remote_code must be false"):
        compile_model_manifest(remote_code)


def test_promoted_manifest_requires_passed_benchmark_and_rollback() -> None:
    document = valid_model_manifest()
    document["promotion"] = {
        "state": "PROMOTED",
        "supersedes_manifest_sha256": "a" * 64,
        "benchmark_receipt_sha256s": [],
        "rollback_manifest_sha256": None,
        "failure_set_review_sha256": None,
    }
    with self.assertRaisesRegex(ValidationError, "PROMOTED model"):
        compile_model_manifest(document)
```

The complete model fixture must include exact provider/repository/revision/source URL, intended task/use, model-card digest, artifact descriptors, five rights findings, runtime lock digest, memory/hardware declarations, schemas, permitted data classes, benchmark bindings, and promotion object. Use fictional `example.invalid` metadata and empty text/config fixture hashes; never represent it as an executable model.

- [ ] **Step 2: Run the focused compiler test once and record RED.**

Run:

```bash
PYTHONPATH=src python3 -m unittest tests.test_ml_manifest_compiler -v
```

Expected: one import failure for `helios_takeoff_core.ml`; do not rerun before implementation changes.

- [ ] **Step 3: Implement frozen types and strict compilers.**

Define these exact public dataclasses:

```python
@dataclass(frozen=True)
class CompiledModelManifest:
    canonical_document: Mapping[str, object]
    canonical_bytes: bytes
    sha256: str


@dataclass(frozen=True)
class CompiledDatasetManifest:
    canonical_document: Mapping[str, object]
    canonical_bytes: bytes
    sha256: str


@dataclass(frozen=True)
class CompiledBenchmarkSpec:
    canonical_document: Mapping[str, object]
    canonical_bytes: bytes
    sha256: str


@dataclass(frozen=True)
class ArtifactDescriptor:
    logical_name: str
    source_url: str
    sha256: str
    byte_size: int
    media_type: str
    serialization: str


@dataclass(frozen=True)
class ModelExecutionRequest:
    model_manifest_sha256: str
    input_sha256: str
    input_path: Path
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ModelObservation:
    name: str
    value: object
    confidence: str
    model_repository: str
    model_revision: str
    input_sha256: str
    region: Mapping[str, object] | None
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ObservationBatch:
    observations: tuple[ModelObservation, ...]
    runtime_receipt: Mapping[str, object]
```

Expose these exact functions:

```python
def compile_model_manifest(
    document: Mapping[str, object],
) -> CompiledModelManifest:
    normalized = _normalize_model_manifest(document)
    canonical_bytes = _canonical_bytes(normalized)
    return CompiledModelManifest(
        canonical_document=normalized,
        canonical_bytes=canonical_bytes,
        sha256=hashlib.sha256(canonical_bytes).hexdigest(),
    )


def compile_dataset_manifest(
    document: Mapping[str, object],
) -> CompiledDatasetManifest:
    normalized = _normalize_dataset_manifest(document)
    canonical_bytes = _canonical_bytes(normalized)
    return CompiledDatasetManifest(
        canonical_document=normalized,
        canonical_bytes=canonical_bytes,
        sha256=hashlib.sha256(canonical_bytes).hexdigest(),
    )


def compile_benchmark_spec(
    document: Mapping[str, object],
) -> CompiledBenchmarkSpec:
    normalized = _normalize_benchmark_spec(document)
    canonical_bytes = _canonical_bytes(normalized)
    return CompiledBenchmarkSpec(
        canonical_document=normalized,
        canonical_bytes=canonical_bytes,
        sha256=hashlib.sha256(canonical_bytes).hexdigest(),
    )
```

`_canonical_bytes` must call `json.dumps` with `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`, and `allow_nan=False`. Exact-field sets reject unknown fields. Lists that are semantically sets—rights, permitted data classes, artifact descriptors, metric declarations, and evidence references—are sorted by stable identifiers and reject duplicates.

Validate these rules exactly:

- Hugging Face revisions match `^[0-9a-f]{40,64}$`.
- SHA-256 values match `^[0-9a-f]{64}$`; byte sizes are positive integers and reject booleans.
- `runtime.trust_remote_code` is exactly false.
- `runtime.dependency_lock_sha256`, adapter name/revision, Python version, framework versions, device class, and precision are present.
- Rights contain exactly `ACCESS`, `COPYING`, `REDISTRIBUTION`, `COMMERCIAL_USE`, and `TRAINING`, each with verdict, source URL, reviewed-by identity, reviewed-at timestamp, and nonblank basis.
- A `CANDIDATE` may have no benchmark receipt. `QUALIFIED` requires at least one passed benchmark receipt and accepted failure-set review. `PROMOTED` additionally requires a distinct qualified rollback-manifest digest. `RETIRED` and `REJECTED` require a successor or reason receipt.
- Model input/output schemas cannot contain executable, path, callback, import, environment, provider-command, or credential fields.
- Benchmark thresholds and confidence values are non-exponent decimal strings; binary floats are rejected.

- [ ] **Step 4: Run the focused compiler test once and record GREEN.**

Run the same command from Step 2. Expected: all `test_ml_manifest_compiler` cases pass in under five minutes.

- [ ] **Step 5: Commit the contract freeze.**

```bash
git add src/helios_takeoff_core/ml/__init__.py \
  src/helios_takeoff_core/ml/contracts.py \
  src/helios_takeoff_core/ml/canonical.py \
  tests/test_ml_manifest_compiler.py
git commit -m "feat: freeze immutable ML manifest contracts"
```

---

### Task 2: Add the isolated immutable ML registry store

**Files:**
- Create: `src/helios_takeoff_core/ml/store.py`
- Create: `src/helios_takeoff_core/ml/migrations/001_model_registry.sql`
- Create: `tests/test_ml_registry_store.py`

**Interfaces:**
- Consumes: compiled types from Task 1.
- Produces: `MlRegistryStore` and immutable model/artifact/benchmark persistence.

- [ ] **Step 1: Write focused RED tests for store identity, replay, conflicts, and foreign-path rejection.**

Cover these exact behaviors:

```python
def test_store_rejects_p0_database_path() -> None:
    p0 = Database(self.root / "p0.sqlite3")
    p0.initialize()
    with self.assertRaisesRegex(PreconditionError, "dedicated ML registry"):
        MlRegistryStore(p0.path).initialize()


def test_model_import_is_idempotent_and_append_only() -> None:
    manifest = compile_model_manifest(valid_model_manifest())
    first_id = self.store.import_model_manifest(manifest)
    second_id = self.store.import_model_manifest(manifest)
    self.assertEqual(first_id, second_id)
    with self.store.connection() as connection:
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE ml_model_manifests SET state='PROMOTED' WHERE id=?",
                (first_id,),
            )


def test_same_logical_version_with_different_bytes_conflicts() -> None:
    self.store.import_model_manifest(compile_model_manifest(valid_model_manifest()))
    changed = valid_model_manifest()
    changed["intended_use"] = "different immutable use"
    with self.assertRaises(ConflictError):
        self.store.import_model_manifest(compile_model_manifest(changed))
```

Also prove initialization rejects a v2 engine registry marker, a random unrelated user table, and any existing `schema_migrations`; repeated initialization of an ML store is idempotent.

- [ ] **Step 2: Run the store test once and record RED.**

```bash
PYTHONPATH=src python3 -m unittest tests.test_ml_registry_store -v
```

Expected: import failure for `MlRegistryStore`.

- [ ] **Step 3: Create migration 001 with one canonical authority per fact.**

The SQL migration must create:

```sql
CREATE TABLE ml_registry_meta (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    store_kind TEXT NOT NULL CHECK(store_kind = 'HELIOS_ML_REGISTRY'),
    created_at TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE ml_model_manifests (
    id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE CHECK(length(sha256) = 64),
    protocol TEXT NOT NULL CHECK(protocol = 'helios.ml.model-manifest/v1'),
    model_code TEXT NOT NULL,
    manifest_version TEXT NOT NULL,
    provider TEXT NOT NULL CHECK(provider = 'HUGGING_FACE'),
    repository TEXT NOT NULL,
    revision TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN (
        'QUARANTINED','CANDIDATE','QUALIFIED','PROMOTED','RETIRED','REJECTED'
    )),
    canonical_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(model_code, manifest_version),
    UNIQUE(provider, repository, revision, sha256)
) WITHOUT ROWID;

CREATE TABLE ml_artifact_receipts (
    id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE CHECK(length(sha256) = 64),
    model_manifest_sha256 TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL CHECK(length(artifact_sha256) = 64),
    status TEXT NOT NULL CHECK(status IN ('PASSED','FAILED','BLOCKED','UNAVAILABLE')),
    canonical_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(model_manifest_sha256) REFERENCES ml_model_manifests(sha256)
) WITHOUT ROWID;

CREATE TABLE ml_benchmark_specs (
    id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE CHECK(length(sha256) = 64),
    benchmark_code TEXT NOT NULL,
    version TEXT NOT NULL,
    task TEXT NOT NULL,
    dataset_manifest_sha256 TEXT NOT NULL CHECK(length(dataset_manifest_sha256) = 64),
    canonical_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(benchmark_code, version)
) WITHOUT ROWID;

CREATE TABLE ml_benchmark_receipts (
    id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE CHECK(length(sha256) = 64),
    model_manifest_sha256 TEXT NOT NULL,
    benchmark_spec_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PASSED','FAILED','BLOCKED','UNAVAILABLE')),
    target_host_eligible INTEGER NOT NULL CHECK(target_host_eligible IN (0, 1)),
    canonical_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(model_manifest_sha256) REFERENCES ml_model_manifests(sha256),
    FOREIGN KEY(benchmark_spec_sha256) REFERENCES ml_benchmark_specs(sha256)
) WITHOUT ROWID;

CREATE TABLE ml_model_promotion_receipts (
    id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE CHECK(length(sha256) = 64),
    predecessor_manifest_sha256 TEXT NOT NULL,
    successor_manifest_sha256 TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('QUALIFIED','PROMOTED','RETIRED','REJECTED')),
    canonical_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(predecessor_manifest_sha256) REFERENCES ml_model_manifests(sha256),
    FOREIGN KEY(successor_manifest_sha256) REFERENCES ml_model_manifests(sha256),
    CHECK(predecessor_manifest_sha256 <> successor_manifest_sha256)
) WITHOUT ROWID;
```

Add `BEFORE UPDATE` and `BEFORE DELETE` triggers to every fact table. The migration ledger is `ml_schema_migrations`, not `schema_migrations`.

- [ ] **Step 4: Implement `MlRegistryStore` with a strict store-kind guard.**

Expose these signatures:

```python
class MlRegistryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self._reject_foreign_store()
        self._apply_migrations()

    def import_model_manifest(self, manifest: CompiledModelManifest) -> str:
        return self._import_model_manifest(manifest)

    def import_benchmark_spec(self, spec: CompiledBenchmarkSpec) -> str:
        return self._import_benchmark_spec(spec)

    def record_artifact_receipt(self, receipt: Mapping[str, object]) -> str:
        return self._record_artifact_receipt(receipt)

    def record_benchmark_receipt(self, receipt: Mapping[str, object]) -> str:
        return self._record_benchmark_receipt(receipt)

    def get_model_manifest(self, sha256: str) -> dict[str, object]:
        return self._get_canonical_document("ml_model_manifests", sha256)
```

`_reject_foreign_store` must inspect `sqlite_master` before creating the ML ledger. An empty file is valid. An existing file is valid only when it contains `ml_registry_meta` with the exact marker and every user table is `ml_registry_meta`, `ml_schema_migrations`, or begins with `ml_`. This rejects P0/P1A, v1 engine, v2 engine, and arbitrary database paths without relying on a brittle table-name list.

Before insertion, recompile the stored canonical document and verify bytes/digest agree. Exact replay returns the existing ID. Same `(model_code, manifest_version)` or `(benchmark_code, version)` with different bytes raises `ConflictError`.

- [ ] **Step 5: Run the focused store test once and record GREEN.**

Run the Step 2 command. Expected: all store tests pass and `PRAGMA foreign_key_check` returns no rows.

- [ ] **Step 6: Commit the isolated store.**

```bash
git add src/helios_takeoff_core/ml/store.py \
  src/helios_takeoff_core/ml/migrations/001_model_registry.sql \
  tests/test_ml_registry_store.py
git commit -m "feat: add isolated immutable ML registry"
```

---

### Task 3: Implement portable quarantine, verification, and supply-chain admission

**Files:**
- Create: `src/helios_takeoff_core/ml/artifacts.py`
- Create: `src/helios_takeoff_core/ml/supply_chain.py`
- Create: `tests/test_ml_artifacts.py`
- Create: `tests/test_ml_supply_chain.py`

**Interfaces:**
- Consumes: `ArtifactDescriptor`, canonical receipt helpers, `MlRegistryStore.record_artifact_receipt`.
- Produces: `MlArtifactStore`, `stage_local_artifact`, `inspect_artifact`, `admit_artifact`.

- [ ] **Step 1: Write focused RED tests for digest verification and quarantine isolation.**

Cover exact replay, wrong digest/size, source symlinks, admitted-object tampering, and a quarantine object remaining unavailable through the admitted read API:

```python
def test_wrong_digest_never_enters_quarantine_identity() -> None:
    source = self.root / "source.safetensors"
    source.write_bytes(b"not the declared bytes")
    descriptor = artifact_descriptor(sha256="0" * 64, byte_size=22)
    with self.assertRaisesRegex(ValidationError, "digest"):
        self.artifacts.stage_local(source, descriptor)


def test_quarantined_object_cannot_be_opened_as_admitted() -> None:
    receipt = self.artifacts.stage_local(self.source, self.descriptor)
    with self.assertRaisesRegex(PreconditionError, "not admitted"):
        self.artifacts.open_admitted(receipt.artifact_sha256)
```

- [ ] **Step 2: Write focused RED archive tests.**

Construct tiny ZIP/TAR fixtures in the test temporary directory and assert rejection for `../escape`, absolute members, duplicate normalized names, symlink/hardlink/device entries, configured member-count excess, configured uncompressed-byte excess, and configured compression-ratio excess. Assert `.bin`, `.pt`, `.pth`, `.ckpt`, `.py`, `.ipynb`, `.dylib`, `.so`, `.dll`, `.exe`, `.bat`, `.cmd`, and `.ps1` artifacts cannot be admitted by the first policy.

- [ ] **Step 3: Run both focused modules once and record RED.**

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_ml_artifacts tests.test_ml_supply_chain -v
```

Expected: import failures for the new modules.

- [ ] **Step 4: Implement the portable CAS and receipt types.**

Use these public contracts:

```python
@dataclass(frozen=True)
class ArtifactReceipt:
    protocol: str
    artifact_sha256: str
    byte_size: int
    media_type: str
    store_key: str
    stage: str
    status: ReceiptStatus
    receipt_sha256: str


class MlArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=False)
        self.quarantine_root = self.root / "quarantine" / "sha256"
        self.object_root = self.root / "objects" / "sha256"

    def stage_local(
        self,
        source: Path,
        descriptor: ArtifactDescriptor,
    ) -> ArtifactReceipt:
        return stage_local_artifact(source, descriptor, self.quarantine_root)

    def admit(
        self,
        verification: ArtifactReceipt,
        scans: Sequence[ArtifactScanReceipt],
        policy: SupplyChainPolicy,
    ) -> ArtifactReceipt:
        return admit_artifact(
            verification,
            scans,
            self.quarantine_root,
            self.object_root,
            policy,
        )

    def open_admitted(self, sha256: str) -> BinaryIO:
        return self._open_verified_object(sha256)
```

Write to a uniquely named temporary file inside the destination digest directory, hash while streaming in 1 MiB chunks, compare expected size/digest, flush and `os.fsync`, then publish with `os.replace`. Object paths are derived only from validated lowercase SHA-256: `sha256/<first-two>/<digest>`. Before reading, rehash the complete admitted object. Reject paths that are symlinks or whose resolved parent escapes the configured root. Do not use the existing POSIX-only P1A `ArtifactStore`.

- [ ] **Step 5: Implement fail-closed supply-chain inspection.**

Expose:

```python
@dataclass(frozen=True)
class SupplyChainPolicy:
    required_scan_kinds: tuple[str, ...]
    allowed_serializations: tuple[str, ...]
    maximum_archive_members: int
    maximum_uncompressed_bytes: int
    maximum_compression_ratio: str


@dataclass(frozen=True)
class ArtifactScanReceipt:
    protocol: str
    artifact_sha256: str
    scanner_kind: str
    scanner_name: str
    scanner_revision: str
    policy_sha256: str
    status: ReceiptStatus
    findings: tuple[str, ...]
    receipt_sha256: str


def inspect_artifact(
    path: Path,
    descriptor: ArtifactDescriptor,
    policy: SupplyChainPolicy,
) -> ArtifactScanReceipt:
    return StaticArtifactInspector(policy).inspect(path, descriptor)
```

The built-in inspector recognizes safetensors by its bounded header structure, checks declared JSON/text/tokenizer configuration as data, and performs metadata-only archive safety inspection before extraction. External malware scanning is represented by an independently canonicalized scan receipt. Dataset archives require both `ARCHIVE_SAFETY` and `MALWARE`; model safetensors/config require `STATIC_FORMAT`, while any executable serialization remains prohibited. `admit_artifact` requires exactly one `PASSED` receipt for every required scan kind with matching artifact and policy digests; `FAILED`, `BLOCKED`, `UNAVAILABLE`, missing, or duplicate receipts block admission.

- [ ] **Step 6: Run the focused artifact/supply-chain command once and record GREEN.**

Run the Step 3 command. Expected: all cases pass in under five minutes.

- [ ] **Step 7: Commit quarantine and admission.**

```bash
git add src/helios_takeoff_core/ml/artifacts.py \
  src/helios_takeoff_core/ml/supply_chain.py \
  tests/test_ml_artifacts.py tests/test_ml_supply_chain.py
git commit -m "feat: quarantine and verify ML artifacts"
```

---

### Task 4: Add provider-neutral and local-only Hugging Face adapters

**Files:**
- Create: `src/helios_takeoff_core/ml/adapters.py`
- Create: `src/helios_takeoff_core/ml/huggingface_adapter.py`
- Create: `requirements/ml/README.md`
- Create: `tests/test_ml_huggingface_adapter.py`

**Interfaces:**
- Consumes: admitted artifact paths, compiled model manifests, model execution contracts.
- Produces: `ModelAdapter`, `AdapterPreflightReceipt`, `HuggingFaceImageToTextAdapter`.

- [ ] **Step 1: Write focused RED tests proving local-only, exact-device behavior.**

Inject a recording backend factory and assert all loader calls receive a local admitted snapshot path, `local_files_only=True`, `trust_remote_code=False`, and `use_safetensors=True`. Assert that missing optional packages, a dependency-lock mismatch, an unadmitted artifact, unsupported task, unavailable requested device, and an attempted CPU fallback all return `UNAVAILABLE` or `BLOCKED`. Patch network functions to raise and prove they are never called.

The success-path test returns a typed observation containing exact repository, revision, input hash, region, confidence, and evidence references. Name the test adapter `RecordingBackend`; keep it inside the test module and never expose it from production code.

- [ ] **Step 2: Run the adapter test once and record RED.**

```bash
PYTHONPATH=src python3 -m unittest tests.test_ml_huggingface_adapter -v
```

Expected: import failures for `adapters` and `huggingface_adapter`.

- [ ] **Step 3: Define the provider-neutral protocol.**

```python
@dataclass(frozen=True)
class HostDescriptor:
    os_name: str
    os_version: str
    architecture: str
    cpu_name: str
    cpu_cores: int
    gpu_name: str | None
    gpu_cores: int | None
    memory_bytes: int
    python_version: str
    device: str


@dataclass(frozen=True)
class AdapterPreflightReceipt:
    status: ReceiptStatus
    adapter_name: str
    adapter_revision: str
    model_manifest_sha256: str
    dependency_lock_sha256: str
    host: HostDescriptor
    reason_codes: tuple[str, ...]


class ModelAdapter(Protocol):
    def preflight(
        self,
        manifest: CompiledModelManifest,
        artifact_paths: Mapping[str, Path],
        host: HostDescriptor,
    ) -> AdapterPreflightReceipt:
        raise NotImplementedError

    def execute(self, request: ModelExecutionRequest) -> ObservationBatch:
        raise NotImplementedError
```

The protocol exception bodies communicate an abstract contract only; concrete adapters must return typed receipts or raise HELIOS domain errors.

- [ ] **Step 4: Implement the local-only image-to-text Hugging Face adapter.**

`HuggingFaceImageToTextAdapter` accepts an exact compiled manifest, an admitted snapshot root, a lock-file path, and explicit device. Its dependency imports occur inside `preflight`/load. The load path must be equivalent to:

```python
processor = AutoProcessor.from_pretrained(
    str(snapshot_root),
    local_files_only=True,
    trust_remote_code=False,
)
model = AutoModelForVision2Seq.from_pretrained(
    str(snapshot_root),
    local_files_only=True,
    trust_remote_code=False,
    use_safetensors=True,
)
model.to(explicit_device)
```

Set no implicit `device_map`; reject `auto`. Verify every regular file under the snapshot is named by the manifest and has an admitted-artifact receipt before importing optional libraries. Hash the lock file and require equality with `runtime.dependency_lock_sha256`. Read installed distribution versions using `importlib.metadata` and compare them with the runtime declaration. A missing package produces `UNAVAILABLE`; it does not tell the caller to download during execution.

`execute` hashes the input before decoding, rejects a changed input, runs exactly one bounded request, and returns `ModelObservation` entries. It never creates a quantity, engine rule, evidence item, or P0/P1A record.

- [ ] **Step 5: Document optional dependency handling without fabricating package versions.**

`requirements/ml/README.md` must state:

- default `pip install .` remains dependency-free;
- optional environments use `hf-macos-arm64-py312.lock`, `hf-windows-x86_64-cpu-py312.lock`, or `hf-windows-x86_64-cuda-py312.lock` only after an exact candidate model/runtime is selected;
- each lock contains exact versions and hashes and is installed with `python -m pip install --require-hashes -r <lock>`;
- a lock is committed only after it is generated and tested on its named platform; no empty or speculative lock is committed;
- the manifest and receipt record the exact lock SHA-256;
- Hub/Kaggle acquisition clients are build-time tools and are absent from the default runtime dependency graph;
- macOS MPS, Windows CPU, and Windows CUDA are separate measured environments; no result transfers between them by assertion.

- [ ] **Step 6: Run the focused adapter test once and record GREEN.**

Run the Step 2 command. Expected: all adapter tests pass without Hugging Face, PyTorch, Pillow, network, or model files installed.

- [ ] **Step 7: Commit the local-only adapter seam.**

```bash
git add src/helios_takeoff_core/ml/adapters.py \
  src/helios_takeoff_core/ml/huggingface_adapter.py \
  requirements/ml/README.md tests/test_ml_huggingface_adapter.py
git commit -m "feat: add local-only Hugging Face adapter"
```

---

### Task 5: Implement frozen OCR benchmarks and honest host/resource receipts

**Files:**
- Create: `src/helios_takeoff_core/ml/metrics.py`
- Create: `src/helios_takeoff_core/ml/host.py`
- Create: `src/helios_takeoff_core/ml/benchmark.py`
- Create: `tests/test_ml_benchmark.py`

**Interfaces:**
- Consumes: `ModelAdapter`, compiled model/benchmark manifests, admitted dataset/sample-index artifacts.
- Produces: exact OCR metrics, `HostProbe`, `BenchmarkReceipt`, `run_benchmark`, `validate_target_m5_max`.

- [ ] **Step 1: Write focused RED metric and benchmark tests.**

Assert exact empty/nonempty CER/WER cases, no binary float output, deterministic sample ordering, threshold pass/fail, one warm-up excluded from timing, exact measured-run count, peak-memory capture, adapter error preservation, and no retry/substitution. Add:

```python
def test_server_shaped_fake_host_cannot_emit_target_acceptance() -> None:
    receipt = run_benchmark(
        store=self.store,
        artifact_root=self.artifact_root,
        adapter=self.adapter,
        model_manifest_sha256=self.model_sha256,
        benchmark_spec_sha256=self.spec_sha256,
        host_probe=FakeTargetHostProbe(),
        execution_context=ExecutionContext.TEST,
    )
    self.assertEqual(receipt.status, ReceiptStatus.PASSED)
    self.assertFalse(receipt.target_host_eligible)
    with self.assertRaises(PreconditionError):
        build_target_acceptance(receipt, execution_context=ExecutionContext.TEST)
```

- [ ] **Step 2: Run the benchmark test once and record RED.**

```bash
PYTHONPATH=src python3 -m unittest tests.test_ml_benchmark -v
```

Expected: import failures for `metrics`, `host`, and `benchmark`.

- [ ] **Step 3: Implement exact OCR metrics.**

Use integer Levenshtein distance and `Decimal` division:

```python
def character_error_rate(expected: str, actual: str) -> Decimal:
    denominator = max(len(expected), 1)
    return Decimal(_edit_distance(tuple(expected), tuple(actual))) / Decimal(denominator)


def word_error_rate(expected: str, actual: str) -> Decimal:
    expected_words = tuple(expected.split())
    actual_words = tuple(actual.split())
    denominator = max(len(expected_words), 1)
    return Decimal(_edit_distance(expected_words, actual_words)) / Decimal(denominator)
```

Normalize only operations declared by the immutable benchmark spec. The harness must not silently lowercase, strip punctuation, normalize units, or remove title-block tokens.

- [ ] **Step 4: Implement honest cross-platform host discovery.**

Expose:

```python
class HostProbe(Protocol):
    def collect(self) -> HostDescriptor:
        raise NotImplementedError


def collect_host_descriptor() -> HostDescriptor:
    system = platform.system()
    if system == "Darwin":
        return _collect_macos_host()
    if system == "Windows":
        return _collect_windows_host()
    return _collect_generic_host()


def validate_target_m5_max(host: HostDescriptor) -> tuple[bool, tuple[str, ...]]:
    failures = []
    if host.os_name != "Darwin":
        failures.append("TARGET_OS_MISMATCH")
    if host.cpu_name != "Apple M5 Max":
        failures.append("TARGET_CHIP_MISMATCH")
    if host.cpu_cores != 18:
        failures.append("TARGET_CPU_CORE_MISMATCH")
    if host.gpu_cores != 40:
        failures.append("TARGET_GPU_CORE_MISMATCH")
    if host.memory_bytes < 128_000_000_000:
        failures.append("TARGET_MEMORY_MISMATCH")
    return not failures, tuple(failures)
```

Production macOS discovery calls `/usr/sbin/system_profiler SPHardwareDataType -json` with `shell=False` and a finite timeout. Windows discovery calls PowerShell/CIM with an argument vector and `shell=False`. Callers cannot provide actual hardware fields through CLI flags. The execution context is set internally to `PRODUCTION` only by the installed CLI path; test injection remains permanently ineligible for a target receipt.

- [ ] **Step 5: Implement deterministic benchmark execution and receipts.**

Expose:

```python
@dataclass(frozen=True)
class BenchmarkReceipt:
    protocol: str
    status: ReceiptStatus
    model_manifest_sha256: str
    benchmark_spec_sha256: str
    dataset_manifest_sha256: str
    metrics: Mapping[str, str]
    thresholds: Mapping[str, str]
    latency_ns: Mapping[str, int]
    peak_memory_bytes: int
    host: HostDescriptor
    runtime_lock_sha256: str
    failure_sample_ids: tuple[str, ...]
    failure_set_review_sha256: str | None
    target_host_eligible: bool
    receipt_sha256: str


def run_benchmark(
    *,
    store: MlRegistryStore,
    artifact_root: Path,
    adapter: ModelAdapter,
    model_manifest_sha256: str,
    benchmark_spec_sha256: str,
    host_probe: HostProbe,
    execution_context: ExecutionContext,
) -> BenchmarkReceipt:
    return BenchmarkRunner(
        store=store,
        artifact_root=artifact_root,
        host_probe=host_probe,
        execution_context=execution_context,
    ).run(adapter, model_manifest_sha256, benchmark_spec_sha256)
```

The runner requires a dataset disposition permitting evaluation, passed artifact/scan receipts, no training overlap, exact model/runtime lock, explicit device, and admitted benchmark inputs. It runs the declared warm-up count and measured repetitions, uses `time.perf_counter_ns`, records every sample failure, computes aggregate metrics in sample-ID order, and evaluates decimal thresholds exactly. An OOM records failure for the exact model. A smaller qualified model may be run only as a new explicit benchmark request and new receipt; it does not replace the failed result.

Freeze the first benchmark contract as `HELIOS_TITLE_BLOCK_OCR_V1`, task `TITLE_BLOCK_TEXT_REGION_OCR`, with these declared conditions:

- an admitted `EVALUATION` dataset of rights-cleared mechanical title-block regions;
- aggregate CER no greater than `0.08`;
- aggregate WER no greater than `0.20`;
- sheet-number exact-match rate at least `0.90`;
- empty-output count `0`;
- runtime-error count `0`;
- explicit `mps` device with no fallback for the target-host receipt;
- median and p95 latency plus peak memory recorded, with any resource ceiling taken from the admitted model manifest;
- one warm-up and three measured repetitions per sample;
- accepted failure-set review required before qualification.

Do not add a benchmark-spec fixture claiming a real dataset digest. Tests construct a clearly labeled non-acceptance spec in a temporary directory. The real spec is imported only after an actual dataset manifest exists.

- [ ] **Step 6: Run the focused benchmark test once and record GREEN.**

Run the Step 2 command. Expected: all tests pass without optional ML dependencies or target hardware.

- [ ] **Step 7: Commit the benchmark harness.**

```bash
git add src/helios_takeoff_core/ml/metrics.py \
  src/helios_takeoff_core/ml/host.py \
  src/helios_takeoff_core/ml/benchmark.py \
  tests/test_ml_benchmark.py
git commit -m "feat: add frozen ML benchmark harness"
```

---

### Task 6: Implement Kaggle/Hugging Face dataset rights, quarantine, and leakage intake

**Files:**
- Create: `src/helios_takeoff_core/ml/migrations/002_dataset_registry.sql`
- Create: `src/helios_takeoff_core/ml/dataset_intake.py`
- Create: `tests/test_ml_dataset_intake.py`
- Modify: `src/helios_takeoff_core/ml/store.py`
- Modify: `src/helios_takeoff_core/ml/contracts.py`
- Modify: `src/helios_takeoff_core/ml/canonical.py`

**Interfaces:**
- Consumes: dataset compiler, artifact/scan receipts, external content-addressed sample index.
- Produces: immutable dataset imports, rights review, exact duplicate/leakage receipt, final intake decision.

- [ ] **Build Fabric prerequisite: freeze and validate `DELIVERABLE_D_DATASET_INTAKE`.**

After the `DELIVERABLE_C_MODEL_REGISTRY` `IntegrationReceipt` is accepted, create `build_control/tasks/deliverable-d-dataset-intake.json`, add the dependent graph node `DELIVERABLE_D_DATASET_INTAKE`, and transfer the shared-file/interface ownership explicitly. Validate the manifest and graph before editing Task 6 production files. Record the actual `LOCAL_ADAPTER` or `EXTERNAL_SESSION`; an unavailable external provider requires an honest reroute event.

- [ ] **Step 1: Write focused RED tests for rights and quarantine ordering.**

Assert:

- origin `HUGGING_FACE` or `KAGGLE` never implies any right;
- `UNKNOWN`, `REVIEW_REQUIRED`, or `PROHIBITED` access/copying blocks parsing;
- commercial/training restrictions can yield `RESTRICTED` with exact permitted purposes but not unrestricted acceptance;
- archive parsing is rejected before hash, archive-safety, and malware receipts pass;
- private/proprietary risk blocks training and external upload;
- rejected/restricted/accepted decisions append and prior records cannot be changed.

- [ ] **Step 2: Write focused RED leakage tests.**

Use content-addressed JSONL sample indexes and assert detection of:

- duplicate artifact SHA-256;
- duplicate sample content SHA-256;
- duplicate normalized-text SHA-256;
- duplicate perceptual fingerprint when the manifest declares it required;
- overlap between `TRAINING` and `EVALUATION` manifests;
- inconclusive/missing required fingerprints.

An empty overlap list with all required detectors complete returns `PASSED`; any overlap returns `FAILED`; incomplete required coverage returns `BLOCKED`.

- [ ] **Step 3: Run the dataset intake test once and record RED.**

```bash
PYTHONPATH=src python3 -m unittest tests.test_ml_dataset_intake -v
```

Expected: missing migration/service interfaces.

- [ ] **Step 4: Add immutable dataset tables in migration 002.**

Create `ml_dataset_manifests`, `ml_dataset_sample_indexes`, `ml_dataset_rights_receipts`, `ml_dataset_leakage_receipts`, and `ml_dataset_intake_receipts`. Each table has an ID, unique canonical receipt/manifest SHA-256, canonical JSON, created-at timestamp, closed status/disposition check, and foreign keys to the dataset manifest digest. Add update/delete rejection triggers.

`ml_dataset_manifests` has unique `(provider, dataset, revision, manifest_version)` and columns for `purpose`, quarantine state, and canonical JSON. The canonical JSON remains authoritative; columns are query projections.

- [ ] **Step 5: Implement dataset sample and intake contracts.**

```python
@dataclass(frozen=True)
class SampleFingerprint:
    sample_id: str
    content_sha256: str
    normalized_text_sha256: str | None
    perceptual_fingerprint: str | None
    split: str


@dataclass(frozen=True)
class LeakageReceipt:
    status: ReceiptStatus
    candidate_dataset_sha256: str
    reference_dataset_sha256s: tuple[str, ...]
    detector_names: tuple[str, ...]
    overlap_sample_ids: tuple[str, ...]
    receipt_sha256: str


@dataclass(frozen=True)
class DatasetIntakeReceipt:
    status: ReceiptStatus
    disposition: DatasetDisposition
    dataset_manifest_sha256: str
    permitted_purposes: tuple[DatasetPurpose, ...]
    reason_codes: tuple[str, ...]
    receipt_sha256: str


def check_dataset_overlap(
    candidate_manifest: CompiledDatasetManifest,
    candidate_samples: Sequence[SampleFingerprint],
    references: Mapping[str, Sequence[SampleFingerprint]],
) -> LeakageReceipt:
    return DatasetLeakageChecker().check(
        candidate_manifest,
        candidate_samples,
        references,
    )


def decide_dataset_intake(
    *,
    manifest: CompiledDatasetManifest,
    artifact_receipts: Sequence[ArtifactReceipt],
    scan_receipts: Sequence[ArtifactScanReceipt],
    rights_receipt: Mapping[str, object],
    leakage_receipts: Sequence[LeakageReceipt],
) -> DatasetIntakeReceipt:
    return DatasetIntakeService().decide(
        manifest=manifest,
        artifact_receipts=artifact_receipts,
        scan_receipts=scan_receipts,
        rights_receipt=rights_receipt,
        leakage_receipts=leakage_receipts,
    )
```

Enforce the append-only state sequence:

```text
QUARANTINED
  -> HASH_VERIFIED
  -> SCAN_PASSED
  -> PARSE_ALLOWED
  -> RIGHTS_REVIEWED
  -> LEAKAGE_CHECKED
  -> ACCEPTED | RESTRICTED | REJECTED
```

A state transition receipt references the immediately preceding receipt digest. Corrections create a successor manifest/receipt. Dataset manifests record exact provider/revision, license/source provenance, original collection method when known, target task/domain relevance, annotation format, quality sample receipt, geographic/discipline/drawing-type coverage, private/proprietary risk, required detectors, purpose/split, and permitted data classes.

- [ ] **Step 6: Add dataset store operations.**

Add:

```python
def import_dataset_manifest(self, manifest: CompiledDatasetManifest) -> str:
    return self._import_dataset_manifest(manifest)


def record_dataset_intake_receipt(
    self,
    receipt: DatasetIntakeReceipt,
) -> str:
    return self._record_dataset_intake_receipt(receipt)


def get_dataset_manifest(self, sha256: str) -> dict[str, object]:
    return self._get_canonical_document("ml_dataset_manifests", sha256)
```

Exact replay returns the prior ID; conflicting logical identity fails. Do not select a current or latest dataset implicitly.

- [ ] **Step 7: Run the focused dataset intake test once and record GREEN.**

Run the Step 3 command. Expected: all dataset tests pass without Kaggle/HF credentials, clients, downloads, or private data.

- [ ] **Step 8: Commit dataset intake.**

```bash
git add src/helios_takeoff_core/ml/migrations/002_dataset_registry.sql \
  src/helios_takeoff_core/ml/dataset_intake.py \
  src/helios_takeoff_core/ml/store.py \
  src/helios_takeoff_core/ml/contracts.py \
  src/helios_takeoff_core/ml/canonical.py \
  tests/test_ml_dataset_intake.py
git commit -m "feat: govern ML dataset intake and leakage"
```

---

### Task 7: Expose a finite CLI and offline installed-code acceptance

**Files:**
- Create: `src/helios_takeoff_core/ml_cli.py`
- Create: `src/helios_takeoff_core/ml/acceptance.py`
- Create: `tests/test_ml_cli.py`
- Create: `tests/test_ml_acceptance.py`
- Create: `tests/test_ml_wheel_resources.py`
- Create: `docs/operator/ml-registry.md`
- Modify: `pyproject.toml`
- Modify: `tests/test_package_metadata.py`

**Interfaces:**
- Consumes: all core ML seams.
- Produces: installed `helios-ml` command and `ML_CORE_CODE_ACCEPTED` receipt.

- [ ] **Step 1: Write focused RED CLI and acceptance tests.**

Assert the parser exposes only:

```text
registry init
manifest verify
model import
dataset import
artifact stage
artifact inspect
artifact admit
dataset decide
benchmark run
acceptance core
acceptance target-mac
```

Assert no `download`, `fetch`, `pull`, `latest`, `approve-quantity`, `approve-estimate`, or provider-login command exists. All output is canonical compact JSON; domain failures return exit code 1 with `{error:{code,message}}`.

The core acceptance test must assert:

```python
self.assertEqual(receipt["status"], "ML_CORE_CODE_ACCEPTED")
self.assertFalse(receipt["live_model_execution"])
self.assertEqual(receipt["target_host_status"], "PENDING")
self.assertFalse(receipt["target_host_eligible"])
self.assertEqual(receipt["network_requests"], 0)
self.assertEqual(
    receipt["deliverables"]["DELIVERABLE_C_MODEL_REGISTRY"]["status"],
    "DELIVERABLE_C_MODEL_REGISTRY_INSTALLED_CODE_ACCEPTED",
)
self.assertEqual(
    receipt["deliverables"]["DELIVERABLE_D_DATASET_INTAKE"]["status"],
    "DELIVERABLE_D_DATASET_INTAKE_INSTALLED_CODE_ACCEPTED",
)
```

It must also prove a P0 path and unrelated SQLite path are rejected, exact manifest replay is stable, tampering is caught, unsafe archives are rejected, unknown dataset rights block, exact leakage blocks, and OCR metrics/threshold evaluation execute in installed production code.

Add a separate wheel-content test whose input is the exact wheel path produced by the packaging gate:

```python
class MlWheelResourceTests(unittest.TestCase):
    def test_wheel_contains_v1_v2_and_ml_runtime_resources(self) -> None:
        wheel_path = Path(os.environ["HELIOS_TEST_WHEEL"])
        with zipfile.ZipFile(wheel_path) as archive:
            names = set(archive.namelist())
        required = {
            "helios_takeoff_core/migrations/001_core.sql",
            "helios_takeoff_core/engine/v2/migrations/001_engine_registry.sql",
            "helios_takeoff_core/engine/v2/schemas/domain-pack-v2.schema.json",
            "helios_takeoff_core/engine/v2/schemas/observation-bundle-v1.schema.json",
            "helios_takeoff_core/engine/v2/schemas/capability-matrix-v1.schema.json",
            "helios_takeoff_core/engine/v2/capability_matrices/division23-v1.json",
            "helios_takeoff_core/engine/v2/packs/airside-v2.json",
            "helios_takeoff_core/engine/v2/packs/piping-v2.json",
            "helios_takeoff_core/engine/v2/packs/equipment-v2.json",
            "helios_takeoff_core/ml/migrations/001_model_registry.sql",
            "helios_takeoff_core/ml/migrations/002_dataset_registry.sql",
        }
        self.assertEqual(required - names, set())
```

This is a wheel-content assertion, not merely a source-tree or TOML assertion. It must fail when either the v2 patterns or ML migration pattern is omitted.

- [ ] **Step 2: Run focused CLI/acceptance tests once and record RED.**

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_ml_cli tests.test_ml_acceptance tests.test_package_metadata -v
```

Expected: missing CLI/acceptance entry point and package metadata.

- [ ] **Step 3: Implement the finite JSON CLI.**

Use the existing `_JsonArgumentParser`, `_emit`, `_load_json`, and `main(argv)` style from `engine_cli.py`, but point every registry operation at `MlRegistryStore`. Artifact commands require explicit `--artifact-root`; benchmark commands require exact model and benchmark manifest digests. No command resolves `latest` or accesses a provider network.

`acceptance target-mac` calls production host discovery internally and refuses injected host JSON. It requires previously admitted manifest/artifact/dataset/benchmark digests and an accepted failure-set review digest.

- [ ] **Step 4: Implement the offline core acceptance receipt.**

Expose:

```python
def build_ml_core_acceptance(work_root: Path) -> dict[str, object]:
    work_root = Path(work_root)
    work_root.mkdir(parents=True, exist_ok=True)
    registry_path = work_root / "helios-ml-registry.sqlite3"
    artifact_root = work_root / "artifacts"
    if registry_path.exists() or artifact_root.exists():
        raise FileExistsError("refusing to overwrite ML acceptance state")
    return _run_offline_core_proof(registry_path, artifact_root)
```

The proof uses only clearly labeled non-executable JSON/text/archive fixtures created under `work_root`. It does not instantiate `HuggingFaceImageToTextAdapter.execute`. Return the exact status fields from Step 1 plus registry schema versions, canonical digests, rejected failure cases, authority exclusions, and `counts_toward=["CORE_CODE_COMPLETE"]`.

- [ ] **Step 5: Merge package metadata in place and update operator documentation.**

Add `helios-ml = "helios_takeoff_core.ml_cli:main"` inside the one existing `[project.scripts]` table and add `ml_database_schema_version = "2"` inside the one existing `[tool.helios]` table. Preserve every other script and schema-version key present when this task begins.

Never create a second `[tool.setuptools.package-data]` table, never create a second `helios_takeoff_core` key, and never replace its existing array with an ML-only array. Edit the existing array in place so its exact merged final value is:

```toml
[tool.setuptools.package-data]
helios_takeoff_core = [
  "migrations/*.sql",
  "engine/v2/migrations/*.sql",
  "engine/v2/schemas/*.json",
  "engine/v2/packs/*.json",
  "engine/v2/capability_matrices/*.json",
  "ml/migrations/*.sql",
]
```

Update `test_package_metadata.py` to assert equality with that full ordered array, in addition to the new script and ML schema version:

```python
self.assertEqual(
    project["tool"]["setuptools"]["package-data"]["helios_takeoff_core"],
    [
        "migrations/*.sql",
        "engine/v2/migrations/*.sql",
        "engine/v2/schemas/*.json",
        "engine/v2/packs/*.json",
        "engine/v2/capability_matrices/*.json",
        "ml/migrations/*.sql",
    ],
)
```

Do not add mandatory dependencies. `docs/operator/ml-registry.md` must document separate database/artifact paths, offline behavior, quarantine states, rights meanings, no-download CLI policy, target-host separation, platform lock policy, and recovery from `BLOCKED`/`UNAVAILABLE` by creating a new explicit receipt rather than mutating history.

- [ ] **Step 6: Run the focused CLI/acceptance command once and record GREEN.**

Run the Step 2 command. Expected: all cases pass in under ten minutes.

- [ ] **Step 7: Run one clean offline wheel/install gate.**

Run once from a clean verification directory:

```bash
python3 -m pip wheel --no-deps --no-build-isolation . --wheel-dir /tmp/helios-ml-wheel
python3 -m venv /tmp/helios-ml-venv
/tmp/helios-ml-venv/bin/python -m pip install --no-deps /tmp/helios-ml-wheel/helios_takeoff_core-0.1.0-py3-none-any.whl
HELIOS_TEST_WHEEL=/tmp/helios-ml-wheel/helios_takeoff_core-0.1.0-py3-none-any.whl \
  /tmp/helios-ml-venv/bin/python -m unittest tests.test_ml_wheel_resources -v
/tmp/helios-ml-venv/bin/helios-ml acceptance core --work-root /tmp/helios-ml-core-acceptance
```

Set `PIP_NO_INDEX=1` for wheel and install after confirming the build backend already exists locally. The wheel-resource test must pass before installed core acceptance and proves the existing v2 migrations, schemas, and packs survive the ML metadata edit alongside both ML migrations. On Windows, use the same wheel with `Scripts\python.exe` and `Scripts\helios-ml.exe`; do not rerun the macOS command to claim Windows acceptance.

Expected receipt: `ML_CORE_CODE_ACCEPTED`, separate nested statuses `DELIVERABLE_C_MODEL_REGISTRY_INSTALLED_CODE_ACCEPTED` and `DELIVERABLE_D_DATASET_INTAKE_INSTALLED_CODE_ACCEPTED`, `live_model_execution=false`, `target_host_status=PENDING`, and no optional ML packages installed. A failure gets at most one rerun after a substantive correction.

- [ ] **Step 8: Collect, independently review, and integrate both Build Fabric nodes.**

For `DELIVERABLE_C_MODEL_REGISTRY`, collect the actual `LOCAL_ADAPTER` or `EXTERNAL_SESSION` handoff, attach `DELIVERABLE_C_MODEL_REGISTRY_INSTALLED_CODE_ACCEPTED`, obtain a `ReviewReceipt` from a reviewer distinct from its builder, and have Codex create the `IntegrationReceipt`. Only then may its model-registry/adapter/benchmark implementation commits count toward `CORE_CODE_COMPLETE`.

After C is integrated, repeat the same sequence for `DELIVERABLE_D_DATASET_INTAKE` with its distinct handoff, distinct review, `DELIVERABLE_D_DATASET_INTAKE_INSTALLED_CODE_ACCEPTED`, and Codex `IntegrationReceipt`. The D receipt must prove migration 002, rights, quarantine, duplicate/leakage, and disposition behavior from the installed wheel. Failure of D does not rewrite or invalidate C's accepted receipt.

Use the finite Build Fabric surface:

```bash
python -m tools.helios_build collect build_control/tasks/deliverable-c-model-registry.json
python -m tools.helios_build review build_control/tasks/deliverable-c-model-registry.json
python -m tools.helios_build integrate build_control/tasks/deliverable-c-model-registry.json
python -m tools.helios_build collect build_control/tasks/deliverable-d-dataset-intake.json
python -m tools.helios_build review build_control/tasks/deliverable-d-dataset-intake.json
python -m tools.helios_build integrate build_control/tasks/deliverable-d-dataset-intake.json
```

If either handoff says `EXTERNAL_SESSION`, its review and integration receipts must preserve `target_host_eligible=false`; a provider session is builder provenance, not proof of where the installed model executed.

- [ ] **Step 9: Commit the installed core gate.**

```bash
git add src/helios_takeoff_core/ml_cli.py \
  src/helios_takeoff_core/ml/acceptance.py \
  tests/test_ml_cli.py tests/test_ml_acceptance.py tests/test_ml_wheel_resources.py \
  tests/test_package_metadata.py pyproject.toml \
  docs/operator/ml-registry.md
git commit -m "feat: expose offline ML registry acceptance"
```

---

### Task 8: Admit one real Kaggle or Hugging Face dataset without blocking core code

**Files:**
- Create after a real intake succeeds: `docs/verification/ml-dataset-intake-acceptance.md`
- Create after a real intake succeeds: content-addressed sanitized receipt under `build_control/outcomes/`
- No installed-code modification is permitted in this task.

**Interfaces:**
- Consumes: an owner-approved real dataset manifest, pre-staged bytes, scanner receipts, rights review, and reference sample indexes.
- Produces: `DATASET_INTAKE_ACCEPTED` or `DATASET_INTAKE_RESTRICTED`; neither status may be fabricated when inputs are absent.

- [ ] **Step 1: Check the external-input gate once.**

The operator-owned state root uses these fixed paths:

```text
/Users/Shared/helios-ml-state/registry.sqlite3
/Users/Shared/helios-ml-state/artifacts/
/Users/Shared/helios-ml-state/intake/dataset-manifest.json
/Users/Shared/helios-ml-state/intake/dataset-archive
/Users/Shared/helios-ml-state/intake/sample-index.jsonl
/Users/Shared/helios-ml-state/intake/rights-review.json
/Users/Shared/helios-ml-state/intake/malware-scan.json
```

If any input is absent, record `DATASET_INTAKE_PENDING` in the parent cycle status and stop this task. That pending state does not fail or invalidate `ML_CORE_CODE_ACCEPTED`.

- [ ] **Step 2: Verify exact provider identity and source rights before parsing.**

The manifest must name `HUGGING_FACE` with immutable dataset revision or `KAGGLE` with exact dataset version. Review the original license/source pages independently and record the five separate rights findings, source provenance/original collection method when known, domain relevance, annotations, coverage, and private/proprietary risk. Origin alone is never a positive finding.

- [ ] **Step 3: Stage, scan, admit, index, and check leakage once.**

Use the installed `helios-ml` commands with the fixed state-root paths. The archive remains in quarantine until digest, archive-safety, and malware receipts all pass. Compare exact content, normalized-text, and every manifest-required perceptual fingerprint against all registered evaluation/training reference indexes. Any overlap or incomplete required detector returns `BLOCKED` or `FAILED` and ends the attempt.

- [ ] **Step 4: Record the immutable intake decision and sanitized receipt.**

An accepted receipt includes provider/dataset/revision, manifest/archive/sample-index digests, five rights findings, provenance and coverage review digests, scan receipts, leakage reference corpus digests, exact overlap count, disposition, permitted purposes, reviewer identity, and timestamp. The sanitized Git receipt contains no dataset bytes, credentials, authenticated URLs, or private paths.

- [ ] **Step 5: Commit only genuine acceptance evidence.**

After successful intake:

```bash
git add docs/verification/ml-dataset-intake-acceptance.md build_control/outcomes
git commit -m "verify: record governed dataset intake"
```

If disposition is rejected, commit the truthful rejected receipt only when it is useful audit evidence; it does not satisfy the accepted-dataset cycle criterion.

---

### Task 9: Produce the separate real M5 Max HELIOS benchmark receipt

**Files:**
- Create after the real run succeeds: `docs/verification/ml-target-mac-acceptance.md`
- Create after the real run succeeds: content-addressed sanitized benchmark and failure-review receipts under `build_control/outcomes/`
- Create after the runtime is selected and tested: `requirements/ml/hf-macos-arm64-py312.lock`
- No installed-code modification is permitted merely to turn a measured failure into success.

**Interfaces:**
- Consumes: a reviewed exact HF model manifest, admitted safetensors/config artifacts, accepted evaluation dataset, frozen `HELIOS_TITLE_BLOCK_OCR_V1` spec, platform lock, and independent failure-set reviewer.
- Produces: `TARGET_MODEL_BENCHMARK_ACCEPTED` or truthful blocked/failed receipt.

The Build Fabric adapter that authored or reviewed code is irrelevant to target-host eligibility. In particular, an `EXTERNAL_SESSION` handoff cannot be upgraded into a host receipt. Only `helios-ml acceptance target-mac` running installed production code with production host discovery on the owner's physical Mac may emit the target status.

- [ ] **Step 1: Check target inputs without downloading through the runtime CLI.**

Use these fixed operator-owned paths:

```text
/Users/Shared/helios-ml-state/intake/model-manifest.json
/Users/Shared/helios-ml-state/intake/benchmark-spec.json
/Users/Shared/helios-ml-state/intake/failure-review.json
/Users/Shared/helios-ml-state/requirements/hf-macos-arm64-py312.lock
```

The model manifest must contain real independently verified repository/revision/checksum/license/runtime facts. If it, the admitted model artifacts, accepted dataset, benchmark spec, or lock is absent, emit `TARGET_MODEL_BENCHMARK_BLOCKED` and leave `TARGET_HOST_ACCEPTED` pending. Do not select a model by popularity or create a substitute identifier.

- [ ] **Step 2: Run exact production preflight on the owner's Mac.**

Install the optional environment with the reviewed hash lock:

```bash
/Users/Shared/helios-ml-state/venv/bin/python -m pip install --require-hashes \
  -r /Users/Shared/helios-ml-state/requirements/hf-macos-arm64-py312.lock
```

Run `helios-ml acceptance target-mac` from the installed wheel. It must discover, not accept flags for, Apple M5 Max, 18 CPU cores, 40 GPU cores, and at least 128,000,000,000 bytes memory; verify exact package/lock/artifact digests; verify MPS availability; and confirm `trust_remote_code=false`, safetensors, and local-files-only behavior. Any CPU or other device fallback blocks this target receipt.

- [ ] **Step 3: Run `HELIOS_TITLE_BLOCK_OCR_V1` once.**

Run the frozen benchmark against the admitted evaluation dataset. The runner performs one declared warm-up and three measured repetitions per sample. Do not rerun an unchanged failing model. A corrected runtime, artifact, or benchmark manifest creates a successor identity and one new explicit attempt.

Required pass conditions are CER ≤ `0.08`, WER ≤ `0.20`, sheet-number exact match ≥ `0.90`, no empty outputs, no runtime errors, manifest resource ceilings met, exact host/runtime receipt, and no training/evaluation leakage.

- [ ] **Step 4: Complete independent failure-set review.**

The reviewer must not be the benchmark executor. The review lists every failed sample ID, gold/predicted digests, failure category, domain consequence, and disposition. Qualification requires an immutable review receipt with status `ACCEPTED`; a missing or rejected review blocks target acceptance even when aggregate metrics pass.

- [ ] **Step 5: Import a `QUALIFIED` successor model manifest.**

Create a successor manifest that preserves exact model/artifact identity, references the passed benchmark and failure-review receipt digests, and sets state `QUALIFIED`. Do not set `PROMOTED` unless another already qualified manifest is named as the rollback digest.

- [ ] **Step 6: Record and commit the sanitized target receipt.**

The receipt contains model/dataset/spec/artifact/lock digests, exact metrics, median/p95 latency, peak memory, exact host/runtime fields, failure-review digest, and `status=TARGET_MODEL_BENCHMARK_ACCEPTED`. It contains no weights, dataset samples, credentials, or authenticated URLs.

```bash
git add requirements/ml/hf-macos-arm64-py312.lock \
  docs/verification/ml-target-mac-acceptance.md \
  build_control/outcomes
git commit -m "verify: record M5 Max HELIOS model benchmark"
```

This commit contributes to `TARGET_HOST_ACCEPTED`; it does not retroactively alter the already independent `CORE_CODE_COMPLETE` receipt.

---

## Bounded verification and anti-test-loop policy

Follow this matrix exactly:

| Gate | Initial run | Allowed verification rerun | Stop condition |
|---|---:|---:|---|
| One task's focused tests | One RED, one GREEN | One after substantive correction | Split/escalate after the second correction round |
| Affected ML integration set | One at Task 7 handoff | One after substantive correction | Do not rerun unchanged failure |
| Full repository suite | One at ML core milestone | One after substantive correction | Record unrelated failure and stop blind reruns |
| Real dataset intake | One per immutable manifest/artifact identity | New successor identity only | Missing input stays pending; failed rights/security does not get waived |
| Real target benchmark | One per model/spec/dataset/runtime identity | New successor identity only | Failed threshold, device, host, or review stays failed/blocked |

After Task 7 focused tests pass, run the affected set once:

```bash
PYTHONPATH=src python3 -m unittest \
  tests.test_ml_manifest_compiler \
  tests.test_ml_registry_store \
  tests.test_ml_artifacts \
  tests.test_ml_supply_chain \
  tests.test_ml_huggingface_adapter \
  tests.test_ml_benchmark \
  tests.test_ml_dataset_intake \
  tests.test_ml_cli \
  tests.test_ml_acceptance \
  tests.test_package_metadata -v
```

At the ML core integration milestone, run the full suite once:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Then run once:

```bash
python3 -m compileall -q src tests
git diff --check
```

Do not run the real model benchmark on the current server. Do not download a model/dataset merely to make an installed-code test green. Do not convert `UNAVAILABLE`, `BLOCKED`, or `PENDING` to success through a fixture or manually edited receipt.

## Final acceptance mapping

| Enriched-design requirement | Plan evidence |
|---|---|
| Dedicated immutable ML registry and migration stream | Tasks 1–2 and migrations 001–002 |
| Weights/archives outside database | Task 3 external quarantine/admitted CAS |
| Exact HF repository/revision, rights, runtime, schemas, hardware | Task 1 strict model manifest |
| Checksum verification, remote code false, safetensors preference, scan isolation | Tasks 3–4 |
| Typed model observations without quantity/rule authority | Task 4 adapter contract |
| Frozen task-specific metrics, latency, memory, runtime/hardware receipt | Task 5 |
| Kaggle/HF quarantine, provenance, separate rights, duplicates/leakage | Task 6 and Task 8 |
| Evaluation/training separation | Task 6 leakage gate |
| One HELIOS-relevant target model execution | Task 9 title-block OCR gate |
| No live download or target Mac required for core code | Task 7 offline acceptance |
| Honest receipt separation | Tasks 7–9 and status table |
| Windows/macOS optional-dependency strategy | Task 4 lock policy and Task 5 host probes |
| Build Fabric immediately uses its own task/handoff/review/integration contracts | `DELIVERABLE_C_MODEL_REGISTRY` and dependent `DELIVERABLE_D_DATASET_INTAKE` self-use gates |

`CYCLE_COMPLETE` remains outside this sub-plan: it additionally requires the independent research round trip and external-worker target-host evidence defined by the controlling specification.
