# HELIOS Local Service, Python SDK, and v2 CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the accepted HELIOS v2 compiler, isolated EngineStore, and deterministic kernels through one versioned loopback service, one typed Python SDK, and a thin helios-engine v2 CLI, while integrating the sibling plan's idempotent P0 extraction-claim adapter without creating quantities or exercising approval authority.

**Architecture:** The sibling Division 23 v2-kernel plan owns all contracts, formulas, compilation, persistence, evaluation, result hashing, installed packs, and submit_engine_claims. This plan adds an application service over those interfaces, a dedicated /v2/engine WSGI router, and a composite local app that delegates existing /v1 requests unchanged to HeliosApi. HeliosClient is the shared typed HTTP boundary and structurally implements the adapter's P0Client.post_json method; the CLI calls HeliosClient and contains no engine or model logic.

**Tech Stack:** Python 3.12 standard library, dataclasses, StrEnum, typing.Protocol, urllib.request, WSGI/wsgiref, argparse, unittest, OpenAPI 3.1.

**Spec:** docs/superpowers/specs/2026-09-01-enriched-build-fabric-design.md

## Global Constraints

- The owner-approved enriched design controls, especially sections 3.3, 9.1 through 9.4, 10, 13 Deliverables E and F, and 14.
- Tasks 1 through 3 may start as soon as Tasks 1 through 5 of docs/superpowers/plans/2026-09-01-division23-v2-kernels.md have accepted contracts, compiler/formulas, EngineStore, evaluator, and assembly behavior. They do not wait for source-backed lane packs.
- Task 4 starts after the sibling plan's Task 10 `submit_engine_claims` adapter is accepted. Task 5 uses the core minimal pack immediately and adds parity cases only for each applicable source-backed lane whose sibling task has been accepted.
- A missing or unaccepted SourcePacket blocks only its airside, piping, or equipment lane pack and that lane's parity case. It does not block the local service, SDK, generic v2 CLI, P0 transport integration, or unrelated accepted lanes.
- Do not edit the sibling kernel plan or its owned contracts.py, compiler.py, formulas.py, store.py, evaluator.py, p0_adapter.py, migrations, schemas, packs, fixtures, or acceptance code.
- Existing helios.p1b.domain-pack/v1 behavior, migration 017, v1 digests/results, and unprefixed helios-engine commands remain unchanged.
- EngineStore uses its dedicated database and migration stream. The composite app rejects an engine path equal to the P0/P1A path.
- Compile and evaluate are pure. Import mutates only EngineStore. No operation selects a latest pack or activates a pack.
- The service is loopback-only. No gateway, sync service, daemon, polling, background worker, or automatic retry is added.
- SDK and CLI contain no formulas, assemblies, model logic, evidence inference, source research, or P0 lifecycle logic.
- Airside, piping, and equipment/scope service proofs use BENCHMARK fixtures and are not drawing ingestion, takeoff, or project quantities.
- PROJECT_BOUND is used only by a generated P0 adapter fixture. It remains a candidate extraction-claim path.
- The adapter may change only extraction_claims, claim_evidence, and matching idempotency_requests receipts.
- Do not add a quantity-assertion, review, takeoff, quote, estimate, approval, bid-release, agent-job, model, or research route, SDK convenience method, or CLI command.
- Leave src/helios_takeoff_core/repository.py, services.py, db.py, migrations/, and existing P0 route behavior unchanged.
- docs/openapi/p0-openapi.yaml receives additive payload documentation only; its existing generic extraction-claim contract is not narrowed.
- Use canonical compact JSON: sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False.
- Unknown fields fail closed. Decimal and confidence values remain canonical strings; no binary float enters a contract.
- No third-party runtime dependency is added.
- Each task gets one focused RED and one focused GREEN command. An unchanged failure is diagnosed before another run.
- At most two substantive correction rounds are allowed per task. The affected integration gate runs once and may run once more only after a substantive correction.
- This plan does not run the full repository suite. The enclosing enriched-build milestone owns the single full-suite and clean-wheel gates.

---

## Ownership and Files

Create:

- src/helios_takeoff_core/engine/v2/service.py — application service over upstream v2 code.
- src/helios_takeoff_core/engine/v2/wire.py — the single recursive upstream-dataclass-to-JSON serializer used by this plan.
- src/helios_takeoff_core/engine/v2/api.py — exact /v2/engine WSGI router.
- src/helios_takeoff_core/local_api.py — composite /v1 plus /v2 app.
- src/helios_takeoff_core/sdk.py — loopback transport, typed DTOs, HeliosClient, and P0Client-compatible post_json.
- docs/openapi/engine-v2-openapi.yaml.
- docs/operator/engine-v2-operator-guide.md.
- tests/test_engine_v2_service.py.
- tests/test_engine_v2_api.py.
- tests/test_engine_v2_sdk.py.
- tests/test_engine_v2_p0_adapter_integration.py.

Modify:

- src/helios_takeoff_core/cli.py.
- src/helios_takeoff_core/engine_cli.py.
- docs/openapi/p0-openapi.yaml.
- docs/operator/p0-operator-guide.md.
- tests/test_cli.py.
- tests/test_engine_cli.py.
- tests/test_package_metadata.py.
- README.md.

Do not modify:

- docs/superpowers/plans/2026-09-01-division23-v2-kernels.md.
- src/helios_takeoff_core/engine/v2/contracts.py.
- src/helios_takeoff_core/engine/v2/compiler.py.
- src/helios_takeoff_core/engine/v2/formulas.py.
- src/helios_takeoff_core/engine/v2/store.py.
- src/helios_takeoff_core/engine/v2/evaluator.py.
- src/helios_takeoff_core/engine/v2/p0_adapter.py.
- src/helios_takeoff_core/engine/v2/acceptance.py.
- tests/test_engine_v2_p0_adapter.py.
- every P0/P1A repository, service, database, and migration file.

