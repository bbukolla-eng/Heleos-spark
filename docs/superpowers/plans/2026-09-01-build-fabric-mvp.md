# Build Fabric MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the finite, repository-local HELIOS Build Fabric MVP core: immutable task contracts, collision-safe task graphs, exact host preflight, isolated finite worker attempts, independent review, controlled integration receipts, truthful status, and a frozen extension seam for the separately owned ATHENA research round trip.

**Architecture:** Git-tracked build_control contains JSON Schemas, policy profiles, content-addressed manifests, chained append-only JSONL events, patches, and sanitized receipts. Repository-only tools/helios_build reads that state and uses an operator-configured external state root for adapter configuration, credentials, raw logs, sessions, process metadata, and disposable worktrees. There is no control database, daemon, command discovery, automatic polling, or automatic retry.

**Tech Stack:** Python 3.12; Python standard library; Git CLI; argparse; dataclasses; enum; hashlib; json; pathlib; subprocess; unittest; optional development-only jsonschema>=4.23,<5 implementing JSON Schema Draft 2020-12.

**Spec:** [Enriched Build Fabric Design](../specs/2026-09-01-enriched-build-fabric-design.md)

## Global Constraints

- Keep the build fabric outside src. The installed wheel must contain none of tools/helios_build, build_control, provider configuration/adapters, browser/NotebookLM code, or a helios-build console entry point.
- Keep setuptools discovery rooted at src. Add no build-control package-data rule and no runtime dependency.
- No tools/helios_build module may import helios_takeoff_core, sqlite3, or a database client; submit agent_jobs; name or write a P0/P1A table; or be imported by installed runtime modules.
- Canonical state is file based: content-addressed JSON plus append-only chained JSONL. Do not add another authority database.
- Git may contain schemas, policy profiles, manifests, hashes, patches, and sanitized receipts. Credentials, environment values, executable paths, raw provider output, authenticated state, private sources, and worktrees remain under an external state root.
- The external state root must resolve outside the repository and must not be a home directory, filesystem root, repository root, or repository descendant.
- Never guess a provider command. AVAILABLE requires a successful finite preflight of the configured absolute executable, its content digest, and the immutable normalized adapter configuration on the current host.
- A real current Codex, Claude, Kimi, Grok, Cursor, or Copilot session may instead work through transport EXTERNAL_SESSION. That path requires an assignment frozen before work, exact task/base/patch hashes, identified worker/session, raw-evidence bytes retained outside Git, Git-derived scope, distinct independent review, and Codex integration.
- EXTERNAL_SESSION never creates or references a preflight receipt, never changes worker availability, never claims local CLI execution, and is permanently ineligible for TARGET_HOST_ACCEPTED.
- At most three implementation BuildTask nodes may be DISPATCHED. One writer owns each file, glob, module, migration stream, schema, and public interface through acceptance/integration.
- Builder, reviewer, and Codex integrator are distinct roles. A worker never reviews its own patch. Only Codex stages canonical changes, commits, pushes, opens pull requests, and merges.
- The exact run identity is task-manifest SHA-256, base commit SHA, and adapter-configuration SHA-256. Replay returns current state or the existing receipt and launches no process.
- Every dispatch is one finite attempt. Retry, reroute, correction, or checkpoint resume requires an explicit routing event and immutable successor task manifest. There is no polling or hidden retry.
- Timeout kills the process group and records OUTCOME_UNKNOWN. A deterministic launch, configuration, or result failure records FAILED. Neither may be converted into success.
- A checkpoint is collectable only at a boundary named in the task. Resume references the verified checkpoint from a successor manifest.
- Git determines changed paths. Worker claims cannot hide an out-of-scope or forbidden file.
- Default budgets are 300 seconds focused, 600 seconds affected integration, and 1200 seconds milestone. A builder/reviewer pair receives at most two correction rounds.
- Run one focused RED and one focused GREEN command per task. Run the affected integration gate once, with at most one rerun after a substantive successor correction. Run the milestone gate once, with the same one-rerun limit. Never rerun an unchanged failure.
- Fixture adapters prove mechanics only. They cannot satisfy TARGET_HOST_ACCEPTED or become provider-success receipts.
- Deliverable A may merge as an enabling gate, but A alone is not engine progress. The first progress milestone is A plus Deliverable B, and the fabric must immediately manage real B-through-F contracts.

## Locked Files and Responsibilities

    pyproject.toml
    build_control/
      README.md
      schemas/
        common-v1.schema.json
        task-manifest-v1.schema.json
        worker-profile-v1.schema.json
        adapter-config-v1.schema.json
        graph-manifest-v1.schema.json
        lifecycle-event-v1.schema.json
        routing-event-v1.schema.json
        preflight-receipt-v1.schema.json
        attempt-manifest-v1.schema.json
        checkpoint-v1.schema.json
        artifact-manifest-v1.schema.json
        command-receipt-v1.schema.json
        worker-handoff-v1.schema.json
        review-receipt-v1.schema.json
        integration-receipt-v1.schema.json
        external-session-assignment-v1.schema.json
        external-session-handoff-v1.schema.json
        external-session-review-v1.schema.json
      worker_profiles/
        codex-control-v1.json
        codex-builder-v1.json
        codex-reviewer-v1.json
        claude-builder-v1.json
        kimi-builder-v1.json
        grok-builder-v1.json
        cursor-builder-v1.json
        copilot-builder-v1.json
      tasks/.gitkeep
      graph/events.jsonl
      graph/routing.jsonl
      graph/receipts/.gitkeep
    tools/__init__.py
    tools/helios_build/
      __init__.py
      __main__.py
      cli.py
      errors.py
      types.py
      canonical.py
      schemas.py
      paths.py
      store.py
      ledger.py
      ownership.py
      graph.py
      profiles.py
      process.py
      worktrees.py
      doctor.py
      dispatch.py
      checkpoints.py
      verification.py
      collect.py
      review.py
      integrate.py
      external_sessions.py
      status.py
    tests/
      build_fabric_support.py
      test_build_contracts.py
      test_build_store.py
      test_build_graph.py
      test_build_preflight.py
      test_build_dispatch.py
      test_build_review_integration.py
      test_build_external_sessions.py
      test_build_cli.py
      test_build_package_boundary.py
      test_build_authority_boundary.py
    docs/verification/build-fabric-mvp-acceptance.md

Shared primitives have one owner: canonical.py performs strict JSON and hashing; schemas.py validates local schemas; paths.py separates roots; store.py publishes CAS objects; ledger.py appends/replays events. graph.py owns state and DAG semantics; ownership.py owns collisions. profiles.py/process.py/doctor.py own adapter policy and preflight. dispatch.py launches; collect.py derives artifacts; review.py invokes a different worker; integrate.py verifies a Codex-created commit but never pushes or merges. cli.py parses, routes, and prints compact JSON through the frozen command-registration seam defined below.

The dedicated ATHENA plan exclusively owns every research/source schema, ATHENA profile or instruction, build_control/source_registry, build_control/source_packets, build_control/outcomes, tools/helios_build/research.py, tools/helios_build/source_registry.py, tools/helios_build/research_receipts.py, and dedicated research test. This plan neither creates nor modifies those paths.

## Exact Contracts

Every schema uses JSON Schema Draft 2020-12, a local https://helios.local/build-control/schemas/ identifier, additionalProperties false, and an explicit required list. Digests are lowercase 64-hex; Git commits are lowercase 40-hex; paths are repository-relative POSIX strings; times are RFC 3339 UTC ending in Z; durations/cost/confidence are integers. Strict loading rejects duplicate keys, floats, NaN/infinity, invalid UTF-8, and non-object roots.

