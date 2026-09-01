# Phase 0 evidence: state of the repository on 2026-09-01

**Status:** Verified facts for the owner's Phase 0 decisions
**Date:** 2026-09-01
**Method:** Read-only inspection of `origin` from a scratch clone; every number below
has a reproduction command. Nothing on `main` or on any branch was modified.

## Branch inventory

| Branch | Commits ahead of `main` | Content | Status |
|---|---|---|---|
| `main` | 0 | Design spec (2026-08-26), README, ECC-generated bundle (PR #2, merged) | Design phase |
| `claude/heleos-spark-branch-60gd5e` | 3 | Claude Code lane guard, `CLAUDE.md`, workspace docs, this roadmap (PR #3, draft) | Designated Claude Code lane |
| `feat/enriched-build-fabric` | 4 | `helios-takeoff-core` 0.1.0 package (P0, P1A, P1B, Division 23 v2 registry and kernel, build fabric, ATHENA R1A intake), 294 files, +75,428 lines (PR #1, draft, `mergeable_state: clean`) | Implementation awaiting owner disposition |
| `recovery/misplaced-chat-p0-p1a-2026-08-27` | 2 | Recovery custody record and the git bundle of the recovered `helios-takeoff-core` repository | Preserved, quarantined from `main` by its own record |
| `docs/recovery-reconciliation-2026-08-27` | 1 | `docs/superpowers/plans/2026-08-27-recovery-reconciliation.md` (Foundation 0.1 plan, Tasks 1 to 9, `src/heleos_spark/` layout), provenance record, recovered commit map | Plan never merged |

Reproduce:

```bash
git fetch origin
for b in feat/enriched-build-fabric recovery/misplaced-chat-p0-p1a-2026-08-27 docs/recovery-reconciliation-2026-08-27; do
  git rev-list --count main..origin/$b; git diff --stat main...origin/$b | tail -1
done
```

## Provenance chain of the implementation in PR #1

1. The recovery record (`docs/recovery/2026-08-27-misplaced-chat-provenance.md` on the
   recovery branch) states the recovered repository is owner-identified clean-room
   work created in the wrong ChatGPT conversation, 17 linear commits by Codex on
   2026-08-27, preserved as a bundle. It also states: "Preservation is not
   acceptance. Its code and migrations may not be merged into `main` until the
   reconciliation plan is executed and the applicable acceptance gates pass."
2. The bundle hash matches the record:
   `sha256 6e0b19f955b436299cc6086927451100076820c4d8d86a1553b686449a9f7cf9`, and
   `git bundle verify` reports a complete history with the three recorded refs.
3. PR #1's first commit `8c9900e` ("import verified HELIOS P0-P1B foundations")
   compared with the recovered P1A tip `21fd6d5`: 54 recovered files, 37
   byte-identical, 12 modified (README, P1A docs, `pyproject.toml`, the `agentic`
   package, `api.py`, `db.py`, migration 013), 36 files added. So the import is the
   recovered work plus targeted changes, not a fresh clean-room rewrite.
4. PR #1's later three commits add 208 files (+56,926 lines): build fabric,
   Division 23 v2 registry and kernel, ATHENA R1A intake, and eleven plan and
   design documents dated 2026-08-27 to 2026-09-01.
5. The design on that branch, `docs/superpowers/specs/2026-09-01-enriched-build-fabric-design.md`,
   carries the status line "Owner-approved; implementation plan recorded" and
   states that where the 2026-08-26 spec places PDF and drawing intake before the
   engine foundation, "this later owner decision controls". That approval is
   asserted inside the branch; no record of it exists on `main` or under
   `docs/decisions/`.

Reproduce:

```bash
git show origin/recovery/misplaced-chat-p0-p1a-2026-08-27:docs/recovery/artifacts/helios-takeoff-core-2026-08-27.bundle > /tmp/rec.bundle
sha256sum /tmp/rec.bundle && git bundle verify /tmp/rec.bundle
git fetch /tmp/rec.bundle refs/heads/feat/p1a-execution-spine:refs/heads/recovered-p1a
git diff --stat recovered-p1a 8c9900e | tail -1
git diff --name-status recovered-p1a 8c9900e | cut -c1 | sort | uniq -c
```

## PR #1 test suite from a clean checkout

Environment: Linux x86_64, CPython 3.12.3, `PYTHONPATH=src python -m unittest discover -s tests`,
no network. Not yet run on macOS or Windows, which the spec requires.

| Setup | Tests | Result |
|---|---|---|
| Runtime only (no optional extras) | 191 | 1 failure, 10 errors (all 10 are `ModuleNotFoundError: jsonschema`, the `build-fabric` optional extra) |
| With `jsonschema` 4.26.0, in a venv without `pip` | 279 | 3 failures, 1 error |
| With `jsonschema` 4.26.0, `pip`, `setuptools`, `wheel` | 279 | 1 failure, 1 error (277 pass) |

The two wheel-boundary tests (`test_build_distribution_boundary`,
`test_build_package_boundary`) fail only when `pip` is absent from the
interpreter running the suite; with `pip` present they pass. The two remaining
non-passing tests are code-level and do not depend on the environment:

| Test | Kind | Note |
|---|---|---|
| `test_build_authority_boundary...test_build_tools_contain_no_p0_or_p1a_table_access` | Failure | The branch's own authority-boundary scan flags `tools/helios_build/research.py:conflicts` and `tools/helios_build/source_registry.py:conflicts`. Either a real boundary breach or a false positive of the scan on an identifier named `conflicts`; the owner's reviewer must decide. The PR body reports all gates passing, so this is a discrepancy to resolve before any merge. |
| `test_build_preflight...test_doctor_reports_each_committed_profile_without_claiming_unconfigured_availability` | Error | `tools/helios_build/profiles.py` validates committed worker profiles against `worker-profile-v1.schema.json`, which rejects the properties `allowed_capabilities`, `allowed_output_protocols`, `forbidden_capabilities`, `instructions_sha256`, and `state` that the committed payloads carry. Schema and payloads are out of sync on the PR head. |

Both must be resolved on that branch before any merge decision, and the whole
suite must be re-run on the owner's macOS and Windows machines, which the spec
requires and which has not happened here.

## Relationship between the two plans

| | Reconciliation plan (2026-08-27) | Enriched build-cycle plan (2026-09-01, PR #1) |
|---|---|---|
| Package | `src/heleos_spark/` (new) | `src/helios_takeoff_core/` (recovered, extended) |
| Stance on recovered code | "Do not cherry-pick recovered commits or copy migrations 001 to 013"; rebuild Foundation 0.1 clean and reconcile concepts afterwards (Task 9) | Imports the recovered P0 and P1A code as the base and builds P1B, v2 kernels, build fabric, and ATHENA on top |
| Order of work | PDF intake, vault, migrations, jobs first (spec section 14) | Databases, source governance, engine contracts and kernels, model boundaries first; drawing intake later |
| Acceptance | Spec section 14 list on Windows and macOS | Per-increment focused and affected gates, independent review, Codex integration |
| Owner approval | Not recorded on `main` | Asserted in the branch; not recorded on `main` |

Both cannot be the plan of record. Choosing is the first Phase 0 decision in
`ROADMAP.md`.