## Exact Sibling Interfaces Consumed

Dependency gates are node-specific:

- Core gate for Tasks 1–3 and the generic portion of Task 5: sibling Tasks 1–5 accepted.
- Adapter gate for Task 4: sibling Task 10 accepted; source-backed lane packs are not required.
- Lane parity gate for Task 5 and Task 6: sibling Task 6, 7, or 8 accepted for the corresponding airside, piping, or equipment case. Unaccepted lanes are reported as blocked by their source-packet node, not silently skipped or treated as passing.

- DOMAIN_PACK_PROTOCOL_V2 = "helios.engine.domain-pack/v2".
- OBSERVATION_BUNDLE_PROTOCOL = "helios.engine.observation-bundle/v1".
- RESULT_SET_PROTOCOL = "helios.engine.result-set/v1".
- compile_domain_pack(document: object) -> CompiledDomainPack.
- compile_observation_bundle(document: object) -> ObservationBundle.
- evaluate_bundle(pack: CompiledDomainPack, bundle: ObservationBundle) -> EngineResultSet.
- EngineStore(path), initialize(), import_pack(pack) -> str, and load_pack(*, pack_sha256) -> CompiledDomainPack.
- submit_engine_claims(p0_client, extraction_run_id, engine_result_set) -> tuple[str, ...].
- P0Client.post_json(path, *, payload, headers) -> Mapping[str, object].

The frozen sibling result fields are:

- EngineResultSet: protocol, mode, bundle_sha256, pack_sha256, subject_code, project_id, extraction_run_id, lines, sha256.
- EngineResultLine: line_code, kind, status, subject_code, project_id, extraction_run_id, subject_kind, subject_key, output_claim_type, calculation_basis, value, bundle_sha256, pack_sha256, rule_code, source_refs, evidence_item_ids, primary_evidence_item_id, observation_refs, confidence, blocked_reasons, formula_trace, assembly, sha256.

The sibling adapter posts:

~~~http
POST /v1/evidence-items/{primary_evidence_item_id}/claims
Idempotency-Key: engine-result:{engine_result_line_sha256}
Content-Type: application/json
~~~

The outer body fields are exactly extraction_run_id, evidence_item_ids, subject_kind, subject_key, claim_type, payload, confidence. The nested payload is exactly:

~~~json
{
  "protocol": "helios.engine.claim-payload/v1",
  "result_line_sha256": "<line digest>",
  "pack_sha256": "<pack digest>",
  "rule_code": "<rule code>",
  "source_refs": [
    {
      "source_id": "<source ID>",
      "source_identity": "<immutable identity>",
      "locator": "<exact locator>",
      "jurisdiction": "<jurisdiction>",
      "applicability": "<applicability>",
      "limitations": "<limitations>"
    }
  ],
  "bundle_sha256": "<bundle digest>",
  "evidence_sha256": "<canonical evidence-set digest>",
  "formula_trace_sha256": "<formula trace digest>",
  "calculation_basis": "MEASURED|RULE_DERIVED|ALLOWANCE",
  "typed_value": {
    "scalar_type": "DECIMAL|INTEGER|BOOLEAN|TEXT|ENUM",
    "value": "<canonical value>",
    "uom": "<UOM or null>"
  }
}
~~~

This plan consumes that adapter contract verbatim and does not define a second adapter or payload protocol.

## Build Fabric Self-Use Gates

- [ ] Before the first substantive Task 1 service edit, freeze a content-addressed immutable Build Fabric `TaskManifest` for capability key `DELIVERABLE_E_LOCAL_SERVICE_SDK` and register its task-graph node and dependency edges. Its owned paths are the exact Task 1 production/test paths listed below; its forbidden paths include `src/helios_takeoff_core/engine/v2/p0_adapter.py`, every sibling-plan v2 kernel module, and the Task 5 CLI paths. Freeze and register successor E manifests before the first Task 2 and Task 3 edits, preserving the same capability key and narrowing each manifest to that task's listed files and RED/GREEN commands. Never assign E retroactively to an already-started patch.
- [ ] Route every E task through exactly one honest non-fixture handoff: `LOCAL_ADAPTER` only after its real preflight succeeds, or `EXTERNAL_SESSION` only when the immutable assignment (task identity, base hash, owned paths, forbidden paths, interfaces, and RED/GREEN commands) was delivered before work began. Preserve raw handoff evidence outside Git and derive accepted scope from Git.
- [ ] After Task 3 is GREEN, require a reviewer distinct from the builder to accept the exact E patch; Codex then integrates the reviewed patch and records the Build Fabric `IntegrationReceipt`. Record an installed-code acceptance receipt against that integrated commit before any E production commit counts toward `PRODUCTION_SELF_USE` or `DELIVERABLE_E_LOCAL_SERVICE_SDK` becomes true.
- [ ] Before the first substantive Task 5 CLI edit, freeze a separate content-addressed immutable Build Fabric `TaskManifest` for capability key `DELIVERABLE_F_CLI_ADAPTER`, register its graph node and accepted-E dependency edge, and limit it to Task 5's CLI, CLI tests, and CLI documentation paths and commands. F must have its own non-fixture handoff, distinct-builder review, Codex `IntegrationReceipt`, and installed-code acceptance receipt; no E handoff, review, or receipt may satisfy F.
- [ ] Keep transport claims literal. An `EXTERNAL_SESSION` task never claims local preflight, worker availability, local CLI execution, or `TARGET_HOST_ACCEPTED`; it may satisfy the applicable E or F core self-use chain only. If neither an eligible `LOCAL_ADAPTER` nor a correctly preassigned `EXTERNAL_SESSION` exists, stop that production task rather than using an unrecorded fallback.