- task-manifest-v1, protocol helios.build.task-manifest/v1: task_id, capability_id, capability, node_type=BuildTask, base_commit_sha, dependency_node_ids, roles, ownership, frozen_interfaces, schema_impact, required_source_record_ids, required_source_packet_ids, evidence_threshold, deliverables, acceptance, budgets, checkpoint_boundaries, conditions, correction_round, supersedes_task_manifest_sha256, resume_from_checkpoint_sha256, created_at, created_by_profile_id.
- roles: builder_profile_id, reviewer_profile_id, integrator_profile_id. Integrator is exactly codex-control-v1 and builder differs from reviewer.
- ownership: files, path_globs, modules, migrations, schemas, public_interfaces, forbidden_paths.
- acceptance: behaviors and commands. Each command has command_id, gate, argv, cwd, expected_exit_code, timeout_seconds; argv is an array, never shell text.
- budgets: implementation_seconds, cost_microusd, focused_test_seconds, affected_integration_seconds, milestone_seconds, maximum_correction_rounds.
- conditions: exact arrays stop, block, reroute, escalation; each item has code, when, action.
- worker-profile-v1, protocol helios.build.worker-profile/v1: profile_id, worker_id, display_name, roles, transports, adapter_protocol, skills, plugins, allowed_tools, allowed_data_classes, allowed_task_purposes, authority_limits, max_concurrency. Skill/plugin items require name, revision, allowed_tools, purpose; empty means no grant. transports is a non-empty subset of LOCAL_ADAPTER and EXTERNAL_SESSION.
- adapter-config-v1, protocol helios.build.adapter-config/v1: host_id and adapters. Each adapter has worker_id, adapter_name, adapter_revision, executable, executable_sha256, preflight_argv, dispatch_argv, environment_variable_names, preflight_timeout_seconds, attempt_timeout_seconds, max_capture_bytes, result_protocol. This document exists only outside Git.
- graph-manifest-v1: protocol, graph_id, nodes, edges, created_at, created_by_profile_id. Nodes have node_id, node_type, manifest_sha256. Edges have edge_type, from_node_id, to_node_id.
- lifecycle-event-v1: protocol, sequence, node_id, prior_state, new_state, reason_code, actor_profile_id, attempt_manifest_sha256, external_session_assignment_sha256, routing_event_sha256, recorded_at, previous_event_sha256, event_sha256. A BuildTask `READY -> DISPATCHED` event requires exactly one dispatch proof: a local AttemptManifest or an ExternalSessionAssignment. `routing_event_sha256` remains exclusive to predecessor/successor routing and is never reused for an external assignment.
- routing-event-v1: protocol, sequence, task_manifest_sha256, action, from_profile_id, to_profile_id, reason_code, successor_task_manifest_sha256, checkpoint_sha256, recorded_at, actor_profile_id, previous_event_sha256, event_sha256.
- preflight-receipt-v1: worker_profile_sha256, adapter_configuration_sha256, executable_sha256, host_id_sha256, availability, reason_code, returncode, timed_out, stdout_sha256, stderr_sha256, started_at, duration_ms.
- attempt-manifest-v1: attempt_id, task_manifest_sha256, base_commit_sha, adapter_configuration_sha256, run_identity_sha256, worker_profile_sha256, attempt_ordinal, preflight_receipt_sha256, worktree_key, resume_from_checkpoint_sha256, created_at.
- checkpoint-v1: checkpoint_id, attempt_manifest_sha256, boundary_id, base_commit_sha, commit_sha, patch_sha256, artifact_sha256s, command_receipt_sha256s, published_at.
- artifact-manifest-v1: artifact_id, attempt_manifest_sha256, kind, sha256, byte_size, media_type, store_key, created_at.
- command-receipt-v1: task_manifest_sha256, gate, command_id, correction_round, argv_sha256, cwd_key, patch_sha256, started_at, duration_ms, exit_code, outcome, stdout_sha256, stderr_sha256, stdout_truncated, stderr_truncated. `patch_sha256` is required for `AFFECTED_INTEGRATION` and `MILESTONE`; it may be null for `FOCUSED`.
- worker-handoff-v1: transport, attempt_manifest_sha256, external_session_assignment_sha256, task_manifest_sha256, output_kind, patch_sha256, commit_sha, files_changed, commands_executed, focused_test_results, assumptions, unresolved_issues, dependency_effects, security_effects, source_record_ids, source_packet_ids, raw_evidence_sha256, duration_ms, cost_microusd. LOCAL_ADAPTER requires attempt_manifest_sha256 and null external-session/raw-evidence fields; EXTERNAL_SESSION requires assignment and raw-evidence hashes and a null attempt hash.
- review-receipt-v1: transport, task_manifest_sha256, handoff_sha256, reviewer_profile_sha256, external_session_assignment_sha256, raw_evidence_sha256, verdict, findings, command_receipt_sha256s, source_verification, started_at, duration_ms.
- integration-receipt-v1: task_manifest_sha256, handoff_sha256, review_receipt_sha256, integrator_profile_id, canonical_parent_sha, integrated_commit_sha, changed_paths, command_receipt_sha256s, recorded_at.
- external-session-assignment-v1, protocol helios.build.external-session-assignment/v1: assignment_id, task_manifest_sha256, base_commit_sha, role, worker_profile_id, worker_profile_sha256, provider, session_id, transport=EXTERNAL_SESSION, routing_action, routing_reason, created_at, created_by_profile_id=codex-control-v1, target_host_eligible=false. routing_action is EXTERNAL_SESSION_ASSIGN for an initially selected external session and REROUTE_TO_EXTERNAL_SESSION for an unavailable or blocked local route; routing_reason is respectively EXTERNAL_SESSION_SELECTED, LOCAL_ADAPTER_UNAVAILABLE, or LOCAL_ADAPTER_BLOCKED_SUCCESSOR.
- external-session-handoff-v1, protocol helios.build.external-session-handoff/v1: assignment_sha256, task_manifest_sha256, base_commit_sha, worker_profile_sha256, provider, session_id, patch_sha256, raw_evidence_sha256, commands_executed, focused_test_results, assumptions, unresolved_issues, dependency_effects, security_effects, source_record_ids, source_packet_ids, duration_ms, cost_microusd, returned_at, target_host_eligible=false.
- external-session-review-v1, protocol helios.build.external-session-review/v1: assignment_sha256, task_manifest_sha256, handoff_sha256, reviewer_profile_sha256, provider, session_id, raw_evidence_sha256, verdict, findings, command_receipt_sha256s, source_verification, reviewed_at, duration_ms, target_host_eligible=false.

Task-manifest `required_source_record_ids`/`required_source_packet_ids`, handoff `source_record_ids`/`source_packet_ids`, and review `source_verification` remain opaque identifiers in Build Fabric contracts. The Build Fabric validates primitive type, uniqueness, exact task-to-handoff equality for both ID sets, and review coverage of both sets. Resolution, authority, source state, admission channel, and research receipt semantics belong exclusively to the ATHENA extension.

Exact enums:

    class HandoffTransport(StrEnum):
        LOCAL_ADAPTER = "LOCAL_ADAPTER"
        EXTERNAL_SESSION = "EXTERNAL_SESSION"

    class ExternalProvider(StrEnum):
        CODEX = "CODEX"
        CLAUDE = "CLAUDE"
        KIMI = "KIMI"
        GROK = "GROK"
        CURSOR = "CURSOR"
        COPILOT = "COPILOT"

    class NodeType(StrEnum):
        RESEARCH_REQUEST = "ResearchRequest"
        SOURCE_PACKET = "SourcePacket"
        INTERFACE_FREEZE = "InterfaceFreeze"
        BUILD_TASK = "BuildTask"
        ATTEMPT = "Attempt"
        CHECKPOINT = "Checkpoint"
        ARTIFACT = "Artifact"
        PATCH = "Patch"
        REVIEW = "Review"
        INTEGRATION = "Integration"
        CAPABILITY = "Capability"

    class EdgeType(StrEnum):
        DEPENDS_ON = "DEPENDS_ON"
        PRODUCES = "PRODUCES"
        CONSUMES = "CONSUMES"
        REVIEWS = "REVIEWS"
        TOUCHES_INTERFACE = "TOUCHES_INTERFACE"
        BLOCKS = "BLOCKS"

    class NodeState(StrEnum):
        DRAFT = "DRAFT"
        READY = "READY"
        DISPATCHED = "DISPATCHED"
        RETURNED = "RETURNED"
        REVIEWED = "REVIEWED"
        ACCEPTED = "ACCEPTED"
        INTEGRATED = "INTEGRATED"
        BLOCKED = "BLOCKED"
        FAILED = "FAILED"
        SUPERSEDED = "SUPERSEDED"

Happy path is DRAFT → READY → DISPATCHED → RETURNED → REVIEWED → ACCEPTED → INTEGRATED. Any state from DRAFT through ACCEPTED may become BLOCKED or FAILED. Those states plus BLOCKED/FAILED may become SUPERSEDED only after a valid successor is stored. INTEGRATED and SUPERSEDED are immutable terminal states.

## Dependencies and Commit Boundaries

| Task | Dependencies | Parallel allowance | Commit |
|---|---|---|---|
| 1 contracts | none | freezes package/schema boundary | feat(build): add strict build-control contracts |
| 2 CAS/ledger | 1 | none | feat(build): add immutable build-control storage |
| 3 graph/collisions | 2 | may overlap 4 | feat(build): add typed graph ownership gates |
| 4 profiles/preflight | 1 | may overlap 3 | feat(build): add exact worker preflight |
| 5 dispatch/checkpoints | 2, 3, 4 | none | feat(build): add isolated finite dispatch |
| 6 external import/collect/review | 5 | none | feat(build): add verified worker handoffs |
| 7 integration/status | 6 | none | feat(build): add controlled integration receipts |
| 8 research extension seam | 1, 2, 3 | coordination-only; no files | no Build Fabric commit |
| 9 CLI/package | 4 through 7; honors Task 8 seam | sole CLI/package owner until integrated | feat(build): complete repository build fabric |
| 10 acceptance/self-use | 9 plus the first B production receipt chain; C–F plans consume the frozen gate | only unrelated graph nodes continue when a required B–F route blocks | docs(build): record build-fabric acceptance |

