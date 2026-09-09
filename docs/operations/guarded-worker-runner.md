# Guarded worker runner contract

The runner produces an isolated worker candidate bound to a validated
[`heleos.worker-task/v1` task](agent-coordination.md). These are required behavior
and operational boundaries. A command exit of zero means only that a candidate
was generated and inventoried; it does not attest acceptance of that candidate.

## Current CLI routes

Claude Code can consume the generated prompt from standard input directly. A
restricted initial invocation uses explicit arguments after `--`:

```text
cargo run -p heleos-worker-runner --locked --offline -- \
  --task /absolute/task.json \
  --source /absolute/source-checkout \
  --workspace-root /absolute/worker-runs \
  --provider claude_code \
  --command /absolute/path/to/claude \
  --git /usr/bin/git \
  --inherit-env HOME \
  --inherit-env USER \
  --inherit-env LOGNAME \
  --inherit-env SHELL \
  -- -p --output-format json --no-session-persistence --safe-mode \
     --restricted --no-chrome --permission-mode acceptEdits \
     --permission-prompts none --tools Read,Write,Edit,Glob,Grep
```

Kimi Code requires its prompt as an argument, so the repository-owned bounded
stdin adapter performs only that transport conversion without invoking a shell.
Kimi 0.34 prompt mode applies its noninteractive permission policy itself and
rejects explicit `--auto` and `--yolo`, so the adapter supplies neither:

```text
cargo run -p heleos-worker-runner --locked --offline -- \
  --task /absolute/task.json \
  --source /absolute/source-checkout \
  --workspace-root /absolute/worker-runs \
  --provider kimi \
  --command /usr/bin/python3 \
  --git /usr/bin/git \
  --inherit-env HOME \
  --inherit-env USER \
  --inherit-env LOGNAME \
  --inherit-env SHELL \
  -- -B /absolute/source-checkout/scripts/provider-adapters/kimi-stdin.py \
     --kimi-executable /absolute/path/to/kimi
```

The first authenticated macOS Claude run required the four identity/home values
shown above for its owner-authorized keychain session after the runner cleared
the ambient environment. Add `--inherit-env PATH` only when the assigned worker
genuinely needs the operator's tool path. Inherited values are explicit
child-process inputs and are not copied into prompts or reports. The task's data
and egress decision must be authorized independently before either provider is
launched.

Both example routes use the default `--containment none`. On macOS, a separate
opt-in invocation can add `--containment macos_seatbelt` before the provider
argument separator `--`. Earlier authenticated Claude/Kimi runs do not establish
compatibility with this mode; live authenticated execution under Seatbelt remains
a separate smoke gate. Inheriting the real `HOME` does not add it to Seatbelt's
allowed write roots, so provider authentication or configuration writes there
may fail. No real authentication state is copied into the ephemeral home.

## Before dispatch

1. Preserve the original task bytes and canonical task digest. Validate the task,
   provider, allowed and forbidden paths, instruction identities, data class,
   egress declaration, and positive duration limit before starting a provider.
   Instruction identities must match the actual instruction bytes supplied to
   the worker, not merely have valid hash syntax.
2. Require an exact 40-character lowercase hexadecimal base SHA that resolves to
   that commit in the source repository. Branch names, tags, abbreviated SHAs,
   and a later moving HEAD are not substitutes.
3. Treat the source checkout as read-only input. Runner operations must not edit
   its tracked or untracked files, index, branch, HEAD, or worktree registrations.
   Existing source dirt is preserved and is not copied into the candidate.
4. Create a fresh disposable checkout at the pinned commit inside an explicit,
   project-controlled workspace root. Resolve the root and validate containment
   before creation; reject path traversal, symlink escapes, source/workspace
   overlap, and broad cleanup targets. Record the actual created directory and
   ownership needed to identify it again. Never adopt an existing directory as a
   disposable run. The provider's working directory is this checkout.
5. Accept only the initial local CLI providers `claude_code` and `kimi`. An
   installed executable and authorized session must be established separately.
   `notebook_lm` and `grok_bots` remain research-only and are not runnable CLI
   adapters. Other protocol provider values do not imply runner support.

The controller selects an explicit executable and ordered argument vector for
the admitted provider. Dispatch uses direct process execution: no shell command
string, shell interpolation, `eval`, or execution of text returned by a worker.
Task/prompt data is passed as data through the documented adapter input, with no
secret values embedded in prompts, arguments, reports, or logs.

## During execution