## Exact Local HTTP Contract

| Method and path | Request | Success response |
|---|---|---|
| GET /v2/engine/health | no body | {"status":"ok","api_version":"v2","domain_pack_protocol":"helios.engine.domain-pack/v2"} |
| POST /v2/engine/domain-packs/compile | {"document": <v2 pack>} | {"document": <canonical v2 pack>,"pack_sha256":"<digest>"} |
| POST /v2/engine/domain-packs/import | {"document": <v2 pack>} | {"pack_code":"<code>","version":"<version>","pack_sha256":"<digest>"} |
| GET /v2/engine/domain-packs/{pack_sha256} | no body | {"document": <canonical v2 pack>,"pack_sha256":"<digest>"} |
| POST /v2/engine/evaluations | {"observation_bundle": <strict bundle>} | recursively materialized EngineResultSet, without an envelope |

Compile and evaluate do not use P0 idempotency storage. Import is content-idempotent through EngineStore; exact replay returns the same response and same registry content. Failures use:

~~~json
{"error":{"code":"STABLE_CODE","message":"non-empty public message"}}
~~~

Codes are INVALID_JSON, VALIDATION_ERROR, NOT_FOUND, CONFLICT, PRECONDITION_FAILED, LOOPBACK_REQUIRED, METHOD_NOT_ALLOWED, and INTERNAL_ERROR.

---

### Task 1: Add the application service and one wire serializer

**Files:**

- Create: src/helios_takeoff_core/engine/v2/service.py.
- Create: src/helios_takeoff_core/engine/v2/wire.py.
- Create: tests/test_engine_v2_service.py.

**Interfaces:**

- Frozen PackIdentity(pack_code: str, version: str, pack_sha256: str).
- EngineV2Service(store: EngineStore).
- compile_pack(document: object) -> CompiledDomainPack.
- import_pack(document: object) -> PackIdentity.
- load_pack(pack_sha256: str) -> CompiledDomainPack.
- evaluate(bundle: ObservationBundle) -> EngineResultSet.
- wire_document(value: object) -> object.
- observation_bundle_document(bundle: ObservationBundle) -> dict[str, object].
- engine_result_set_document(result_set: EngineResultSet) -> dict[str, object].

- [ ] **Build Fabric prerequisite: Freeze the first E task before editing.**

Freeze `DELIVERABLE_E_LOCAL_SERVICE_SDK` with Task 1's three listed files as owned paths, the sibling kernel and adapter paths plus Tasks 2–5 paths as forbidden paths, the interfaces above, and the Step 2/Step 5 commands as its exact RED/GREEN commands. Record the chosen `LOCAL_ADAPTER` or preassigned `EXTERNAL_SESSION` transport before the first production edit.

- [ ] **Step 1: Write failing service and wire tests.**

Load tests/fixtures/engine_v2/minimal_pack.json and the sibling benchmark/minimal_bundle.json. Replace only the fixture's declared pack_sha256 with the compiled digest.

~~~python
document = load_fixture("minimal_pack.json")
compiled = compile_domain_pack(document)
service = EngineV2Service(self.store)

first = service.import_pack(document)
second = service.import_pack(document)
bundle_document = load_fixture("benchmark/minimal_bundle.json")
bundle_document["pack_sha256"] = compiled.sha256
bundle = compile_observation_bundle(bundle_document)
result = service.evaluate(bundle)

self.assertEqual(first, second)
self.assertEqual(first.pack_sha256, compiled.sha256)
self.assertEqual(result.pack_sha256, compiled.sha256)
self.assertEqual(
    engine_result_set_document(result)["sha256"],
    result.sha256,
)
~~~

Also prove:

- missing observations return BLOCKED lines;
- an unimported digest raises NotFoundError;
- exact replay leaves one pack blob;
- compile does not initialize or write a database;
- wire_document converts dataclasses, StrEnum, Mapping, tuple/list, None, str, int, and bool;
- wire_document rejects float, bytes, Path, non-string mapping keys, and unknown objects.

- [ ] **Step 2: Run RED once.**

~~~bash
PYTHONPATH=src python3 -m unittest tests.test_engine_v2_service -v
~~~

Expected: FAIL because service.py and wire.py do not exist.

- [ ] **Step 3: Implement the service.**

~~~python
@dataclass(frozen=True)
class PackIdentity:
    pack_code: str
    version: str
    pack_sha256: str


class EngineV2Service:
    def __init__(self, store: EngineStore) -> None:
        self._store = store

    def compile_pack(self, document: object) -> CompiledDomainPack:
        return compile_domain_pack(document)

    def import_pack(self, document: object) -> PackIdentity:
        pack = self.compile_pack(document)
        self._store.import_pack(pack)
        return PackIdentity(
            pack_code=str(pack.canonical_document["pack_code"]),
            version=str(pack.canonical_document["version"]),
            pack_sha256=pack.sha256,
        )

    def load_pack(self, pack_sha256: str) -> CompiledDomainPack:
        return self._store.load_pack(pack_sha256=pack_sha256)

    def evaluate(self, bundle: ObservationBundle) -> EngineResultSet:
        return evaluate_bundle(self.load_pack(bundle.pack_sha256), bundle)
~~~

Do not add latest, activation, P0 lookup, model, or source-fetch methods.