---

### Task 1: Strict JSON, schemas, and repository/state separation

**Files:**
- Modify: pyproject.toml
- Create: tools/__init__.py
- Create: tools/helios_build/__init__.py
- Create: tools/helios_build/errors.py
- Create: tools/helios_build/types.py
- Create: tools/helios_build/canonical.py
- Create: tools/helios_build/schemas.py
- Create: tools/helios_build/paths.py
- Create: core schemas through routing-event-v1.schema.json listed above
- Create: tests/build_fabric_support.py
- Create: tests/test_build_contracts.py

**Interfaces:**
- BuildFabricError, ContractError, StateRootError, CollisionError, TransitionError.
- JsonValue, NodeType, EdgeType, NodeState, AttemptOutcome, HandoffTransport, and ExternalProvider.
- load_strict_json(path: Path) -> dict[str, Any].
- canonical_json_bytes(value: JsonValue) -> bytes.
- sha256_hex(data: bytes) -> str.
- SchemaRegistry(repo_root: Path).validate(payload: JsonValue, schema_name: str) -> None.
- BuildPaths.discover(start: Path, state_root: Path | None = None) -> BuildPaths.

- [ ] **Step 1: Write the failing contract tests**

    def test_strict_boundaries(self) -> None:
        duplicate = self.root / "duplicate.json"
        duplicate.write_text('{"protocol":"a","protocol":"b"}', encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "duplicate object key"):
            load_strict_json(duplicate)
        with self.assertRaisesRegex(ContractError, "floating-point"):
            canonical_json_bytes({"confidence": 0.9})
        invalid = valid_task_manifest() | {"unexpected": True}
        with self.assertRaises(ContractError):
            self.schemas.validate(invalid, "task-manifest-v1.schema.json")
        with self.assertRaisesRegex(StateRootError, "outside the repository"):
            BuildPaths.discover(self.repo_root, self.repo_root / "state")

- [ ] **Step 2: Run RED once**

Run: PYTHONPATH=src:. python3 -m unittest tests.test_build_contracts -v

Expected: FAIL because the build-control modules and schemas do not exist.

- [ ] **Step 3: Add only the development extra**

Add to pyproject.toml without changing project.scripts:

    [project.optional-dependencies]
    build-fabric = ["jsonschema>=4.23,<5"]

- [ ] **Step 4: Implement strict loading and canonical bytes**

    def canonical_json_bytes(value: JsonValue) -> bytes:
        reject_floats_and_invalid_values(value)
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")

load_strict_json uses object_pairs_hook to reject duplicate keys, parse_float to reject all floats, parse_constant to reject NaN/infinity, UTF-8 only, and an object root. SchemaRegistry uses Draft202012Validator, checks every schema at construction, resolves references only from build_control/schemas, and rejects network retrieval.

BuildPaths locates the nearest ancestor pyproject whose project.name is helios-takeoff-core. It takes state_root from the argument or HELIOS_BUILD_STATE_ROOT and rejects unsafe/resolved repository-contained roots.

- [ ] **Step 5: Implement every exact core field/enumeration in Exact Contracts**

Task ownership has files, path_globs, modules, migrations, schemas, public_interfaces, and forbidden_paths. Null predecessor/checkpoint fields remain present as JSON null. Paths reject absolute paths, backslashes, empty segments, dot-dot, NUL, and .git.

- [ ] **Step 6: Run GREEN once**

Run: PYTHONPATH=src:. python3 -m unittest tests.test_build_contracts -v

Expected: PASS within 300 seconds.

- [ ] **Step 7: Commit**

    git add pyproject.toml tools/__init__.py tools/helios_build/__init__.py tools/helios_build/errors.py tools/helios_build/types.py tools/helios_build/canonical.py tools/helios_build/schemas.py tools/helios_build/paths.py build_control/schemas/common-v1.schema.json build_control/schemas/task-manifest-v1.schema.json build_control/schemas/worker-profile-v1.schema.json build_control/schemas/adapter-config-v1.schema.json build_control/schemas/graph-manifest-v1.schema.json build_control/schemas/lifecycle-event-v1.schema.json build_control/schemas/routing-event-v1.schema.json tests/build_fabric_support.py tests/test_build_contracts.py
    git commit -m "feat(build): add strict build-control contracts"

### Task 2: Content-addressed storage and append-only ledgers

**Files:**
- Create: tools/helios_build/store.py
- Create: tools/helios_build/ledger.py
- Create: build_control/schemas/preflight-receipt-v1.schema.json
- Create: build_control/schemas/attempt-manifest-v1.schema.json
- Create: build_control/schemas/checkpoint-v1.schema.json
- Create: build_control/schemas/artifact-manifest-v1.schema.json
- Create: build_control/schemas/command-receipt-v1.schema.json
- Create: build_control/schemas/worker-handoff-v1.schema.json
- Create: build_control/schemas/review-receipt-v1.schema.json
- Create: build_control/schemas/integration-receipt-v1.schema.json
- Create: build_control/schemas/external-session-assignment-v1.schema.json
- Create: build_control/schemas/external-session-handoff-v1.schema.json
- Create: build_control/schemas/external-session-review-v1.schema.json
- Create: build_control/tasks/.gitkeep
- Create: build_control/graph/events.jsonl
- Create: build_control/graph/routing.jsonl
- Create: build_control/graph/receipts/.gitkeep
- Create: tests/test_build_store.py

**Interfaces:**
- StoredObject(sha256: str, path: Path, replayed: bool).
- ContentAddressedStore.put_json(collection: str, payload: JsonValue) -> StoredObject.
- ContentAddressedStore.put_bytes(collection: str, content: bytes, suffix: str) -> StoredObject.
- ContentAddressedStore.get_json(digest: str) -> dict[str, Any].
- append_event(path: Path, event: dict[str, Any], lock_root: Path) -> str.
- replay_events(path: Path) -> tuple[dict[str, Any], ...].

- [ ] **Step 1: Write failing replay/corruption tests**

    first = store.put_json("tasks", valid_task_manifest())
    second = store.put_json("tasks", valid_task_manifest())
    self.assertEqual(first.sha256, second.sha256)
    self.assertFalse(first.replayed)
    self.assertTrue(second.replayed)
    append_event(events, lifecycle_event("DRAFT", "READY"), lock_root)
    events.write_bytes(events.read_bytes()[:-4])
    with self.assertRaisesRegex(ContractError, "event ledger"):
        replay_events(events)

- [ ] **Step 2: Run RED once**

Run: PYTHONPATH=src:. python3 -m unittest tests.test_build_store -v

Expected: FAIL because store.py and ledger.py do not exist.

- [ ] **Step 3: Implement exclusive CAS publication**

Store objects at collection/sha256/first-two/digest.suffix. Write a same-directory temporary file, flush/fsync, publish with os.link so an existing path is never overwritten, chmod 0644, fsync the directory, then unlink the temporary. Existing identical bytes return replayed=True; differing bytes raise content-address collision.

- [ ] **Step 4: Implement the event chain and controller lock**

Use external_state_root/locks/build-control-ledger.lock with O_CREAT|O_EXCL and mode 0600. Never auto-remove an existing lock. While locked, verify the complete ledger; set the next integer sequence and previous digest; calculate event_sha256 over the canonical event without event_sha256; append one compact line; flush/fsync. Replay rejects a missing final newline, blank interior record, malformed JSON, sequence gap, prior-hash mismatch, or body-hash mismatch.

- [ ] **Step 5: Implement receipt schemas**

Preflight receipts exclude paths, argv, environment values, and output. Worker handoff requires one output kind PATCH or COMMIT and exactly the matching non-null digest. Command receipts use gate FOCUSED, AFFECTED_INTEGRATION, or MILESTONE and outcome PASS, FAIL, BLOCKED, or OUTCOME_UNKNOWN. The three external-session schemas require target_host_eligible=false and prohibit preflight_receipt_sha256, adapter_configuration_sha256, executable_sha256, and availability fields.

- [ ] **Step 6: Run GREEN once**

Run: PYTHONPATH=src:. python3 -m unittest tests.test_build_store -v

Expected: PASS within 300 seconds.

