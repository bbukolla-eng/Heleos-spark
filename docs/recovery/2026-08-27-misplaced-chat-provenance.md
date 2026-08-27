# Misplaced-Chat Recovery Provenance

**Recorded:** 2026-08-27  
**Canonical repository:** `bbukolla-eng/Heleos-spark`  
**Decision owner:** Bekim Bukolla  
**Recovery status:** Preserved, quarantined from `main`, not approved for production use
**Recovery branch:** `recovery/misplaced-chat-p0-p1a-2026-08-27`

## Owner decision and clean-room boundary

The owner identified the recovered `helios-takeoff-core` Git repository as new
`Heleos-spark` work that was created in the wrong ChatGPT conversation and directed
Codex to recover it. This authorization applies only to the repository and artifacts
enumerated below. Ephemeral workspace paths are intentionally excluded from the
permanent record.

It does **not** relax the binding quarantine on the former `HELEO_HELIOS_2.0`
repository. No old code, Git objects, schemas, tests, configurations, dependencies,
datasets, generated files, issues, pull requests, or history may enter
`Heleos-spark`.

The recovered work is preserved because it is owner-identified clean-room work.
Preservation is not acceptance. Its code and migrations may not be merged into
`main` until the reconciliation plan is executed and the applicable acceptance gates
pass.

## Remote state before recovery

The connected GitHub account `bbukolla-eng` had administrator and push permission to
the private repository. Remote `main` pointed to:

```text
c579c1b43a5af5e2068efe7e0c330d1e09ba1c0e docs: add clean-room project readme
538b436e733e3804fb894772f969a54ed55db48b docs: establish clean-room foundation design
```

No implementation branch from the misplaced conversation existed remotely.

## Recovered Git repository

Verified state:

| Fact | Verified value |
|---|---|
| Worktree/index | Clean |
| Remotes | None |
| Tags | None |
| Commit count | 17 |
| History shape | Linear; zero merge commits |
| Root | `8d9ea9fa2375cfea738680ab8d7a4ee3bcc30be5` |
| P0 tip | `aa98477c801358bef6a5bf5124da9c2efb03601b` |
| P1A/current tip | `21fd6d5976b529ce994ed76172caa5675a4e3a86` |
| Tracked files at current tip | 54 |

All three local branch refs are preserved in the recovery bundle:

```text
main                       8d9ea9fa2375cfea738680ab8d7a4ee3bcc30be5
feat/p0-foundation         aa98477c801358bef6a5bf5124da9c2efb03601b
feat/p1a-execution-spine   21fd6d5976b529ce994ed76172caa5675a4e3a86
```

`git fsck --full --no-reflogs` found one unreachable dangling blob,
`946c5ffc4484c94da6c1799bcb2f5e228d79af6b`. It is not reachable from any
preserved branch and is not included as accepted project content.

## Recovery bundle

The exact reachable Git history is stored on the recovery branch as:

```text
docs/recovery/artifacts/helios-takeoff-core-2026-08-27.bundle
```

| Property | Value |
|---|---|
| SHA-256 | `6e0b19f955b436299cc6086927451100076820c4d8d86a1553b686449a9f7cf9` |
| Size | 114,801 bytes |
| Refs | Three branches listed above |
| History | Complete; no prerequisites |
| Git object hash | SHA-1 |

Verification command:

```bash
git bundle verify docs/recovery/artifacts/helios-takeoff-core-2026-08-27.bundle
```

Expected statements include `The bundle records a complete history` and the three
branch refs above. To inspect without modifying the canonical repository:

```bash
git clone docs/recovery/artifacts/helios-takeoff-core-2026-08-27.bundle recovered-review
git -C recovered-review fsck --full
git -C recovered-review log --oneline --all --decorate --graph
```

## P0 release artifact

The misplaced workspace also contained:

```text
HELIOS_Takeoff_Core_P0_0.1.0.zip
```

| Property | Value |
|---|---|
| SHA-256 | `07095ed0ae75bb860c02804561800b299304a2d51d91cf8ec2536d31195bc49d` |
| Size | 137,680 bytes |
| ZIP entries | 82 |
| Uncompressed bytes | 436,494 |
| Source commit | `aa98477c801358bef6a5bf5124da9c2efb03601b` |
| Source tree | `a78c2e3a22a3f240a10b1c42982a27eba2b1d2a8` |
| Embedded wheel SHA-256 | `356b164a27375ed3227de42f12252d3dadeea0d5cb7f59bd36a035b993c1f269` |

All 39 archived Git-tracked files match the P0 source commit byte-for-byte. The
remaining 22 source-tree files are generated `build/lib` and `.egg-info` artifacts.
The ZIP is a consistent P0 package; it is not a snapshot of the later P1A tip.

The ZIP was independently verified in the misplaced workspace but is not added to
the recovery branch. The complete Git bundle already preserves its source commit;
the separate generated archive is outside the owner-authorized recovery scope.

## Verification evidence

Independent read-only verification produced:

- archived P0: 31 tests run, zero failures;
- current P1A workspace: 61 tests run, zero failures;
- fresh installation of the supplied P0 wheel: successful;
- fresh build and installation of the current workspace: successful;
- SQLite integrity checks: `ok`, with zero foreign-key violations;
- migrations 1–8 and 1–13: repeat initialization successful;
- P0 database upgrade through migration 13: existing project preserved;
- OpenAPI: 28 documented routes matched the API source and all 253 internal
  references resolved.

These results prove only the implemented and tested surfaces. They do not establish
production readiness or an operating HVAC takeoff engine. The release-blocking
findings are converted into regression and architecture requirements in
`docs/superpowers/plans/2026-08-27-recovery-reconciliation.md`; the separate detailed
security audit is intentionally not published to the repository.

## Package identity collision

Both the P0 archive and materially different P1A workspace identify the package as
`helios-takeoff-core==0.1.0`. The current `pyproject.toml` also declares a stale
database schema version of `0`, while the actual migration maxima are 8 and 13.
No recovered package may be published or installed as a canonical
`Heleos-spark` release until naming and versioning are corrected.

## Branch and merge policy

1. The recovery branch is a custody branch, not an implementation branch.
2. The original bundle and ZIP are immutable recovery artifacts.
3. No pull request is opened from the recovery branch to `main`.
4. No recovered migration is renumbered or copied into the canonical migration
   lineage.
5. Compatible concepts are independently adapted through the reconciliation plan.
6. Production acceptance must use the real vault, real PDF adapter, real SQLite
   migrations, and real restart/restore path; demonstration data cannot satisfy it.
7. Verification is bounded: run the changed task's checks once, repair concrete
   failures, rerun the affected checks, and run the complete suite once at the
   milestone gate. Repeating unchanged tests without a new failure hypothesis is not
   project progress.