- [ ] **Step 4: Implement wire_document once.**

Use dataclasses.fields rather than asdict so StrEnum and Mapping are handled explicitly. Preserve dataclass field order, tuple order, and mapping insertion order; canonical response byte sorting happens only at the HTTP emitter.

~~~python
def observation_bundle_document(bundle: ObservationBundle) -> dict[str, object]:
    document = wire_document(bundle)
    if not isinstance(document, dict):
        raise ValidationError("observation bundle did not serialize to an object")
    return document


def engine_result_set_document(result_set: EngineResultSet) -> dict[str, object]:
    document = wire_document(result_set)
    if not isinstance(document, dict):
        raise ValidationError("engine result set did not serialize to an object")
    return document
~~~

- [ ] **Step 5: Run GREEN once.**

~~~bash
PYTHONPATH=src python3 -m unittest tests.test_engine_v2_service -v
~~~

Expected: PASS with exact replay, digest-selected evaluation, ready/blocked output, and deterministic wire documents.

- [ ] **Step 6: Commit.**

~~~bash
git add \
  src/helios_takeoff_core/engine/v2/service.py \
  src/helios_takeoff_core/engine/v2/wire.py \
  tests/test_engine_v2_service.py
git commit -m "feat: add v2 engine application service"
~~~

---

### Task 2: Expose the versioned loopback service beside unchanged P0

**Files:**

- Create: src/helios_takeoff_core/engine/v2/api.py.
- Create: src/helios_takeoff_core/local_api.py.
- Create: docs/openapi/engine-v2-openapi.yaml.
- Create: tests/test_engine_v2_api.py.
- Modify: src/helios_takeoff_core/cli.py.
- Modify: tests/test_cli.py.

**Interfaces:**

- EngineV2Api(service: EngineV2Service).
- create_engine_v2_wsgi_app(engine_database_path: str | Path) -> EngineV2Api.
- LocalHeliosApi(p0_app: HeliosApi, engine_v2_app: EngineV2Api).
- create_local_wsgi_app(p0_database_path, *, engine_database_path, artifact_root=None) -> LocalHeliosApi.
- helios-p0 serve gains optional --engine-database.

- [ ] **Build Fabric prerequisite: Freeze the Task 2 E successor before editing.**

Link this immutable `DELIVERABLE_E_LOCAL_SERVICE_SDK` successor to the accepted Task 1 integration; own only the six Task 2 files above, forbid `engine/v2/p0_adapter.py`, sibling kernel modules, `sdk.py`, and `engine_cli.py`, and freeze the Step 2/Step 6 commands. Use a new honest handoff and review for this patch; do not inherit Task 1 evidence by reference.

- [ ] **Step 1: Write failing WSGI and finite CLI tests.**

Cover all five routes, loopback IPv4/IPv6, non-loopback 403, unknown routes, method rejection, JSON object/content-type/size validation, unknown fields, error envelopes, exact import replay, explicit digest lookup, ready/blocked evaluation, and hidden internal exception text.

Snapshot all P0/P1A tables before and after v2 compile/import/load/evaluate. Require exact equality. Separately assert that import changes only the dedicated EngineStore.

~~~python
status, payload = invoke(
    app,
    "POST",
    "/v2/engine/evaluations",
    {"observation_bundle": observation_bundle_document(bundle)},
    remote_addr="127.0.0.1",
)
self.assertEqual(status, "200 OK")
self.assertEqual(payload, engine_result_set_document(expected))
~~~

Patch make_server in tests/test_cli.py so serve_forever records one call and returns; no test starts an indefinite process.

- [ ] **Step 2: Run RED once.**

~~~bash
PYTHONPATH=src python3 -m unittest \
  tests.test_engine_v2_api \
  tests.test_cli -v
~~~

Expected: FAIL because the v2 and composite APIs do not exist.

- [ ] **Step 3: Implement strict v2 routing.**

- Enforce loopback before dispatch.
- Route GET health and pack load.
- Route POST compile/import/evaluate.
- Call compile_observation_bundle for request bundles.
- Call engine_result_set_document for response results.
- Validate lowercase 64-hex path digests.
- Never call HeliosApi._mutation.
- Return 200 for compile/import/load/evaluate, including exact import replay.
- Emit compact sorted JSON and stable public errors.

~~~python
if method == "POST" and path == "/v2/engine/evaluations":
    body = self._read_json(environ)
    self._require_fields(body, {"observation_bundle"})
    bundle = compile_observation_bundle(body["observation_bundle"])
    return self._respond(
        start_response,
        "200 OK",
        engine_result_set_document(self.service.evaluate(bundle)),
    )
~~~

- [ ] **Step 4: Compose without changing HeliosApi.**

~~~python
class LocalHeliosApi:
    def __init__(self, p0_app: HeliosApi, engine_v2_app: EngineV2Api) -> None:
        self._p0_app = p0_app
        self._engine_v2_app = engine_v2_app

    def __call__(self, environ, start_response):
        path = str(environ.get("PATH_INFO", "/"))
        if path == "/v2/engine" or path.startswith("/v2/engine/"):
            return self._engine_v2_app(environ, start_response)
        return self._p0_app(environ, start_response)
~~~

create_local_wsgi_app rejects equal resolved P0 and engine paths before initialization. cli.py keeps init and P0-only serve unchanged; when --engine-database is present it validates a loopback host and calls create_local_wsgi_app.

- [ ] **Step 5: Write exact OpenAPI 3.1.**

Document only the five routes. Use additionalProperties: false for request/envelope schemas. State:

- BENCHMARK has no P0 evidence identity and cannot enter submit_engine_claims;
- PROJECT_BOUND results are candidates only;
- every evaluation names pack_sha256;
- no latest/activation, quantity, approval, model, research, worker, or background route exists.

- [ ] **Step 6: Run GREEN once.**

~~~bash
PYTHONPATH=src python3 -m unittest \
  tests.test_engine_v2_api \
  tests.test_cli -v
~~~

Expected: PASS with v1 delegation unchanged, loopback enforced, and no P0/P1A mutation from v2 routes.

- [ ] **Step 7: Commit.**

~~~bash
git add \
  src/helios_takeoff_core/engine/v2/api.py \
  src/helios_takeoff_core/local_api.py \
  src/helios_takeoff_core/cli.py \
  docs/openapi/engine-v2-openapi.yaml \
  tests/test_engine_v2_api.py \
  tests/test_cli.py
git commit -m "feat: expose versioned local engine service"
~~~

---

### Task 3: Add the typed Python SDK and P0Client transport

**Files:**

- Create: src/helios_takeoff_core/sdk.py.
- Create: tests/test_engine_v2_sdk.py.

**Interfaces:**

- HeliosApiError(status_code: int, code: str, message: str).
- JsonTransport.request(method, path, *, body=None, headers=None) -> Mapping[str, object].
- HttpJsonTransport(base_url: str, timeout_seconds: float = 10.0).
- Frozen EngineResultLineView and EngineResultSetView with strict from_document and to_document.
- HeliosClient.compile_pack, import_pack, load_pack, evaluate, and post_json.

- [ ] **Build Fabric prerequisite: Freeze the Task 3 E successor before editing.**

Link this immutable `DELIVERABLE_E_LOCAL_SERVICE_SDK` successor to the accepted Task 2 integration; own only `src/helios_takeoff_core/sdk.py` and `tests/test_engine_v2_sdk.py`, forbid CLI, P0, adapter, and sibling kernel paths, and freeze the Step 2/Step 5 commands. Use a new honest handoff and distinct review for this patch.

- [ ] **Step 1: Write failing recording-transport and real-loopback tests.**

~~~python
result = HeliosClient(recording_transport).evaluate(bundle)
self.assertEqual(result.sha256, expected.sha256)
self.assertEqual(result.to_document(), engine_result_set_document(expected))
self.assertEqual(
    recording_transport.requests,
    [(
        "POST",
        "/v2/engine/evaluations",
        {"observation_bundle": observation_bundle_document(bundle)},
        {},
    )],
)
~~~

Cover non-loopback/credential/query/fragment base URLs, malformed success/error objects, extra fields, invalid digests/enums/line shapes, and HTTP errors without traceback. Include one finite wsgiref test of urllib transport.

- [ ] **Step 2: Run RED once.**

~~~bash
PYTHONPATH=src python3 -m unittest tests.test_engine_v2_sdk -v
~~~

Expected: FAIL because sdk.py does not exist.

- [ ] **Step 3: Implement transport and views.**

HttpJsonTransport uses urllib.request, canonical JSON request bytes, Content-Type only with a body, and exact structured error parsing.

EngineResultLineView mirrors every sibling line wire field. EngineResultSetView mirrors every sibling result-set wire field. Their strict constructors reject unknown fields and invalid nested value/source_refs/formula_trace/assembly shapes, preserve tuples, and validate digest syntax. They validate service documents but do not recalculate formulas or create a second result hash.

- [ ] **Step 4: Implement HeliosClient.**

~~~python
class HeliosClient:
    def __init__(self, transport: JsonTransport) -> None:
        self._transport = transport

    def evaluate(self, bundle: ObservationBundle) -> EngineResultSetView:
        response = self._transport.request(
            "POST",
            "/v2/engine/evaluations",
            body={"observation_bundle": observation_bundle_document(bundle)},
        )
        return EngineResultSetView.from_document(response)

    def post_json(
        self,
        path: str,
        *,
        payload: Mapping[str, object],
        headers: Mapping[str, str],
    ) -> Mapping[str, object]:
        if not path.startswith("/v1/"):
            raise ValidationError("P0 post_json path must begin with /v1/")
        return self._transport.request(
            "POST",
            path,
            body=payload,
            headers=headers,
        )
~~~

compile_pack and load_pack verify response digests by calling the shared compile_domain_pack on returned canonical documents. import_pack returns PackIdentity. post_json exists to satisfy the sibling adapter's P0Client Protocol; the SDK does not derive claims and exposes no quantity or authority convenience method.

- [ ] **Step 5: Run GREEN once.**

~~~bash
PYTHONPATH=src python3 -m unittest tests.test_engine_v2_sdk -v
~~~

Expected: PASS with exact typed results, loopback-only transport, and structural P0Client compatibility.

- [ ] **Step 6: Commit.**

~~~bash
git add \
  src/helios_takeoff_core/sdk.py \
  tests/test_engine_v2_sdk.py
git commit -m "feat: add typed local HELIOS sdk"
~~~

- [ ] **Step 7: Close the E self-use chain.**

For each Task 1–3 E manifest, retain its non-fixture Git-derived handoff, distinct accepted review, and Codex `IntegrationReceipt`. Run the enclosing milestone's installed-code acceptance against the exact integrated Task 3 descendant and record its installed-code receipt. Only then mark `DELIVERABLE_E_LOCAL_SERVICE_SDK` true and count the three E production commits toward `PRODUCTION_SELF_USE`; an `EXTERNAL_SESSION` chain remains ineligible for `TARGET_HOST_ACCEPTED`.

---

### Task 4: Integrate the sibling P0 adapter through the real API

**Files:**