- [ ] **Step 7: Commit**

    git add tools/helios_build/store.py tools/helios_build/ledger.py build_control/schemas/preflight-receipt-v1.schema.json build_control/schemas/attempt-manifest-v1.schema.json build_control/schemas/checkpoint-v1.schema.json build_control/schemas/artifact-manifest-v1.schema.json build_control/schemas/command-receipt-v1.schema.json build_control/schemas/worker-handoff-v1.schema.json build_control/schemas/review-receipt-v1.schema.json build_control/schemas/integration-receipt-v1.schema.json build_control/schemas/external-session-assignment-v1.schema.json build_control/schemas/external-session-handoff-v1.schema.json build_control/schemas/external-session-review-v1.schema.json build_control/tasks/.gitkeep build_control/graph/events.jsonl build_control/graph/routing.jsonl build_control/graph/receipts/.gitkeep tests/test_build_store.py
    git commit -m "feat(build): add immutable build-control storage"

### Task 3: DAG, transitions, and ownership collisions

**Files:**
- Create: tools/helios_build/ownership.py
- Create: tools/helios_build/graph.py
- Create: tests/test_build_graph.py

**Interfaces:**
- OwnershipClaim.from_manifest(payload) -> OwnershipClaim.
- find_collisions(candidate, active) -> tuple[Collision, ...].
- assert_owned_changes(task, changed_paths) -> None.
- BuildGraph.load(manifest, events) -> BuildGraph.
- validate_acyclic(graph) -> None.
- ready_nodes(graph) -> tuple[str, ...].
- descendants(graph, node_id) -> frozenset[str].
- transition_node(paths, node_id, expected_state, new_state, reason_code, actor_profile_id) -> str.

- [ ] **Step 1: Write failing cycle/collision/state tests**

    collisions = find_collisions(candidate_claim(), [active_claim()])
    self.assertEqual(
        {item.kind for item in collisions},
        {"FILE", "PATH_GLOB", "MODULE", "MIGRATION", "SCHEMA", "PUBLIC_INTERFACE"},
    )
    with self.assertRaisesRegex(ContractError, "cycle"):
        validate_acyclic(cyclic_graph())
    graph = blocked_dependency_graph()
    self.assertEqual(graph.state("dependent-build"), NodeState.BLOCKED)
    self.assertEqual(graph.state("independent-build"), NodeState.READY)

- [ ] **Step 2: Run RED once**

Run: PYTHONPATH=src:. python3 -m unittest tests.test_build_graph -v

- [ ] **Step 3: Implement conservative ownership**

File/file is exact; file/glob uses PurePath.match; glob/glob collides when literal prefixes through the first wildcard have a directory-prefix relationship, and a glob without literal prefix collides with every glob. Module claims collide by equal-or-dot-prefix. Migration stream, schema ID, and public-interface ID collide exactly. Compare a dispatch candidate with every writer from DISPATCHED through ACCEPTED. False-positive serialization is permitted; a missed overlap is not.

- [ ] **Step 4: Implement graph legality**

Validate all node hashes and edge endpoints. Detect cycles over DEPENDS_ON with color-marked DFS and report the concrete cycle. A node is ready only when dependencies are integrated and required source/checkpoint artifacts resolve. Enforce the exact states above, distinct builder/reviewer, Codex integrator, and three dispatched implementation lanes. BLOCKED propagates only through descendants.

- [ ] **Step 5: Run GREEN once**

Run: PYTHONPATH=src:. python3 -m unittest tests.test_build_graph -v

Expected: PASS within 300 seconds.

- [ ] **Step 6: Commit**

    git add tools/helios_build/ownership.py tools/helios_build/graph.py tests/test_build_graph.py
    git commit -m "feat(build): add typed graph ownership gates"

### Task 4: Policy profiles and exact finite preflight

**Files:**
- Create: tools/helios_build/profiles.py
- Create: tools/helios_build/process.py
- Create: tools/helios_build/doctor.py
- Create: build_control/worker_profiles/codex-control-v1.json
- Create: build_control/worker_profiles/codex-builder-v1.json
- Create: build_control/worker_profiles/codex-reviewer-v1.json
- Create: build_control/worker_profiles/claude-builder-v1.json
- Create: build_control/worker_profiles/kimi-builder-v1.json
- Create: build_control/worker_profiles/grok-builder-v1.json
- Create: build_control/worker_profiles/cursor-builder-v1.json
- Create: build_control/worker_profiles/copilot-builder-v1.json
- Create: tests/test_build_preflight.py

**Interfaces:**
- load_worker_profile(path, schemas) -> WorkerProfile.
- load_adapter_configuration(path, schemas) -> tuple[AdapterConfiguration, ...].
- adapter_configuration_sha256(adapter) -> str.
- run_bounded_process(argv, input_bytes, cwd, env, timeout_seconds, max_capture_bytes) -> ProcessResult.
- preflight_worker(paths, profile, adapter) -> PreflightResult.
- build_doctor_report(paths, adapter_config_path) -> dict[str, Any].

- [ ] **Step 1: Write failing availability/secret tests**

    result = preflight_worker(paths, claude_profile, None)
    self.assertEqual((result.availability, result.reason_code), ("UNAVAILABLE", "ADAPTER_NOT_CONFIGURED"))
    available = preflight_worker(paths, fixture_profile, fixture_adapter)
    receipt = available.receipt_path.read_bytes()
    self.assertNotIn(secret.encode(), receipt)
    self.assertNotIn(str(fixture_adapter.executable).encode(), receipt)

- [ ] **Step 2: Run RED once**

Run: PYTHONPATH=src:. python3 -m unittest tests.test_build_preflight -v

- [ ] **Step 3: Add command-free policy profiles**

codex-control-v1 is CONTROLLER/INTEGRATOR and has no builder/reviewer role or dispatch adapter. codex-builder-v1 identifies a bounded Codex author session; codex-reviewer-v1 identifies a separate Codex review session. Both permit EXTERNAL_SESSION and have no integration authority. Claude is ARCHITECT/BUILDER/REVIEWER. Kimi, Grok, Cursor, and Copilot have bounded builder/reviewer roles matching the spec. All builder/reviewer profiles permit EXTERNAL_SESSION; LOCAL_ADAPTER is declared only where a real adapter protocol is supported. Profiles contain no host path or command. Do not create an ATHENA profile in this plan. Do not invent skill/plugin revisions; an empty list grants none.

- [ ] **Step 4: Implement adapter normalization and preflight**

Require an absolute existing regular executable, executable bit, and exact file digest. Permit only task_manifest, handoff_path, checkpoint_dir, and worktree tokens in dispatch argv and none in preflight argv. Hash normalized non-secret fields and sorted environment-variable names, never values.

run_bounded_process uses shell=False, bounded concurrent stream drains, and a new process group/session. Timeout sends TERM, waits 500 ms, then KILL. Raw streams go outside Git. Truncation makes preflight unavailable.

Exact outcomes: PREFLIGHT_READY, ADAPTER_NOT_CONFIGURED, EXECUTABLE_MISSING, EXECUTABLE_HASH_MISMATCH, ENVIRONMENT_MISSING, PREFLIGHT_TIMEOUT, PREFLIGHT_EXIT_NONZERO, PREFLIGHT_OUTPUT_TRUNCATED.

- [ ] **Step 5: Run GREEN once**

Run: PYTHONPATH=src:. python3 -m unittest tests.test_build_preflight -v

Expected: PASS within 300 seconds.

- [ ] **Step 6: Commit**

    git add tools/helios_build/profiles.py tools/helios_build/process.py tools/helios_build/doctor.py build_control/worker_profiles/codex-control-v1.json build_control/worker_profiles/codex-builder-v1.json build_control/worker_profiles/codex-reviewer-v1.json build_control/worker_profiles/claude-builder-v1.json build_control/worker_profiles/kimi-builder-v1.json build_control/worker_profiles/grok-builder-v1.json build_control/worker_profiles/cursor-builder-v1.json build_control/worker_profiles/copilot-builder-v1.json tests/test_build_preflight.py
    git commit -m "feat(build): add exact worker preflight"

### Task 5: Idempotent worktree dispatch and checkpoints

**Files:**
- Create: tools/helios_build/worktrees.py
- Create: tools/helios_build/checkpoints.py
- Create: tools/helios_build/dispatch.py
- Create: tests/test_build_dispatch.py

**Interfaces:**
- compute_run_identity(task_manifest_sha256, base_commit_sha, adapter_configuration_sha256) -> RunIdentity.
- create_detached_worktree(paths, run_identity_sha256, base_commit_sha) -> Path.
- dispatch_task(paths, task_ref, adapter_config_path) -> DispatchResult.
- collect_checkpoints(paths, attempt, task) -> tuple[CheckpointRecord, ...].

