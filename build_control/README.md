# HELIOS Build Fabric

The Build Fabric is the repository-only control plane that gives Codex a finite,
auditable way to assign, collect, review, and integrate bounded build work. It
is not part of the `helios-takeoff-core` runtime wheel and has no P0 or P1A
database authority.

## Custody and ownership

Committed policy and sanitized evidence live below `build_control/`:

- `schemas/` contains closed local contracts.
- `worker_profiles/` contains command-free capability and authority policy.
- `tasks/sha256/` contains immutable task manifests.
- `graph/` contains graph manifests, hash-chained lifecycle/routing ledgers,
  sanitized receipts, canonical patches, attempts, and assignments.

Mutable and sensitive execution data lives outside the repository at
`HELIOS_BUILD_STATE_ROOT` (or `--state-root`). That external root holds local
adapter configuration, worktrees, bounded attempt data, checkpoints, raw
stdout/stderr, and raw external-session evidence. Raw evidence never enters the
sanitized repository receipts. Adapter configuration names exact executable
hashes and environment-variable names; it contains no secret values and is not
committed.

The Build Fabric may read Git, manifests, and its external state. It may not
import the runtime package, open P0/P1A databases, write projects, evidence,
quantities, pricing, approvals, estimates, or bids, or claim runtime authority.

## Finite operation

Run from a checkout with the optional Build Fabric dependency available:

```bash
python -m tools.helios_build --state-root /safe/external/state doctor
python -m tools.helios_build --state-root /safe/external/state graph status
python -m tools.helios_build task validate build_control/tasks/draft.json
python -m tools.helios_build report
```

All successful output is one compact, sorted JSON object on stdout. Errors are
one compact JSON object on stderr. Exit codes are `0` success, `2` contract or
usage, `3` blocked or unavailable, `4` failed, and `5` outcome unknown. Each
invocation is finite. There is no daemon, polling loop, hidden retry, implicit
provider discovery, autonomous merge, or bid/takeoff authority.

### Exact local adapter

`LOCAL_ADAPTER` is allowed only after `doctor` proves the exact committed
profile plus exact host configuration and executable hash. One dispatch creates
one detached worktree and at most one bounded attempt; collection derives the
patch and changed paths from Git, not worker claims.

```bash
python -m tools.helios_build doctor --adapter-config /safe/adapters.json
python -m tools.helios_build dispatch TASK --adapter-config /safe/adapters.json
python -m tools.helios_build collect TASK
python -m tools.helios_build review TASK --adapter-config /safe/adapters.json
```

### Identified external session

Codex, Claude, Kimi, Grok, Cursor, or Copilot may instead be assigned as an
identified `EXTERNAL_SESSION`. The provider, session ID, profile revision,
routing reason, task, and base commit are frozen before work begins. Import
validates the exact handoff/review, raw-evidence digest, canonical Git patch,
scope, and lineage.

```bash
python -m tools.helios_build external-session begin TASK \
  --worker-profile codex-builder-v1 --provider CODEX --session-id SESSION \
  --role BUILDER --routing-reason EXTERNAL_SESSION_SELECTED
python -m tools.helios_build external-session import-handoff TASK \
  --assignment SHA --handoff /safe/handoff.json --patch /safe/change.patch \
  --raw-evidence /safe/raw-evidence.json
python -m tools.helios_build external-session import-review TASK \
  --assignment SHA --review /safe/review.json --raw-evidence /safe/raw-review.json
```

Imported external sessions are always `target_host_eligible=false`; they cannot
prove target-host execution. A distinct reviewer must accept the exact patch.
Codex creates the canonical commit separately and only then records it:

```bash
python -m tools.helios_build integrate TASK --commit-sha COMMIT
```

## Recovery, successors, and limits

Lifecycle and routing ledgers are append-only, hash-chained event streams.
Recovery replays those streams and content-addressed receipts; it never invents
success. A crash may leave an outcome unknown. Before reconciling a stale lock
or started attempt, the operator must positively prove the recorded process is
dead. Checkpoints are immutable and may be resumed only by an explicitly linked
successor task.

Task manifests freeze ownership, interfaces, dependencies, acceptance gates,
time/cost limits, checkpoint boundaries, and correction budgets. Overlapping
active writers are a collision and block dispatch. Failures or requested
changes create immutable higher-round successors; manifests and receipts are
never edited in place. When a budget is exhausted, the task escalates instead
of looping.

## Extension seam and research ownership

Every top-level command registers through
`tools.helios_build.cli.register_command`. Duplicate names fail during parser
construction. This seam is frozen so a later serialized task can add research
commands without replacing core handlers.

ATHENA research is a separate plan and exclusive owner of:

- research-request, source-registry, source-packet, research-outcome, and
  research-receipt schemas;
- `build_control/worker_profiles/athena-research-v1.json` and ATHENA
  instructions;
- `build_control/source_registry/`, `build_control/source_packets/`, and
  `build_control/outcomes/`;
- `tools/helios_build/research.py`, `source_registry.py`, and
  `research_receipts.py`, plus their tests.

Those paths and research CLI registrations are implemented only by the
dedicated ATHENA plan after this CLI task releases ownership. Core Build Fabric
tasks do not write them or register placeholder research commands.