- Create: tests/test_engine_v2_p0_adapter_integration.py.
- Modify: docs/openapi/p0-openapi.yaml.
- Modify: docs/operator/p0-operator-guide.md.

**Interfaces:**

- Starts only after sibling Task 10 is accepted; it does not require sibling Tasks 6–8.
- Consumes upstream submit_engine_claims and its exact tuple[str, ...] return.
- Consumes HeliosClient.post_json as the concrete upstream P0Client.
- Does not modify p0_adapter.py or its focused unit test.

- [ ] **Step 1: Write the generated P0 fixture and full table snapshot.**

Before adapter invocation create project A, a document revision, two evidence items, and one extraction run; create project B with one evidence item. Evaluate the sibling project_bound/minimal_bundle.json after substituting the real pack, project, run, subject, and evidence IDs. Create no revision set or downstream authority record.

Snapshot every non-SQLite table as ordered row dictionaries:

~~~python
def table_snapshot(database: Database) -> dict[str, tuple[dict[str, object], ...]]:
    with database.connection() as connection:
        names = tuple(
            row["name"]
            for row in connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                ORDER BY name
                """
            )
        )
        return {
            name: tuple(
                dict(row)
                for row in connection.execute(
                    f'SELECT * FROM "{name}" ORDER BY rowid'
                )
            )
            for name in names
        }
~~~

Names come from sqlite_master, not user input.

- [ ] **Step 2: Write failing real-API replay and authority tests.**

Start the composite WSGI app on a finite loopback test server and call:

~~~python
first = submit_engine_claims(client, extraction_run_id, result_set)
after_first = table_snapshot(database)
second = submit_engine_claims(client, extraction_run_id, result_set)
after_replay = table_snapshot(database)

self.assertEqual(first, second)
self.assertEqual(after_first, after_replay)
self.assertEqual(
    set(changed_tables(before, after_first)),
    {"extraction_claims", "claim_evidence", "idempotency_requests"},
)
self.assertEqual(after_first["quantity_assertions"], before["quantity_assertions"])
self.assertEqual(after_first["evidence_items"], before["evidence_items"])
self.assertEqual(after_first["extraction_runs"], before["extraction_runs"])
~~~

Require every agent_% table and all quantity/review/takeoff/conflict/quote/estimate/release tables to be byte-for-byte unchanged.

Inspect the persisted extraction claim and require:

- outer field set exactly matches the existing P0 claim route;
- primary evidence is first and claim_evidence contains exactly the line evidence set;
- nested protocol and fields exactly match the sibling payload contract above;
- source_refs contain full SourceRef objects;
- idempotency key scope is the exact claim path and key engine-result:{line.sha256};
- no quantity assertion exists.

- [ ] **Step 3: Cover fail-closed real API cases.**

- BENCHMARK, blocked, run mismatch, duplicate evidence, missing primary, and result/line mismatch are rejected by the upstream adapter before HTTP.
- nonexistent evidence returns 404 with no claim or idempotency row.
- project B evidence under project A's run returns 400 with no claim or idempotency row.
- direct changed-body reuse of the adapter key returns 409 IDEMPOTENCY_CONFLICT.
- no recorded path contains quantity-assertions, review-decisions, takeoff-versions, estimate-versions, approvals, bid-releases, or agent-jobs.

- [ ] **Step 4: Run RED once.**

~~~bash
PYTHONPATH=src python3 -m unittest \
  tests.test_engine_v2_p0_adapter_integration \
  tests.test_engine_v2_p0_adapter \
  tests.test_api \
  tests.test_evidence_claims -v
~~~

Expected: integration test fails until HeliosClient.post_json and the composite service exist; the sibling adapter unit test remains green.

- [ ] **Step 5: Add documentation only; do not change adapter or P0 code.**

In docs/openapi/p0-openapi.yaml retain the generic ExtractionClaimCommand.payload object and add EngineClaimPayloadV1 as an informational exact schema matching the sibling payload. Do not add an engine-specific P0 route.

In docs/operator/p0-operator-guide.md state:

- engine results enter P0 only as candidate extraction claims;
- exact replay returns the same claim;
- claims do not create quantities;
- quantity review/approval remains separate human-controlled P0 authority;
- REUSE is never coerced to another P0 quantity scope.

- [ ] **Step 6: Run GREEN once.**

~~~bash
PYTHONPATH=src python3 -m unittest \
  tests.test_engine_v2_p0_adapter_integration \
  tests.test_engine_v2_p0_adapter \
  tests.test_api \
  tests.test_evidence_claims -v
~~~

Expected: PASS with only extraction_claims, claim_evidence, and idempotency_requests changed after first submission and no changes after replay.

- [ ] **Step 7: Commit.**

~~~bash
git add \
  tests/test_engine_v2_p0_adapter_integration.py \
  docs/openapi/p0-openapi.yaml \
  docs/operator/p0-operator-guide.md
git commit -m "test: prove v2 P0 claim integration boundary"
~~~

---

### Task 5: Route the finite v2 CLI through the SDK

**Files:**

- Modify: src/helios_takeoff_core/engine_cli.py.
- Modify: tests/test_engine_cli.py.
- Modify: tests/test_package_metadata.py.
- Create: docs/operator/engine-v2-operator-guide.md.
- Modify: README.md.

**Interfaces:**

- Generic compile/import/show/evaluate CLI work starts after sibling Tasks 1–5.
- Each lane-specific parity assertion starts only after that lane's sibling Task 6, 7, or 8 is accepted.
- Preserve every unprefixed v1 command.
- Add helios-engine v2 compile.
- Add helios-engine v2 import.
- Add helios-engine v2 pack show.
- Add helios-engine v2 evaluate.
- No CLI adapter-submit command is added: the typed upstream EngineResultSet-to-P0 boundary remains the Python submit_engine_claims API.

- [ ] **Build Fabric prerequisite: Freeze F independently before editing.**

Freeze `DELIVERABLE_F_CLI_ADAPTER` only after the applicable E SDK interface is accepted. Own exactly the five Task 5 files above; forbid service, SDK, P0, adapter, and sibling kernel paths; freeze the Step 2/Step 5 commands and the accepted-lane subset. Record a fresh `LOCAL_ADAPTER` or preassigned `EXTERNAL_SESSION` handoff. E's builder, handoff, review, `IntegrationReceipt`, and installed-code receipt do not satisfy F.

- [ ] **Step 1: Write failing subprocess and parity tests.**

~~~text
helios-engine v2 compile --service-url URL --input PACK [--output CANONICAL_PACK]
helios-engine v2 import --service-url URL --input PACK
helios-engine v2 pack show --service-url URL --sha256 PACK_SHA256
helios-engine v2 evaluate --service-url URL --input OBSERVATION_BUNDLE
~~~

--service-url defaults to http://127.0.0.1:8787. Preserve one compact stdout JSON line, empty stderr, exit 0 on success, exit 1 with a structured error, and canonical bytes only in compile --output.

For the sibling minimal READY and BLOCKED BENCHMARK fixtures, compare:

1. EngineV2Service.evaluate;
2. HeliosClient.evaluate;
3. helios-engine v2 evaluate subprocess output.

~~~python
self.assertEqual(cli_document, engine_result_set_document(direct_result))
self.assertEqual(cli_document, sdk_result.to_document())
self.assertEqual(
    {direct_result.sha256, sdk_result.sha256, cli_document["sha256"]},
    {direct_result.sha256},
)
~~~

Re-run existing v1 cases in tests/test_engine_cli.py.

Add separately named parity tests as their source-backed sibling lanes are accepted:

- EngineCliV2ParityTests.test_airside_service_sdk_cli_parity after sibling Task 6;
- EngineCliV2ParityTests.test_piping_service_sdk_cli_parity after sibling Task 7;
- EngineCliV2ParityTests.test_equipment_service_sdk_cli_parity after sibling Task 8.

Each accepted lane compares its direct EngineV2Service result, HeliosClient view, and CLI document. A lane without an accepted SourcePacket remains a blocked sibling node and is not converted into a skip, canned pack, or fabricated passing result.

The initial Task 5 RED/GREEN cycle contains the minimal core parity case plus only lane methods whose sibling tasks are already accepted. Later-accepted lane methods use the focused Task 6 lane command and a substantive follow-up commit; they do not reopen or rerun the generic core gate.

- [ ] **Step 2: Run RED once.**

~~~bash
PYTHONPATH=src python3 -m unittest \
  tests.test_engine_cli \
  tests.test_package_metadata -v
~~~

Expected: FAIL because the v2 command tree does not exist.

- [ ] **Step 3: Implement v2 parser and dispatch.**

Each v2 leaf gets:

~~~python
def _add_service_url(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--service-url",
        default="http://127.0.0.1:8787",
    )
~~~

Every v2 command creates:

~~~python
client = HeliosClient(HttpJsonTransport(arguments.service_url))
~~~

Do not call Database, EngineStore, compile_domain_pack, evaluate_bundle, submit_engine_claims, TakeoffRepository, or TakeoffService from v2 CLI dispatch.

Exact outputs:

- compile: {"document": <canonical pack>,"pack_sha256":"<digest>"}.
- import: {"pack_code":"<code>","version":"<version>","pack_sha256":"<digest>"}.
- pack show: {"document": <canonical pack>,"pack_sha256":"<digest>"}.
- evaluate: EngineResultSetView.to_document() directly.

- [ ] **Step 4: Document operator scope.**

README.md and docs/operator/engine-v2-operator-guide.md show:

- helios-p0 serve --database ./p0.sqlite3 --engine-database ./engine-v2.sqlite3;
- the four v2 commands;
- SDK construction;
- explicit pack digest;
- READY/BLOCKED and BENCHMARK/PROJECT_BOUND meanings;
- Python submit_engine_claims usage for a typed PROJECT_BOUND EngineResultSet;
- candidate-only P0 claims and no automatic quantity;
- CLI is an operator/proof/automation adapter, not the engine, model, or product UI.

Keep the existing helios-engine entry point and assert no helios-build entry point.

- [ ] **Step 5: Run GREEN once.**

~~~bash
PYTHONPATH=src python3 -m unittest \
  tests.test_engine_cli \
  tests.test_package_metadata -v
~~~

Expected: PASS with unchanged v1 behavior, exact minimal service/SDK/CLI v2 hash parity, and parity for every lane accepted at this point.

- [ ] **Step 6: Commit.**

~~~bash
git add \
  src/helios_takeoff_core/engine_cli.py \
  tests/test_engine_cli.py \
  tests/test_package_metadata.py \
  docs/operator/engine-v2-operator-guide.md \
  README.md
git commit -m "feat: route v2 engine CLI through the SDK"
~~~

- [ ] **Step 7: Close the F self-use chain.**

Require a reviewer distinct from the F builder to accept the exact Git-derived CLI patch. Codex integrates only that accepted patch and records its `IntegrationReceipt`; then run the enclosing milestone's installed-code CLI acceptance and record the installed-code receipt against the integrated commit. Only afterward mark `DELIVERABLE_F_CLI_ADAPTER` true or count the CLI production commit toward `PRODUCTION_SELF_USE`. `EXTERNAL_SESSION` evidence may satisfy F core self-use but never `TARGET_HOST_ACCEPTED`.

---

### Task 6: Run the bounded affected-integration gate

**Files:** No file is created solely for this gate.

- [ ] **Step 1: Run the affected test set once.**

~~~bash
PYTHONPATH=src python3 -m unittest \
  tests.test_engine_v1_compatibility \
  tests.test_engine_v2_contracts \
  tests.test_engine_v2_compiler \
  tests.test_engine_v2_formulas \
  tests.test_engine_v2_store \
  tests.test_engine_v2_evaluator \
  tests.test_engine_v2_assemblies \
  tests.test_engine_v2_p0_adapter \
  tests.test_engine_v2_service \
  tests.test_engine_v2_api \
  tests.test_engine_v2_sdk \
  tests.test_engine_v2_p0_adapter_integration \
  tests.test_engine_cli \
  tests.test_api \
  tests.test_evidence_claims \
  tests.test_cli \
  tests.test_package_metadata -v
~~~

Expected: PASS with v1 compatibility, explicit pack identity, minimal ready/blocked behavior, adapter replay, protected tables, and generic service/SDK/CLI parity. This core gate does not wait for source-backed lane packets.

- [ ] **Step 2: Run one focused parity gate for each accepted source-backed lane.**

Run only the commands whose sibling lane task is accepted:

~~~bash
PYTHONPATH=src python3 -m unittest \
  tests.test_engine_v2_airside \
  tests.test_engine_cli.EngineCliV2ParityTests.test_airside_service_sdk_cli_parity -v

PYTHONPATH=src python3 -m unittest \
  tests.test_engine_v2_piping \
  tests.test_engine_cli.EngineCliV2ParityTests.test_piping_service_sdk_cli_parity -v

PYTHONPATH=src python3 -m unittest \
  tests.test_engine_v2_equipment \
  tests.test_engine_cli.EngineCliV2ParityTests.test_equipment_service_sdk_cli_parity -v
~~~

Each executed command must pass. Record a lane whose SourcePacket or sibling task is unaccepted as BLOCKED at that dependency node; do not run its command, invent its pack, or block the core gate.

- [ ] **Step 3: Run static checks once.**

~~~bash
python3 -m compileall -q src
git diff --check
~~~

Expected: both exit 0.

- [ ] **Step 4: Hand off facts without an empty commit.**

Record task commits, RED/GREEN commands, core affected-gate result, each executed lane-parity result, blocked lane dependency IDs, parity hashes, allowed P0 deltas, unchanged protected tables, fixture modes, and correction rounds. The enclosing milestone owns the full suite, wheel inspection, installed acceptance, and verification receipt.

- [ ] **Step 5: Reconcile independent Build Fabric completion.**

Verify that E and F each name its own frozen manifest chain, honest transport, non-fixture Git-derived handoff, distinct accepted review, Codex `IntegrationReceipt`, and installed-code receipt. Reject a self-use count if either capability borrows the other's evidence, if work preceded assignment freeze, or if an `EXTERNAL_SESSION` record claims target-host execution. Task 4's tests and documentation prove the P0 authority boundary but are not substantive production code and do not flip E or F.

## Anti-Test-Loop Budget

| Gate | Initial budget | Verification budget | Stop condition |
|---|---:|---:|---|
| Task 1 service/wire | 1 RED + 1 GREEN | 1 after substantive correction | Reconcile sibling interface rather than duplicate it |
| Task 2 API/composition | 1 RED + 1 GREEN | 1 after substantive correction | Split routing from serve bootstrap |
| Task 3 SDK | 1 RED + 1 GREEN | 1 after substantive correction | Split transport from DTO parsing |
| Task 4 adapter integration | 1 RED + 1 GREEN | 1 after substantive correction | Stop immediately on unauthorized table mutation |
| Task 5 CLI | 1 RED + 1 GREEN | 1 after substantive correction | Split parser/output while preserving v1 |
| Core affected integration | 1 | 1 after substantive correction | Escalate or split; never rerun unchanged failure |
| Each accepted lane parity | 1 | 1 after substantive lane correction | Keep a source-blocked lane blocked; never fabricate or repeatedly probe it |
| Full suite | 0 in this plan | enclosing milestone only | Never substitute broad reruns for diagnosis |

No command is placed in a retry loop, poller, watcher, or background verification process. An interrupted command is recorded as unknown and investigated before another run.

## Commit Boundaries

| Commit | Executable capability | Self-use accounting |
|---|---|---|
| feat: add v2 engine application service | Shared upstream compiler/store/evaluator exposed through one application service and wire serializer | E descendant; does not count until the complete E receipt chain closes |
| feat: expose versioned local engine service | Exact loopback /v2 routes composed beside unchanged P0 /v1 | E descendant; does not count until the complete E receipt chain closes |
| feat: add typed local HELIOS sdk | Typed loopback client plus structural P0Client transport | Closes `DELIVERABLE_E_LOCAL_SERVICE_SDK` only after review, Codex integration, and installed-code receipt |
| test: prove v2 P0 claim integration boundary | Real API replay changes only the three permitted P0 tables | Authority evidence only; flips neither E nor F |
| feat: route v2 engine CLI through the SDK | Finite v2 CLI with exact service/SDK/CLI parity and unchanged v1 | Closes `DELIVERABLE_F_CLI_ADAPTER` only through its separate review, Codex integration, and installed-code receipt |

No commit may claim drawing ingestion, takeoff, full Division 23 coverage, model execution, research acceptance, quantity approval, pricing, estimate approval, bid release, native UI, or target-host acceptance.