- [ ] **Step 1: Write failing replay/timeout/checkpoint tests**

    first = dispatch_task(paths, task_sha, adapter_config)
    second = dispatch_task(paths, task_sha, adapter_config)
    self.assertFalse(first.replayed)
    self.assertTrue(second.replayed)
    self.assertEqual(first.run_identity_sha256, second.run_identity_sha256)
    self.assertEqual(launch_counter.read_text(encoding="utf-8"), "1")
    timed_out = dispatch_task(paths, timeout_task_sha, adapter_config)
    self.assertEqual(timed_out.outcome, AttemptOutcome.OUTCOME_UNKNOWN)
    with self.assertRaisesRegex(ContractError, "undeclared checkpoint boundary"):
        collect_checkpoints(paths, timed_out.attempt, task)

- [ ] **Step 2: Run RED once**

Run: PYTHONPATH=src:. python3 -m unittest tests.test_build_dispatch -v

- [ ] **Step 3: Implement the run identity and atomic pre-dispatch gate**

    def compute_run_identity(task_sha: str, base_sha: str, config_sha: str) -> RunIdentity:
        body = {
            "task_manifest_sha256": task_sha,
            "base_commit_sha": base_sha,
            "adapter_configuration_sha256": config_sha,
        }
        return RunIdentity(task_sha, base_sha, config_sha, sha256_hex(canonical_json_bytes(body)))

Under one controller lock validate task/profile, Git commit, DAG/dependencies, three-lane cap, ownership, source/checkpoint requirements, exact preflight, then lookup run identity. Existing identity returns immediately.

- [ ] **Step 4: Implement one detached worktree and one process**

Create external_state_root/worktrees/run-sha only through git worktree add --detach path base-sha, argv mode, shell false. Store attempt and append READY→DISPATCHED before execution. Expand only declared tokens, pass canonical task bytes on stdin, disable Git credential prompting, and expose only declared environment names. Exit zero plus a handoff file remains DISPATCHED until collection validates it.

- [ ] **Step 5: Implement terminal and successor behavior**

Missing/unavailable adapter blocks without process. Timeout kills the process group and records OUTCOME_UNKNOWN. Deterministic process failure records FAILED. Collect checkpoints only when attempt/base/boundary/artifact/command hashes and ownership match. Retry, reroute, correction, or resume stores a successor, appends an explicit routing event, then supersedes the predecessor. Never append another attempt for an existing run identity.

- [ ] **Step 6: Run GREEN once**

Run: PYTHONPATH=src:. python3 -m unittest tests.test_build_dispatch -v

Expected: PASS within 300 seconds.

- [ ] **Step 7: Commit**

    git add tools/helios_build/worktrees.py tools/helios_build/checkpoints.py tools/helios_build/dispatch.py tests/test_build_dispatch.py
    git commit -m "feat(build): add isolated finite dispatch"

### Task 6: External-session import, Git-derived collection, and independent review

**Files:**
- Create: tools/helios_build/verification.py
- Create: tools/helios_build/collect.py
- Create: tools/helios_build/review.py
- Create: tools/helios_build/external_sessions.py
- Create: tests/test_build_review_integration.py
- Create: tests/test_build_external_sessions.py
- Modify: build_control/schemas/lifecycle-event-v1.schema.json
- Modify: build_control/schemas/command-receipt-v1.schema.json
- Modify: tools/helios_build/graph.py
- Modify: tools/helios_build/doctor.py, only to add a backward-compatible keyword-only expected result protocol for exact reviewer preflight.
- Modify as required by the new nullable lifecycle field: existing lifecycle-event producers and focused fixtures only.

**Interfaces:**
- run_verification_gate(paths, task, worktree, gate) -> tuple[CommandReceipt, ...].
- collect_task(paths, task_ref) -> HandoffReceipt.
- review_task(paths, task_ref, adapter_config_path) -> ReviewReceipt.
- begin_external_session(paths, task_ref, worker_profile_id, provider, session_id, role, routing_reason) -> ExternalSessionAssignment.
- import_external_handoff(paths, task_ref, assignment_ref, handoff_path, patch_path, raw_evidence_path) -> HandoffReceipt.
- import_external_review(paths, task_ref, assignment_ref, review_path, raw_evidence_path) -> ReviewReceipt.
- create_correction_successor(paths, task_ref, review_receipt_sha256) -> StoredObject.

- [ ] **Step 1: Write failing scope/self-review/run-limit tests**

    with self.assertRaisesRegex(CollisionError, "forbidden.py"):
        collect_task(paths, underreported_task_sha)
    with self.assertRaisesRegex(ContractError, "reviewer must differ"):
        review_task(paths, returned_task_sha, builder_adapter_config)
    first = run_verification_gate(paths, task, worktree, "AFFECTED_INTEGRATION")
    self.assertEqual(first[0].outcome, "FAIL")
    with self.assertRaisesRegex(ContractError, "unchanged failing command"):
        run_verification_gate(paths, task, worktree, "AFFECTED_INTEGRATION")
    with self.assertRaisesRegex(ContractError, "source_record_ids"):
        import_external_handoff(
            paths,
            direct_primary_task_sha,
            assignment_sha,
            handoff_missing_required_source_record,
            patch_path,
            raw_evidence_path,
        )

    assignment = begin_external_session(
        paths,
        task_sha,
        "codex-builder-v1",
        "CODEX",
        "session-20260901-a",
        "BUILDER",
        "EXTERNAL_SESSION_SELECTED",
    )
    imported = import_external_handoff(
        paths, task_sha, assignment.sha256, handoff_path, patch_path, raw_evidence_path
    )
    self.assertEqual(imported.transport, HandoffTransport.EXTERNAL_SESSION)
    self.assertIsNone(imported.attempt_manifest_sha256)
    self.assertFalse(imported.target_host_eligible)
    self.assertFalse(raw_evidence_bytes_present_under(paths.repo_root))

- [ ] **Step 2: Run RED once**

Run: PYTHONPATH=src:. python3 -m unittest tests.test_build_review_integration tests.test_build_external_sessions -v

- [ ] **Step 3: Implement collection from Git truth**

For LOCAL_ADAPTER, strictly validate the external handoff. Derive paths with git diff --name-status -z base and patch with git diff --binary base. For a commit result, require descent from exact base and derive from the commit. Reject reported/derived mismatch, forbidden/unowned paths, .git changes, undeclared migration/schema/interface changes, and unrequired source packets. Content-address the patch and sanitized receipts under build_control/graph/receipts, then append DISPATCHED→RETURNED. Keep raw output external.

- [ ] **Step 4: Implement honest EXTERNAL_SESSION begin and handoff import**

begin_external_session accepts only CODEX, CLAUDE, KIMI, GROK, CURSOR, or COPILOT and an exact committed worker profile permitting EXTERNAL_SESSION. BUILDER requires task state READY, all normal dependency/collision/lane checks, and a profile with BUILDER. Store an immutable assignment containing task/base/profile/provider/session hashes and the routing reason before work begins, count it as an active implementation lane, and transition READY→DISPATCHED with the exact assignment digest in `external_session_assignment_sha256` and a null AttemptManifest digest. EXTERNAL_SESSION_SELECTED records EXTERNAL_SESSION_ASSIGN inside the immutable assignment. LOCAL_ADAPTER_UNAVAILABLE records REROUTE_TO_EXTERNAL_SESSION without asserting that any preflight ran; LOCAL_ADAPTER_BLOCKED_SUCCESSOR is valid only for the immutable successor of a terminally blocked local task. External assignments never enter `routing.jsonl`; that ledger remains exclusive to predecessor/successor routing. Reject every other action/reason pairing. Do not load adapter configuration, run preflight, create an AttemptManifest, or change availability.

import_external_handoff requires the exact assignment, task, base, worker profile, provider, and session ID. Verify patch_sha256 against the supplied patch bytes and raw_evidence_sha256 against a file already under the external state root. Copy raw evidence into external_state_root/external_sessions/evidence/sha256/first-two/digest with mode 0600; Git receives only its digest. Apply the patch with git apply --check and git apply in a fresh detached worktree at the frozen base, then derive canonical patch bytes and changed paths from Git. Reject a hash mismatch, pre-assignment handoff timestamp, second differing patch for one assignment, out-of-scope path, undeclared interface/schema impact, or worker/profile mismatch. Identical import is idempotent. Store the canonical patch, standard WorkerHandoff, and graph receipts; append DISPATCHED→RETURNED with reason EXTERNAL_SESSION_HANDOFF_IMPORTED.

- [ ] **Step 5: Implement bounded gates**

Key every command receipt by task SHA, gate, command ID, correction round, and the canonical patch digest for affected/milestone gates. Run exact argv with shell false and the smaller declared/default timeout. Collection records one final focused affected set. Review records one affected-integration gate. A second affected/milestone run requires a higher correction round and changed patch digest. Persist failure before refusing an unchanged rerun.

