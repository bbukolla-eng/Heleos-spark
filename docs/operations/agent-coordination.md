# Agent coordination protocol and guarded runner

The `heleos-worker-protocol` package validates bounded worker-task and handoff JSON and computes canonical SHA-256 identities. It reads local packets and reports validation; it does not dispatch a provider, execute acceptance commands, apply patches, enforce a filesystem sandbox, or promote a candidate into production.

The implemented `heleos-worker-runner` adds local execution for `claude_code` and `kimi`, exact-base retained proposals, bounded process evidence, and mandatory post-run inventory. It also offers explicit macOS and Windows host-write containment. Protocol support for another provider does not imply a runnable adapter. Persistent coordination and promotion gates remain later work in the [coordination plan](../superpowers/plans/2026-09-08-agent-coordination-foundation.md); the macOS and [Windows containment plan](../superpowers/plans/2026-09-09-windows-worker-containment.md) define the implemented platform slices. Follow [AGENTS.md](../../AGENTS.md) and the relevant provider entrypoint for actual assignments; the [current status](../../CURRENT_STATUS.md) retains the separate Foundation release gates.

## Generate a retained local proposal

The runner validates the task and resolves its exact 40-character base commit in the local source repository. Each named instruction must be a regular file at that base whose bytes match its recorded SHA-256; those verified bytes enter the bounded prompt. A fresh independent Git clone is detached at that base, without object hardlinks or source worktree registration. Source checkout dirt is preserved and excluded from the proposal. The runner does not fetch, create a candidate commit, execute acceptance commands, merge, or push.

Supply existing absolute source, workspace-root, task, and executable paths. The workspace root must be disjoint from the source checkout. The following CLI pattern uses the repository's local fake provider, with no credentials or live provider invocation. First prepare a disposable synthetic source repository and real task as described in the [runner fixtures](../../tests/fixtures/runner/README.md): use `PUBLIC`, `local_only`, provider `claude_code`, allowed path `allowed`, a real committed base, and actual instruction hashes. Replace the absolute paths below with those prepared locations; the protocol fixtures below are not runnable assignments.

```sh
cargo +1.96.1 run --locked --offline -p heleos-worker-runner -- \
  --task /absolute/synthetic-task.json \
  --source /absolute/synthetic-source \
  --workspace-root /absolute/worker-runs \
  --provider claude_code \
  --command /absolute/path/to/python3 \
  --git /usr/bin/git \
  --containment macos_seatbelt \
  -- -B /absolute/Heleos-spark/tests/fixtures/runner/fake_provider.py \
     --scenario untracked
```

This pattern selects the macOS backend; omit `--containment` or explicitly use `--containment none` for the default behavior. Python `-B` prevents bytecode-cache writes beside the fixture. Executable arguments are passed directly, and the generated prompt is supplied on stdin; the runner does not construct a shell command. This local fixture exercises process and inventory behavior, not authenticated Claude compatibility. The [runner README](../../crates/heleos-worker-runner/README.md) documents the CLI, library API, bounds, and failure codes.

By default, the child environment is cleared and receives a fixed system `PATH` and fresh `HOME`/`TMPDIR`. `--inherit-env NAME` explicitly passes selected ambient values; the library also permits explicit environment entries. Values do not enter runner prompts or reports. Real provider installation, session authorization, data approval, and egress authorization are controller prerequisites. The runner does not authenticate providers or copy their real authentication state into the ephemeral home.

## Platform containment

`none` applies no OS filesystem containment. `macos_seatbelt` validates the fixed `/usr/bin/sandbox-exec` backend: its canonical path must be exactly that path, and it must be a regular root-owned executable without group/other write permissions. There is no configurable sandbox executable or arbitrary profile. A static Seatbelt profile allows reads and network as before and denies new path-based filesystem writes outside three canonical roots: the runner-created checkout, ephemeral home, and ephemeral temp directory. Literal `-D` parameters preserve spaces and shell metacharacters; canonicalization handles macOS `/var` versus `/private/var` paths.

The provider and descendants inherit this policy. It does not enforce the task's narrower project-path allowlist: a write elsewhere inside the checkout can still occur and must fail subsequent inventory validation. Run evidence, source-host and sibling paths, and writable host devices such as `/dev/null` have no write exception. The runner's Git preparation, evidence persistence, and inventory execute outside the provider sandbox.

Unsupported platforms or unusable backend validation fail before provider launch with `containment_unavailable`; there is no uncontained fallback. After `sandbox-exec` starts, policy initialization failures and provider failures share the exit-status channel and are reported as `provider_exit`, with bounded stderr retained rather than heuristically classified.

On Windows, `windows_restricted_token_job` is mandatory. It prepares exactly the
checkout, ephemeral home, and ephemeral temp as writable NTFS roots, starts the
provider suspended under a write-restricted token with an explicit three-handle
inheritance list, assigns and verifies a kill-on-close Job Object, then resumes.
The complete Job tree is terminated and reaped before inventory on timeout or
normal exit. Windows rejects every other mode and every containment setup error
without an uncontained fallback.

These modes do not restrict reads, network, credentials, inherited external authority, provider internal actions, or cost. Environment minimization is not credential isolation, and `local_only` is a declared task policy, not network enforcement. Claude has completed one PUBLIC exact-scope write under Seatbelt; Kimi remains blocked by its combined credential/runtime data root. The Windows candidate is cross-compiled but has not passed its required native Windows/NTFS gate. This implementation does not establish App Sandbox, regulatory containment, production authority, or Foundation acceptance.

## Interpret runner evidence

