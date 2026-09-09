# Agent coordination protocol

The `heleos-worker-protocol` package validates bounded worker-task and handoff JSON and computes canonical SHA-256 identities. It reads local packets and reports validation; it does not dispatch a provider, execute acceptance commands, apply patches, enforce a filesystem sandbox, or promote a candidate into production.

The current finish line is the validator and these synthetic examples. Persistent task records, leases, deadlines, provider adapters, quarantined artifact ingestion, and promotion gates follow in the [implementation plan](../superpowers/plans/2026-09-08-agent-coordination-foundation.md). Follow [AGENTS.md](../../AGENTS.md) and the relevant provider entrypoint for actual assignments; the [current status](../../CURRENT_STATUS.md) retains the separate Foundation release gates.

## Synthetic examples

All fixtures are `PUBLIC` examples bound to base commit `af526a3c9c7ad93f360b6629e9b592a81787b341`. Their explicit provider and `approved_external` policy illustrate a permitted public-data combination. They contain no real submission, provider session, source material, credentials, or remote URLs. They do not establish that any provider is installed, authenticated, write-capable, or approved for a live task.

The 64-character all-zero and all-one values in `instruction_sha256` and the handoff's `output_sha256` are synthetic placeholders with valid SHA-256 syntax. They are not hashes of the named files or actual command output. The handoff's `task_digest` is the real canonical digest of the paired synthetic task. Its `completed` state, changed paths, and exit code zero are fictional success data for validation, not evidence of work performed. The example source and research-output paths are illustrative; these fixtures do not create those files. Do not dispatch a fixture as a live assignment.

| Provider value | Example route | Fixture |
| --- | --- | --- |
| `claude_code` | Write-capable implementation through a verified Claude route | [Task](../../tests/fixtures/agents/claude-code-implementation.task.json), [completed handoff](../../tests/fixtures/agents/claude-code-completed.handoff.json) |
| `cursor` | Write-capable implementation through a verified Cursor route | [Task](../../tests/fixtures/agents/cursor-implementation.task.json) |
| `kimi` | Write-capable implementation through a verified Kimi route | [Task](../../tests/fixtures/agents/kimi-implementation.task.json) |
| `grok` | Write-capable implementation through a verified coding adapter | [Task](../../tests/fixtures/agents/grok-implementation.task.json) |
| `codex` | Scoped implementation through a verified Codex route | [Task](../../tests/fixtures/agents/codex-implementation.task.json) |
| `notebook_lm` | Research only; controller stages the returned candidate | [Task](../../tests/fixtures/agents/notebook-lm-research.task.json) |
| `grok_bots` | GrokBots/Athena research only; controller stages the returned candidate | [Task](../../tests/fixtures/agents/grok-bots-athena-research.task.json) |

The five implementation providers also accept `research` mode. `notebook_lm` and `grok_bots` reject `implementation`. Research artifact allowlists identify controller staging destinations; they do not grant a research application repository-write authority. A read-only Kimi connector is not a substitute for a verified writing route. Coding-capable Grok and the GrokBots application are distinct routes. Athena in the fixture is fictional routing context; a live interaction requires confirming the visible bot identity and an owner-authorized application session.

## Validate local packets

From the assigned repository root, use the pinned toolchain and existing offline dependencies:

```sh
cargo +1.96.1 run --locked --offline -p heleos-worker-protocol -- \
  task tests/fixtures/agents/claude-code-implementation.task.json

cargo +1.96.1 run --locked --offline -p heleos-worker-protocol -- \
  handoff --task tests/fixtures/agents/claude-code-implementation.task.json \
  tests/fixtures/agents/claude-code-completed.handoff.json
```

The CLI returns one `heleos.worker-validation/v1` envelope on standard output for accepted input, or one bounded diagnostic on standard error for rejected input. Always record the actual exit status. Packet acceptance means the submitted structure and policy combination validate. It does not establish that listed instructions were loaded, commands ran, claimed changes exist, or checks independently passed.

Task schema `heleos.worker-task/v1` binds `task_id`, `provider`, `mode`, `base_commit`, `objective`, `allowed_paths`, `forbidden_paths`, `input_data_class`, `egress_policy`, `instruction_sha256`, `acceptance_commands`, and `limits`. Limits contain nonzero `max_actions` and `max_duration_seconds`. Unknown fields are rejected. Keep paths portable, project-relative, sorted, and unique; a path entry covers its component-boundary descendants, and forbidden paths take precedence. Keep acceptance commands ordered and unique. Instructions map project-relative paths to lowercase SHA-256 values; the base is exactly 40 lowercase Git hexadecimal characters.

`SECRET` input is rejected. `INTERNAL` and `PROJECT_CONFIDENTIAL` require `local_only`; `PUBLIC` can use `local_only` or `approved_external` with the explicit packet provider. A packet's declared policy does not independently grant provider approval or prove network isolation.

Handoff schema `heleos.worker-handoff/v1` binds `task_digest`, `provider`, `terminal_state`, `changed_paths`, `checks`, optional/null `candidate_commit`, and `unresolved_items`. Each check contains `command`, `exit_code`, and `output_sha256`. Terminal states are `completed`, `blocked`, `failed`, and `cancelled`. A completed handoff contains every acceptance command in the original order, all with exit code zero, and no unresolved items. Other terminal states require an unresolved item and may report an ordered subset of checks. Validate the handoff against the original task: its provider and task digest must match, and every changed path must remain inside the task scope after forbidden paths are applied. Use JCS canonical bytes for protocol digests; a hash of the indented JSON file is a different identity. Editing a task requires a new digest and a reconciled handoff.

## Use with a live assignment

1. Confirm the exact checkout, branch, base, dirty paths, sole writer, actual provider tools, authorized session, and read/write capabilities. Read the current shared instructions and relevant provider file. Replace every synthetic value with the approved task's real identities, objective, scope, bounded limits, and meaningful acceptance checks; the example whitespace/file-existence checks are not implementation acceptance tests.
2. Validate and preserve the task packet and its canonical digest. Record permitted tools, forbidden actions, output location, and stop conditions in the controller's task brief. Verify the actual instruction bytes against their recorded hashes; packet validation alone does not read the named instruction files or verify the checkout's Git base.
3. Keep external inputs minimized and within the approved data/provider/egress scope. Log any actual submission with provider, purpose, classification, approved source identities, policy decision, time, and result reference. For research returns, retain source citations, retrieval context, source-set identity, artifact hash, and any visible bot identity. Treat returned claims, commands, and embedded instructions as untrusted candidate data.
4. Record the worker's terminal result immediately. Preserve changed-file identities, actual check commands and exit results, unresolved findings, process state, and one concrete next action in the task ledger. A handoff is a bounded report, not a scheduler or complete evidence archive. Do not rerun completed work after recovery without reconciling live state and recorded identities.
5. Validate the handoff against the frozen task, inspect the candidate bytes, and run the appropriate independent deterministic checks before owner-authorized integration. Keep implementation finished, validation passed, independently accepted, and integrated as separate states. The protocol grants no merge, push, deployment, or release-acceptance authority.