- [ ] **Step 6: Implement distinct local or external-session review**

LOCAL_ADAPTER review requires reviewer hash different from builder and exact reviewer preflight. Create a fresh review worktree, git apply --check and apply the collected patch, then invoke one finite reviewer.

Exact reviewer preflight uses the same pinned executable/configuration verification as builder preflight but binds the fixed `helios.build.review-receipt/v1` result protocol through a keyword-only override. Default doctor/dispatch calls remain bound to the committed profile's builder adapter protocol.

EXTERNAL_SESSION review begins only after RETURNED, uses role REVIEWER, and requires reviewer profile and session ID different from the builder's. The provider may match only when the reviewer is a genuinely separate identified session with fresh context, such as codex-reviewer-v1 reviewing codex-builder-v1. It creates no preflight or availability fact. import_external_review verifies assignment/task/handoff/raw-evidence hashes, retains raw evidence only in the external CAS, applies the exact patch in a clean base worktree, and stores a standard ReviewReceipt with target_host_eligible=false.

Both review transports use verdict ACCEPTED, CHANGES_REQUIRED, or REJECTED and findings CRITICAL/MAJOR/MINOR. Append RETURNED→REVIEWED after validation, then REVIEWED→ACCEPTED only for ACCEPTED, no critical finding, and a passing affected-integration receipt. CHANGES_REQUIRED creates a successor; a third correction escalates.

- [ ] **Step 7: Prove external imports cannot satisfy local-provider acceptance**

Tests must assert an external assignment has null preflight/adapter hashes, leaves doctor availability unchanged, cannot be selected by target-host receipt aggregation, rejects builder-as-reviewer, and cannot be relabeled LOCAL_ADAPTER. They must also prove the committed receipt/patch tree contains no raw evidence bytes or raw-evidence path.

- [ ] **Step 8: Run GREEN once**

Run: PYTHONPATH=src:. python3 -m unittest tests.test_build_review_integration tests.test_build_external_sessions -v

Expected: PASS within 300 seconds.

- [ ] **Step 9: Commit**

    git add tools/helios_build/verification.py tools/helios_build/collect.py tools/helios_build/review.py tools/helios_build/external_sessions.py tests/test_build_review_integration.py tests/test_build_external_sessions.py
    git commit -m "feat(build): add verified worker handoffs"

### Task 7: Controlled integration and truthful status

**Files:**
- Create: tools/helios_build/integrate.py
- Create: tools/helios_build/status.py
- Modify: tests/test_build_review_integration.py

**Interfaces:**
- record_integration(paths, task_ref, commit_sha: str | None = None) -> IntegrationReceipt.
- graph_status(paths) -> dict[str, Any].
- build_report(paths) -> dict[str, Any].

`BUILD_FABRIC_CORE_CODE_COMPLETE` requires one exact non-fixture, substantive, accepted integration chain with capability_id `BUILD_FABRIC_CORE_CODE`; Task 9 is frozen and executed as that first real self-use task before its implementation begins. Broad task-name matching is forbidden.

`build_report` may accept keyword-only research and target-host evidence validators. Without a registered validator, each status remains false. A validator returns only after validating its separately owned immutable receipts; the Build Fabric never reads research-owned paths or infers target-Mac truth from an arbitrary host hash.

- [ ] **Step 1: Add failing authority/status tests**

    with self.assertRaisesRegex(ContractError, "accepted independent review"):
        record_integration(paths, returned_task_sha)
    receipt = record_integration(paths, accepted_task_sha, integrated_commit_sha)
    self.assertEqual(receipt.integrator_profile_id, "codex-control-v1")
    report = build_report(paths)
    self.assertTrue(report["BUILD_FABRIC_CORE_CODE_COMPLETE"])
    self.assertFalse(report["TARGET_HOST_ACCEPTED"])
    self.assertEqual(
        report["PRODUCTION_SELF_USE"],
        {
            "DELIVERABLE_B_DIV23_V2": False,
            "DELIVERABLE_C_MODEL_REGISTRY": False,
            "DELIVERABLE_D_DATASET_INTAKE": False,
            "DELIVERABLE_E_LOCAL_SERVICE_SDK": False,
            "DELIVERABLE_F_CLI_ADAPTER": False,
        },
    )

- [ ] **Step 2: Run RED once**

Run: PYTHONPATH=src:. python3 -m unittest tests.test_build_review_integration -v

- [ ] **Step 3: Implement verification/finalization, not autonomous merge**

Require ACCEPTED state, independent accepted review, Codex integrator, existing canonical commit, exact parent, and commit patch/path equality with the handoff. The function does not stage, commit, push, or merge. It records remaining declared gates, writes the integration receipt, and appends ACCEPTED→INTEGRATED. Gate failure leaves the task accepted with a failure receipt. An EXTERNAL_SESSION handoff remains labeled EXTERNAL_SESSION through integration; integration never creates a preflight claim or changes target_host_eligible=false.

- [ ] **Step 4: Implement reconstructable status**

graph_status replays only manifests and ledgers. Return node counts, ready nodes, lane count, blocked nodes/descendants, collisions, and integrity errors. build_report separates BUILD_FABRIC_CORE_CODE_COMPLETE, PRODUCTION_SELF_USE, CORE_CODE_COMPLETE, RESEARCH_ROUNDTRIP_ACCEPTED, TARGET_HOST_ACCEPTED, and CYCLE_COMPLETE with supporting receipt hashes. Build-fabric core completion never implies the other statuses.

PRODUCTION_SELF_USE has exactly five keys: DELIVERABLE_B_DIV23_V2, DELIVERABLE_C_MODEL_REGISTRY, DELIVERABLE_D_DATASET_INTAKE, DELIVERABLE_E_LOCAL_SERVICE_SDK, and DELIVERABLE_F_CLI_ADAPTER. A key becomes true only when a substantive production task for that capability has a content-addressed task manifest, graph node/edges, non-fixture handoff with Git-derived patch, distinct accepted review, Codex integration receipt, and installed-code acceptance receipt. Documentation-only, test-only, demo-only, fixture, or canned artifacts do not count. EXTERNAL_SESSION is valid for these five keys.

CORE_CODE_COMPLETE remains false until all five self-use keys and the spec's installed-code gates are true. TARGET_HOST_ACCEPTED ignores every EXTERNAL_SESSION record and requires LOCAL_ADAPTER, exact successful preflight, owner-Mac host identity, and real bounded task receipts for Claude plus one secondary builder. Research receipt discovery is an extension callback registered later by the ATHENA plan; this task neither reads nor creates research paths.

Status reconstruction revalidates every affected-integration command receipt required by the accepted review against task SHA, correction round, exact patch, argv, cwd, and PASS outcome. Substantive production paths exclude documentation-only extensions and documentation directories.

- [ ] **Step 5: Run GREEN once**

Run: PYTHONPATH=src:. python3 -m unittest tests.test_build_review_integration -v

Expected: PASS within 300 seconds.

- [ ] **Step 6: Commit**

    git add tools/helios_build/integrate.py tools/helios_build/status.py tests/test_build_review_integration.py
    git commit -m "feat(build): add controlled integration receipts"

### Task 8: Freeze the ATHENA extension seam without taking ownership

**Files:**
- No files are created or modified by this coordination gate.

**Interfaces consumed by the dedicated ATHENA plan:**
- load_strict_json(path: Path) -> dict[str, Any].
- canonical_json_bytes(value: JsonValue) -> bytes.
- sha256_hex(data: bytes) -> str.
- SchemaRegistry(repo_root: Path).validate(payload: JsonValue, schema_name: str) -> None.
- BuildPaths.discover(start: Path, state_root: Path | None = None) -> BuildPaths.
- ContentAddressedStore.put_json(collection: str, payload: JsonValue) -> StoredObject.
- ContentAddressedStore.put_bytes(collection: str, content: bytes, suffix: str) -> StoredObject.
- append_event(path: Path, event: dict[str, Any], lock_root: Path) -> str.
- replay_events(path: Path) -> tuple[dict[str, Any], ...].
- Task-manifest `required_source_record_ids`/`required_source_packet_ids`, handoff `source_record_ids`/`source_packet_ids`, and review `source_verification` as opaque pass-through fields with exact task-to-handoff equality.

**Exclusive ATHENA ownership:**
- build_control/schemas/research-request-v1.schema.json.
- build_control/schemas/source-record-v1.schema.json and every other source-registry schema.
- build_control/schemas/source-packet-v1.schema.json.
- build_control/schemas/research-outcome-v1.schema.json and every research receipt schema.
- build_control/worker_profiles/athena-research-v1.json and all ATHENA instructions.
- build_control/source_registry, build_control/source_packets, and build_control/outcomes.
- tools/helios_build/research.py, tools/helios_build/source_registry.py, and tools/helios_build/research_receipts.py.
- Every dedicated research test module and fixture.