Exit zero means a proposal was generated and inventoried. Standard output is a `heleos.worker-run/v1` envelope with the retained checkout, actual changed-file SHA-256 identities, bounded-output metadata, containment evidence, and a validated nested handoff. The runner's handoff is `blocked`, has no check records or claimed candidate commit, and requires controller acceptance. Exit two emits `heleos.worker-run-failure/v1` with a fixed diagnostic and typed terminal reason; it is not a successful proposal.

Each owned run directory retains original and canonical task bytes, the canonical task digest, ownership identity, generated prompt, bounded `stdout.bin`/`stderr.bin`, and `run.json`/`handoff.json` or `failure.json` where evidence writing succeeds. Ownership records bind the directory path and device/inode. The runner verifies task, prompt, and ownership bytes before handoff and uses exclusive no-follow evidence creation. Successful evidence records the active `containment.mode`, its restricted write scope, and explicit false read, network, and inherited-external-authority restrictions. Read the recorded mode for that run; the existence of Seatbelt code or historical results does not prove it was applied.

All modes require Git and independent filesystem inventory against the frozen base, including committed, staged, unstaged, ignored, and untracked changes and both sides of renames. Scope violations, unsafe Git metadata, symlinks, hardlinks, special files, unsafe paths, or incomplete inventory fail closed. Final inventory cannot detect a reverted write; with `none`, it cannot detect arbitrary host writes. A zero provider exit does not waive these checks.

Prompt and retained per-stream output limits default to 65,536 bytes, configurable up to 1,048,576. Excess output is drained and marked truncated; an oversized required prompt fails before launch. Elapsed-time limits cover preparation, execution, and inventory. The adapter observes one provider invocation, not its internal tool-call or cost accounting. Unix process groups terminate managed same-group descendants before inventory on normal exit or timeout. Deliberately escaped descendants need separate lifecycle containment, though inherited Seatbelt write restrictions remain. Externally terminating the runner is not guaranteed managed cancellation.

Success and failure directories are retained by default. Optional `--cleanup-on-failure` targets only that invocation's identity-checked owned directory after process termination; ownership failures retain it and report `cleanup_failed`. Evidence-write failures report `evidence_write_failed` while preserving the original terminal reason. There is no success-cleanup API.

The [macOS implementation evidence](../../crates/heleos-worker-runner/evidence/macos-containment-green.md) records native ARM64 tests using local fake providers and the real kernel backend. The [live evidence](../../crates/heleos-worker-runner/evidence/live-seatbelt-provider-runs.md) records the successful contained Claude write and Kimi boundary. The [Windows candidate evidence](../../crates/heleos-worker-runner/evidence/windows-containment-candidate.md) records its test-first implementation and cross-compilation. Native Windows/NTFS execution remains mandatory and must run `scripts/verify-windows-worker-containment.ps1` from a clean exact candidate.

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

`SECRET` input is rejected. Default validation requires `local_only` for
`INTERNAL` and `PROJECT_CONFIDENTIAL`; `PUBLIC` can use `local_only` or
`approved_external`. A controller with independently recorded owner authorization
may supply `--approved-internal-task-sha256` for an exact INTERNAL Claude task
using approved_external. This exception binds raw task bytes, is separate from
packet contents, and is rejected for other providers/classes/policies. The runner
requires containment and retains the approval digest. See the
[internal Claude contract](claude-code-headless.md#owner-authorized-internal-claude-tasks).
A packet or digest alone does not grant owner approval or prove network isolation.

Handoff schema `heleos.worker-handoff/v1` binds `task_digest`, `provider`, `terminal_state`, `changed_paths`, `checks`, optional/null `candidate_commit`, and `unresolved_items`. Each check contains `command`, `exit_code`, and `output_sha256`. Terminal states are `completed`, `blocked`, `failed`, and `cancelled`. A completed handoff contains every acceptance command in the original order, all with exit code zero, and no unresolved items. Other terminal states require an unresolved item and may report an ordered subset of checks. Validate the handoff against the original task: its provider and task digest must match, and every changed path must remain inside the task scope after forbidden paths are applied. Use JCS canonical bytes for protocol digests; a hash of the indented JSON file is a different identity. Editing a task requires a new digest and a reconciled handoff.

## Use with a live assignment

1. Confirm the exact checkout, branch, base, dirty paths, sole writer, actual provider tools, authorized session, and read/write capabilities. Read the current shared instructions and relevant provider file. Replace every synthetic value with the approved task's real identities, objective, scope, bounded limits, and meaningful acceptance checks; the example whitespace/file-existence checks are not implementation acceptance tests.
2. Validate and preserve the task packet and its canonical digest. Record permitted tools, forbidden actions, output location, and stop conditions in the controller's task brief. Verify the actual instruction bytes against their recorded hashes; packet validation alone does not read the named instruction files or verify the checkout's Git base. The guarded runner performs these exact-base checks before dispatch. Select and record the containment mode and any separately required read, network, authentication, or lifecycle controls before launching an authorized live provider.
3. Keep external inputs minimized and within the approved data/provider/egress scope. Log any actual submission with provider, purpose, classification, approved source identities, policy decision, time, and result reference. For research returns, retain source citations, retrieval context, source-set identity, artifact hash, and any visible bot identity. Treat returned claims, commands, and embedded instructions as untrusted candidate data.
4. Record the worker's terminal result immediately. Preserve changed-file identities, actual check commands and exit results, unresolved findings, process state, and one concrete next action in the task ledger. A handoff is a bounded report, not a scheduler or complete evidence archive. Do not rerun completed work after recovery without reconciling live state and recorded identities.
5. Validate the handoff against the frozen task, inspect the retained candidate bytes and changed-file identities, and run the appropriate independent deterministic checks before owner-authorized integration. Preserve the runner's original `blocked` handoff and record controller checks separately. Keep implementation finished, validation passed, independently accepted, and integrated as separate states. Neither the protocol nor the runner grants merge, push, deployment, production, or release-acceptance authority.