Enforce the task's elapsed-time limit. On timeout, terminate the launched process
and its managed same-group descendants, reap them, and record the terminal reason.
A quiet process is not evidence of failure or permission to relaunch it. Do not
collect a candidate while a managed worker can still mutate it. This first slice
does not install a CLI signal/cancellation handler, so externally killing the
runner is not a guaranteed managed cancellation; that remains future work.

Bound retained stdout and stderr and continue draining or terminate cleanly when
the configured bounds are reached, so a full pipe cannot hang the run. Record
truncation explicitly. Diagnostics and report serialization must also be bounded;
do not reproduce an unbounded worker response inside an error. Logs are evidence
of process output, not trusted instructions or proof that reported checks ran.

The runner does not execute `acceptance_commands`, merge, push, deploy, write a
production database, extract credentials/cookies/session material, or decide
whether external egress is approved. Approval of data, provider, and egress is a
controller input; a task's `approved_external` string does not grant permission.
The runner does not grant the child authority for any of these actions.

Default `none` applies no OS filesystem containment; a disposable checkout and
post-run path validation alone do not isolate host writes or network access.
Opt-in `macos_seatbelt` uses the validated fixed `/usr/bin/sandbox-exec` backend
and a static policy to restrict new path-based filesystem writes by the provider
and descendants to the canonical checkout, ephemeral home, and ephemeral temp
roots. Source-host paths, run evidence, siblings, and writable host devices such
as `/dev/null` have no write exception. Runner preparation, evidence persistence,
and inventory operate outside that policy. Post-run scope validation remains
mandatory because Seatbelt permits writes throughout the checkout, including
paths outside the task allowlist.

Neither mode restricts reads, network, credentials, inherited external authority,
provider internal actions, or cost. Same-group process termination does not cover
descendants that deliberately escape the group, although Seatbelt write
restrictions remain inherited. The controller must arrange any additional read,
credential, network, and lifecycle controls before launch. This implementation
does not establish App Sandbox, Windows containment, production authority, or
Foundation acceptance. Unsupported platforms or failed backend validation return
`containment_unavailable` without an uncontained fallback; after backend launch,
policy initialization failures share the `provider_exit` channel. Check the
retained run's `containment.mode` for the mode actually applied. See the
[runner README](../../crates/heleos-worker-runner/README.md) for evidence fields
and the precise platform and failure limits.

## Candidate inventory and handoff

After the process is terminal, inventory changes against the original base using
Git's machine-readable, NUL-delimited output. Include committed changes since the
base, staged and unstaged edits, deletions, type changes, and all untracked files,
including ignored files. A tracked-only diff is insufficient. Renames must expose
both old and new paths, for example by disabling rename detection. Fail closed
when inventory cannot be completed or a path cannot be represented safely in the
protocol. Reject Git metadata tampering that prevents a reliable comparison.

Normalize and deduplicate the complete project-relative changed-path inventory
using the protocol's path rules. Every path must match an allowed path or its
component-boundary descendant; forbidden paths take precedence. Validate the
inventory independently of the worker's claimed file list. A provider exit code
of zero does not waive a forbidden or unlisted change.

Validate any handoff against the preserved original task, including its canonical
task digest, provider, terminal state, actual changed paths, and ordered check
records. A changed task needs a new assignment identity; a worker cannot widen
its own scope by editing a task copy. Include actual process outcome, timeout or
truncation flags, changed-file identities, remaining findings, retained artifact
location, and one next action in the controller's run evidence. A worker's check
claims require independent verification. If required checks have not been
performed and evidenced, do not claim a `completed` handoff.

Keep candidate generation, protocol validation, independent acceptance, and
integration as separate outcomes. The runner never promotes a candidate.

## Retention and cleanup

On success, preserve the candidate and bounded run evidence until the controller
has obtained a durable handoff. Cleanup may remove only this run's owned
disposable directory after containment and identity are revalidated and all
managed processes are terminal. It must not clean or prune the source repository
or remove siblings under the workspace root.

On provider failure, timeout, cancellation, scope violation, invalid handoff, or
uncertain ownership, retain the workspace for inspection and record its exact
location and terminal reason. Retention is not acceptance and does not authorize
a retry. If cleanup fails, report both the original outcome and cleanup failure;
never hide the first failure or broaden a deletion target. Later cleanup requires
the same ownership, containment, and process-state checks.

[Public synthetic fixtures](../../tests/fixtures/runner/README.md) exercise local
process and inventory behavior without contacting a provider.