- [ ] **Step 1: Verify the shared seam is frozen before research implementation begins**

The ATHENA task records the exact commits containing Tasks 1, 2, and 3 and consumes only the interfaces listed above. It must not change their signatures while a Build Fabric task is active. Any required change returns to a serialized interface-freeze task with one owner.

- [ ] **Step 2: Make research-owned paths forbidden in every Build Fabric task manifest**

Build Fabric manifests include the exclusive paths above in forbidden_paths. This includes build_control/outcomes even though the accepted repository-level design requires that directory eventually; the dedicated ATHENA plan creates and owns it.

- [ ] **Step 3: Serialize the later CLI integration**

Task 9 owns tools/helios_build/cli.py and tests/test_build_cli.py until its commit is integrated. Only afterward may the dedicated ATHENA plan take sole writer ownership of those two shared files to register research export/import. That later task consumes register_command from Task 9, adds its dedicated research routes/tests, records the new interface digest, and releases ownership before another CLI task starts.

- [ ] **Step 4: Record no Build Fabric test or commit for this gate**

This gate is satisfied by the non-overlapping task manifests and interface hashes. Its implementation, RED/GREEN cycle, receipts, and commit belong to the dedicated ATHENA plan.

### Task 9: CLI, documentation, package exclusion, and authority proof

Before Step 1, freeze Task 9 as the first real Build Fabric self-use task with capability_id `BUILD_FABRIC_CORE_CODE`, its exact owned files/interfaces/tests/base/budgets, one identified builder, a distinct reviewer, and Codex integrator. Work started before this assignment does not count toward `BUILD_FABRIC_CORE_CODE_COMPLETE`.

**Files:**
- Create: tools/helios_build/__main__.py
- Create: tools/helios_build/cli.py
- Create: build_control/README.md
- Create: tests/test_build_cli.py
- Create: tests/test_build_package_boundary.py
- Create: tests/test_build_authority_boundary.py

**Interfaces:**
- main(argv: Sequence[str] | None = None) -> int.
- register_command(subparsers, handlers, name: str, configure: CommandConfigurer, handler: CommandHandler) -> None.
- CommandHandler = Callable[[BuildPaths, argparse.Namespace], dict[str, Any]].
- CommandConfigurer = Callable[[argparse.ArgumentParser], None].
- Compact sorted JSON stdout; compact structured JSON stderr.
- Exit 0 success, 2 usage/contract, 3 blocked/unavailable, 4 failed, 5 outcome unknown.

- [ ] **Step 1: Write failing CLI/package/authority tests**

    commands = [
        ["doctor"],
        ["graph", "status"],
        ["task", "validate", str(task_path)],
        ["dispatch", task_sha, "--adapter-config", str(adapter_config)],
        ["collect", task_sha],
        ["review", task_sha, "--adapter-config", str(adapter_config)],
        ["integrate", task_sha, "--commit-sha", "a" * 40],
        ["external-session", "begin", task_sha, "--worker-profile", "codex-builder-v1", "--provider", "CODEX", "--session-id", "session-a", "--role", "BUILDER", "--routing-reason", "EXTERNAL_SESSION_SELECTED"],
        ["external-session", "import-handoff", task_sha, "--assignment", "b" * 64, "--handoff", str(handoff_path), "--patch", str(patch_path), "--raw-evidence", str(raw_evidence_path)],
        ["external-session", "import-review", task_sha, "--assignment", "c" * 64, "--review", str(review_path), "--raw-evidence", str(raw_evidence_path)],
        ["report"],
    ]
    for argv in commands:
        with self.subTest(argv=argv):
            self.assertNotEqual(main(argv), 2)

Authority test parses imports with ast and forbids roots helios_takeoff_core, sqlite3, sqlalchemy. It scans build-tool source for P0/P1A table names and scans src/helios_takeoff_core for imports of tools.helios_build.

Package test builds a temporary wheel with pip wheel --no-deps --no-build-isolation, inspects it with zipfile, and asserts no tools/, build_control/, provider/browser module, or helios-build entry. Existing helios-p0, helios-p1a, helios-engine entries remain exact.

- [ ] **Step 2: Run RED once**

Run: PYTHONPATH=src:. python3 -m unittest tests.test_build_cli tests.test_build_package_boundary tests.test_build_authority_boundary -v

- [ ] **Step 3: Implement exact CLI grammar**

    python -m tools.helios_build doctor [--adapter-config PATH]
    python -m tools.helios_build graph status
    python -m tools.helios_build task validate TASK
    python -m tools.helios_build dispatch TASK --adapter-config PATH
    python -m tools.helios_build collect TASK
    python -m tools.helios_build review TASK --adapter-config PATH
    python -m tools.helios_build integrate TASK [--commit-sha SHA]
    python -m tools.helios_build external-session begin TASK --worker-profile PROFILE --provider PROVIDER --session-id SESSION --role BUILDER|REVIEWER --routing-reason EXTERNAL_SESSION_SELECTED|LOCAL_ADAPTER_UNAVAILABLE|LOCAL_ADAPTER_BLOCKED_SUCCESSOR
    python -m tools.helios_build external-session import-handoff TASK --assignment SHA --handoff PATH --patch PATH --raw-evidence PATH
    python -m tools.helios_build external-session import-review TASK --assignment SHA --review PATH --raw-evidence PATH
    python -m tools.helios_build report

Global options are --repo-root and --state-root; stateful commands also use HELIOS_BUILD_STATE_ROOT. TASK is a digest or JSON path under build_control/tasks; validation content-addresses a path before dispatch. PROVIDER is one of CODEX, CLAUDE, KIMI, GROK, CURSOR, COPILOT. __main__.py imports main and raises SystemExit(main()). All core commands register through register_command; duplicate command names fail at parser construction. This plan does not register research commands. The dedicated ATHENA plan later adds research export/import in one serialized cli.py task after this commit.

- [ ] **Step 4: Document finite operation and recovery**

README documents Build Fabric-owned directories, external state, adapter config, immutable successors, event-chain recovery, stale-lock positive-death check, collisions, checkpoints, budgets, sanitized/raw separation, both LOCAL_ADAPTER and EXTERNAL_SESSION commands, P0/P1A prohibition, target-host ineligibility of imported sessions, and the frozen command-registration seam. It names the research-owned paths from Task 8 and directs their implementation and CLI registration to the dedicated ATHENA plan.

- [ ] **Step 5: Run GREEN once**

Run: PYTHONPATH=src:. python3 -m unittest tests.test_build_cli tests.test_build_package_boundary tests.test_build_authority_boundary -v

Expected: PASS within 600 seconds.

- [ ] **Step 6: Run the affected gate once**

Run exactly once:

    PYTHONPATH=src:. python3 -m unittest tests.test_build_contracts tests.test_build_store tests.test_build_graph tests.test_build_preflight tests.test_build_dispatch tests.test_build_review_integration tests.test_build_external_sessions tests.test_build_cli tests.test_build_package_boundary tests.test_build_authority_boundary -v

Expected: PASS within 600 seconds. After a substantive correction, one verification rerun is allowed. An unchanged failure is not rerun.

- [ ] **Step 7: Commit**

    git add tools/helios_build/__main__.py tools/helios_build/cli.py build_control/README.md tests/test_build_cli.py tests/test_build_package_boundary.py tests/test_build_authority_boundary.py
    git commit -m "feat(build): complete repository build fabric"

### Task 10: Clean acceptance and mandatory production self-use

**Files:**
- Create: docs/verification/build-fabric-mvp-acceptance.md
- Create through the CLI: immutable task, graph, handoff, review, integration, and installed-code receipts for the first Deliverable B production task

**Interfaces:**
- Produces truthful BUILD_FABRIC_CORE_CODE_COMPLETE evidence.
- Proves the first substantive Deliverable B production task used either LOCAL_ADAPTER or EXTERNAL_SESSION before Codex integration.
- Defines the same mandatory receipt-chain gate consumed by the C, D, E, and F subsystem plans.
- Produces TARGET_HOST_ACCEPTED only from real target-Mac Claude and secondary-builder attempts.
- Leaves unavailable external acceptance blocked while unrelated engine nodes continue.
- Does not produce RESEARCH_ROUNDTRIP_ACCEPTED; that receipt belongs to the dedicated ATHENA plan.

- [ ] **Step 1: Freeze the first substantive Deliverable B task before production work**

The Deliverable B plan authors a non-documentation, non-test-only interface/schema or kernel task with capability_id DELIVERABLE_B_DIV23_V2. Before its builder edits code, validate/content-address the task, add its BuildTask, Review, and Integration nodes/edges, and record the exact base commit, ownership, interfaces, commands, budgets, builder profile, distinct reviewer profile, and Codex integrator. Save the emitted task and graph hashes. Work begun before this freeze cannot satisfy production self-use by retroactive receipt import.

- [ ] **Step 2: Route first B through LOCAL_ADAPTER or EXTERNAL_SESSION with no bypass**

Freeze exactly one initial transport before the builder edits code. For LOCAL_ADAPTER, run doctor first; only an exact AVAILABLE preflight permits one dispatch and collect. For a deliberately selected identified current Codex/Claude/Kimi/Grok/Cursor/Copilot session, invoke external-session begin with its real profile/session ID and `--routing-reason EXTERNAL_SESSION_SELECTED`, without running or claiming local preflight. If LOCAL_ADAPTER was selected but is unavailable, either invoke external-session begin on the still-ready task with `--routing-reason LOCAL_ADAPTER_UNAVAILABLE` and import that identified session's handoff, exact patch, and raw evidence from the external state root, or mark B BLOCKED. The reroute stores that reason and appends REROUTE_TO_EXTERNAL_SESSION without claiming a successful or failed CLI preflight.

If a local dispatch already terminally blocked, first create a successor task manifest and explicit reroute event, then begin the external session against the successor with `--routing-reason LOCAL_ADAPTER_BLOCKED_SUCCESSOR`. If neither a real local adapter nor a real identified external session is available, B becomes BLOCKED and none of its descendants or production commit may be integrated. There is no outside-fabric fallback; only unrelated graph nodes continue.

- [ ] **Step 3: Independently review and integrate first B through the graph**

Use either exact-preflight LOCAL_ADAPTER review or a separately identified EXTERNAL_SESSION reviewer whose profile and session ID differ from the builder. Import/store the review evidence, run the affected integration gate, and require an accepted ReviewReceipt with no critical finding. Codex then stages and commits the exact accepted patch and invokes integrate with the real canonical commit SHA. The B self-use key becomes true only when task, graph, handoff, review, integration, and installed-code acceptance receipts all resolve to the same task/base/patch/commit chain.

No direct canonical commit, informal chat handoff, retroactive task, self-review, fixture receipt, or unreviewed patch counts. Absence of local CLI preflight does not block B when the honest EXTERNAL_SESSION chain is complete, and that chain does not count toward TARGET_HOST_ACCEPTED.

- [ ] **Step 4: Make the same receipt chain mandatory in C, D, E, and F plans**

Each subsystem plan must freeze and run at least one substantive production task before its production commit can count:

- DELIVERABLE_C_MODEL_REGISTRY: real model-registry/benchmark-harness production code.
- DELIVERABLE_D_DATASET_INTAKE: real dataset-license/provenance intake production code.
- DELIVERABLE_E_LOCAL_SERVICE_SDK: real local service/typed SDK production code.
- DELIVERABLE_F_CLI_ADAPTER: real shared SDK/service CLI adapter production code.

Each task requires its own content-addressed TaskManifest, graph nodes/edges, LOCAL_ADAPTER or EXTERNAL_SESSION HandoffReceipt, distinct accepted ReviewReceipt, Codex IntegrationReceipt, and installed-code acceptance receipt. Each subsystem plan owns its task paths and exact RED/GREEN command. Documentation-only, tests-only, demo-only, fixture, or canned work leaves that capability's PRODUCTION_SELF_USE key false. CORE_CODE_COMPLETE requires all five B–F keys true plus the spec's other core gates.

- [ ] **Step 5: Run the Build Fabric milestone gate once**

    python3 -m compileall -q src tools
    PYTHONPATH=src:. python3 -m unittest discover -s tests -v
    git diff --check

Expected: PASS within 1200 seconds. One rerun is allowed only after a substantive correction.

- [ ] **Step 6: Prove clean runtime installation**

    BUILD_FABRIC_WHEEL_ROOT="$(mktemp -d /tmp/helios-build-wheel.XXXXXX)"
    python3 -m pip wheel --no-deps --no-build-isolation --wheel-dir "$BUILD_FABRIC_WHEEL_ROOT" .
    python3 -m venv "$BUILD_FABRIC_WHEEL_ROOT/venv"
    "$BUILD_FABRIC_WHEEL_ROOT/venv/bin/pip" install --no-deps "$BUILD_FABRIC_WHEEL_ROOT"/*.whl
    "$BUILD_FABRIC_WHEEL_ROOT/venv/bin/python" -c 'import importlib.util; assert importlib.util.find_spec("tools.helios_build") is None'
    test ! -e "$BUILD_FABRIC_WHEEL_ROOT/venv/bin/helios-build"
    "$BUILD_FABRIC_WHEEL_ROOT/venv/bin/helios-p0" --help
    "$BUILD_FABRIC_WHEEL_ROOT/venv/bin/helios-p1a" --help
    "$BUILD_FABRIC_WHEEL_ROOT/venv/bin/helios-engine" --help

- [ ] **Step 7: Keep target-Mac local-provider acceptance separate**

TARGET_HOST_ACCEPTED requires doctor and real LOCAL_ADAPTER dispatch on the owner's Mac for Claude plus one secondary builder, each tied to exact successful executable/configuration preflight and a bounded real task. EXTERNAL_SESSION assignments, handoffs, reviews, and integrations are ignored by this receipt even when they completed B–F core self-use. Missing CLI, mismatched digest, expired authentication, or MFA leaves TARGET_HOST_ACCEPTED pending without invalidating completed core self-use.

- [ ] **Step 8: Write truthful verification evidence**

Record exact commit, platform, commands, durations, exits, receipt hashes, wheel filename/hash, first-B task/base/patch/review/integration chain, and limitations. BUILD_FABRIC_CORE_CODE_COMPLETE requires implementation Tasks 1–7 and 9 plus the Task 8 ownership gate. CORE_CODE_COMPLETE additionally requires all five B–F PRODUCTION_SELF_USE keys. Do not mark RESEARCH_ROUNDTRIP_ACCEPTED, TARGET_HOST_ACCEPTED, or CYCLE_COMPLETE without their distinct evidence. Explicitly state that ATHENA schemas, paths, receipts, and CLI routes are outside this plan.

- [ ] **Step 9: Commit only real sanitized evidence**

Stage docs/verification/build-fabric-mvp-acceptance.md plus each exact Build Fabric task, lifecycle/routing event, and graph/receipts path printed by the CLI. Do not use a directory-wide git add because research-owned graph nodes or directories may exist concurrently. Then commit:

    git commit -m "docs(build): record build-fabric acceptance"

Never add adapter configuration, credentials, raw logs, sessions, private sources, worktrees, or fixture-provider receipts.

## Final Plan Review

- [ ] Build Fabric core coverage is explicit: schemas/profiles Tasks 1/4; preflight 4; graph/collisions 3; dispatch/idempotency/checkpoints 5; collection/review/budgets 6; integration/status 7; frozen ATHENA seam 8; core CLI/wheel proof 9; real self-use 10.
- [ ] The dedicated ATHENA plan exclusively owns all research/source schemas, profiles/instructions, directories, modules, receipts, and tests; none appears in a Build Fabric file or commit list.
- [ ] Node/edge vocabulary, legal states, successor behavior, descendant blocking, lane cap, and reviewer separation match the accepted spec.
- [ ] Collision detection covers explicit files, globs, modules, migrations, schemas, and public interfaces.
- [ ] Build Fabric never imports runtime/P0/P1A code and never uses any database.
- [ ] Runtime wheel remains rooted at src and contains no build-control code or entry point.
- [ ] Every implementation task has one RED, one GREEN, an exact command/budget, and a commit boundary; Task 8 is explicitly a no-file coordination gate.
- [ ] Affected and milestone gates enforce one initial run, one substantive-correction rerun, and no unchanged retry.
- [ ] Task 10 proves the first substantive B task has a frozen task/graph plus real LOCAL_ADAPTER or EXTERNAL_SESSION handoff, distinct review, Codex integration, and installed-code acceptance receipts; unavailable local execution explicitly reroutes to EXTERNAL_SESSION or blocks B, with no outside-fabric bypass.
- [ ] Each C, D, E, and F subsystem plan must produce its own equivalent task/graph/handoff/review/integration/installed-code receipt chain before its production commit counts; CORE_CODE_COMPLETE requires all five named PRODUCTION_SELF_USE keys.
- [ ] EXTERNAL_SESSION assignments preserve worker/session/raw-evidence provenance but create no preflight or availability claim and never count toward the separate target-Mac Claude-plus-secondary TARGET_HOST_ACCEPTED receipt.
- [ ] Build-fabric, core-code, research-roundtrip, target-host, and cycle status remain separate and truthful.
