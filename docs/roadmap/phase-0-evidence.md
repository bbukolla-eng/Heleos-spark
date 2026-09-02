# Phase 0 evidence: state of the repository on 2026-09-02

**Status:** Verified facts for the owner's Phase 0 decisions (revision 2)
**Date:** 2026-09-02
**Supersedes:** the 2026-09-01 page of the same name on pull request #3 (`claude/heleos-spark-branch-60gd5e`, commit `861fcfe`)
**Method:** Read-only inspection of every `origin` branch from a scratch clone, a clean-export test run of PR #1 under CPython 3.12.11, a simulation of the PR #3 lane guard, and an inventory of the Claude Code Remote harness. Ten independent audit agents produced the facts; independent skeptic agents then re-checked every blocker, major, and minor finding (197 of 230) against the primary sources; the 33 informational findings were not sent to the skeptics. Every number below has a reproduction command. Nothing on `main` or on any other branch was modified.

## 1. Branch inventory

| Branch | Head | Ahead / behind `main` | Size | Content | Status |
|---|---|---|---|---|---|
| `main` | `d85bd86` | 0 / 0 | 14 tracked files | Spec (2026-08-26), README, and the 12 ECC-generated files merged as PR #2 on 2026-09-01 | Design phase; no code |
| `claude/heleos-spark-branch-60gd5e` | `861fcfe` | 4 / 0 | 23 files, +3,998 | Lane guard (`.claude/hooks`, `.githooks`, `tests/hooks`), `CLAUDE.md`, roadmap revision 1, evidence page, admission register, mirrored plan and custody record | PR #3, open draft |
| `feat/enriched-build-fabric` | `1a804ed` (audited); `11a3595` on `origin` since 2026-09-02 11:29 UTC | 4 / 13 at `1a804ed`; 6 / 13 at `11a3595` | 293 files changed, +75,428 / -6; 294 tracked files at `1a804ed` (302 changed, +76,462 / -6, 303 tracked at `11a3595`) | `helios-takeoff-core` 0.1.0: P0, P1A, P1B, Division 23 v2 registry and kernel, build fabric, ATHENA R1A intake | PR #1, open draft; based on `c579c1b` (before PR #2); merges onto today's `main` without conflicts (`git merge-tree`) |
| `recovery/misplaced-chat-p0-p1a-2026-08-27` | `2c9de80` | 2 / 13 | 3 files, +183 | Custody record, recovered commit map, git bundle of the recovered repository | Custody branch; quarantined from `main` by its own record |
| `docs/recovery-reconciliation-2026-08-27` | `7d7c97c` | 1 / 13 | 4 files, +959 | Reconciliation plan (Option A), provenance record, commit map, README | Never merged |
| `claude/dynamic-workflow-roadmap-156gdn` | from `d85bd86` | this revision | roadmap revision 2 | This page and its companions | Open |

Freshness: PR #1 moved on 2026-09-02 after the audits read it. Its head is now `11a3595`, 6 commits ahead of `main`, 302 files changed (+76,462 / -6), 303 tracked files; the two new commits (`289ca2e`, `11a3595`, 9 files, +1,034) freeze ML manifest contracts and a local service task graph. It still merges onto `main` without conflicts. Every figure on this page describes `1a804ed`, the head the audits read; anything the owner decides about PR #1 is re-derived at the head current at decision time.

Other facts about the branches:

- At the audit snapshot, no audited branch carried a `.github` directory. This revision subsequently added the GitHub Actions workflows documented in `docs/policies/github-automation.md`; they were not part of the ten-agent evidence audit.
- No branch carries a `docs/decisions` record except PR #3, whose README records only the lane decision of 2026-09-01.
- The ECC bundle branch `ecc-tools/Heleos-spark-1788252490451` no longer exists as a ref; its 12 `ecc-tools[bot]` commits survive through merge commit `d85bd86`.
- The string `HELEO_HELIOS` (the quarantined predecessor) appears five times across all branches, every time as a policy statement forbidding its use. Nothing references its content.

Reproduce:

```bash
git fetch origin
for b in claude/heleos-spark-branch-60gd5e feat/enriched-build-fabric \
         recovery/misplaced-chat-p0-p1a-2026-08-27 docs/recovery-reconciliation-2026-08-27; do
  echo "$b: ahead $(git rev-list --count origin/main..origin/$b), behind $(git rev-list --count origin/$b..origin/main)"
  git diff --stat origin/main...origin/$b | tail -1
done
git ls-tree -r --name-only origin/main | wc -l                       # 14
git ls-tree -r --name-only origin/feat/enriched-build-fabric | wc -l # 294 at 1a804ed; 303 at 11a3595
git log --oneline 1a804ed..origin/feat/enriched-build-fabric          # 289ca2e, 11a3595 (2026-09-02)
git merge-tree --write-tree origin/main origin/feat/enriched-build-fabric; echo "exit=$?"
for r in $(git for-each-ref --format='%(refname:short)' refs/remotes/origin); do
  echo "$r: $(git ls-tree -r --name-only $r | grep -c '^\.github/') workflow files"
done
```

## 2. Corrections to the 2026-09-01 evidence page

| 2026-09-01 statement | Verified value on 2026-09-02 | Why it differed |
|---|---|---|
| PR #3 is 3 commits ahead of `main` | 4 | The commit that added the page (`861fcfe`) was not counted |
| Import commit `8c9900e` modified 12 recovered files | 17 modified, 37 byte-identical, 36 added | The list omitted `docs/architecture/agentic-layer-boundary.md`, the P1A plan, and five test files; 37 + 12 did not add up to the 54 recovered files |
| PR #1 is "294 files, +75,428 lines" | 293 files changed (+75,428 / -6); 294 tracked files at the tip | 294 is the tree size; `README.md` is modified, not added |
| The later three PR #1 commits "add 208 files" and "eleven plan and design documents dated 2026-08-27 to 2026-09-01" | 208 files touched (204 added, 4 modified); 10 plan and spec documents added, all dated 2026-09-01 | The 2026-08-27 and 2026-08-31 documents arrived in the import commit |
| "The PR body reports all gates passing" | The PR body claims bounded gates: "Division 23 kernel focused gate: 14/14 PASS", an affected gate, and a kernel review with zero findings. It never claims the full suite passes | Paraphrase overstated the claim; the 14/14 figure is reproducible |
| The `test_build_preflight` error means "schema and payloads are out of sync" | Only `athena.v1.json` carries the five rejected properties; it is a different protocol and validates cleanly against its own schema. The defect is in `tools/helios_build/doctor.py`, which forces every profile file through the worker-profile-v1 schema | Root cause was attributed to the data instead of the loader |
| The `test_build_authority_boundary` failure is "either a real boundary breach or a false positive; the owner's reviewer must decide" | It is a scanner false positive that can be decided from code (section 4 below) | Not analysed on 2026-09-01 |
| Runtime-only run: 191 tests, 1 failure, 10 errors | True only when `pip` is importable; in a venv without `pip` the same run gives 191 tests, 3 failures, 10 errors | Two wheel-boundary tests shell out to `python -m pip wheel` |

## 3. Provenance chain of the implementation in PR #1

1. **Custody record facts are correct.** The bundle at `docs/recovery/artifacts/helios-takeoff-core-2026-08-27.bundle` on the recovery branch has SHA-256 `6e0b19f955b436299cc6086927451100076820c4d8d86a1553b686449a9f7cf9`, 114,801 bytes, three refs, and a complete history. It holds 17 linear commits authored "Codex" on 2026-08-27 (root `8d9ea9f`, P0 tip `aa98477` with 39 tracked files, P1A tip `21fd6d5` with 54 tracked files, P0 tree `a78c2e3a`). The recovered commit map TSV matches the bundle row for row, including tree ids. The recovered `pyproject.toml` declares `helios-takeoff-core` 0.1.0 with `database_schema_version = "0"` while its migrations reach 013.
2. **The import commit is the recovered work plus edits, not a rewrite.** `8c9900e` ("feat: import verified HELIOS P0-P1B foundations", 2026-09-01 04:45 -0400) versus the recovered P1A tip: 37 files byte-identical, 17 modified (`README.md`, `docs/architecture/agentic-layer-boundary.md`, the P1A plan and design, `pyproject.toml`, `src/helios_takeoff_core/agentic/{artifacts,contracts,repository,service}.py`, `api.py`, `db.py`, migration 013, and five test modules), 36 files added. Migrations 001 to 012 are byte-identical to the recovered ones and 013 is modified. That is the transplant the custody record forbids ("no recovered migration is renumbered or copied into the canonical migration lineage") and the reconciliation plan forbids ("Do not cherry-pick recovered commits or copy migrations 001 to 013"). PR #1 records none of this provenance.
3. **Part of the import has no preserved history.** The commit message of `8c9900e` and the enriched design both cite base commit `6d50910727b14fedfe28ce5c1e6171b8fab9fc0c` as the "verified local foundation head". That commit exists in no branch and not in the bundle. The 36 added files, including the 2026-08-31 P1B plan, design, acceptance record, and code, therefore arrive without history. The same is true of every commit SHA cited by the build fabric's integration receipts and task manifests (`f3ff0f36`, `bffd15c4`, `d3f980dd`, `0da163c2`, `4fe7ea49`, `69725512`, and others): none resolves in the repository or the bundle, so the chain "handoff patch equals canonical commit" cannot be verified from the repository.
4. **The later commits.** `6da0d70` (design, 2 files, +713), `a0cbfda` (plans, 8 files, +6,730), `1a804ed` (implementation, 202 files, +49,491), all on 2026-09-01, all with author and committer `bbukolla-eng`, unsigned; together 204 files added and 4 modified (+56,926 / -3), including ten plan and design documents dated 2026-09-01. The 2026-08-27 and 2026-08-31 P0, P1A, and P1B plans and specs arrived with the import commit `8c9900e`. The branch's historical verification records are traceable to trees, not to the commits they cite: the 31 tests in `docs/verification/p0-acceptance.md` reproduce at the bundle's P0 tip `aa98477` rather than at `10b2442` where the file places them, and the 141 tests in `p1b-engine-foundation-acceptance.md` reproduce at the import tree `8c9900e` while the cited commit `dfac38f` exists nowhere (reproduced in verification run 3, finding `option-b-audit/verification-docs-stale-and-unverifiable` in `docs/runs/2026-09-02-roadmap-revision-2/findings.json`).
5. **The approval claim lives only inside the branch.** `docs/superpowers/specs/2026-09-01-enriched-build-fabric-design.md` line 3 reads "**Status:** Owner-approved; implementation plan recorded"; line 705 reads "The owner approved this written specification on 2026-09-01"; line 15 states that where the spec places PDF and drawing intake first, "this later owner decision controls". In `6da0d70` (04:46) the status line read "Owner-approved architecture; written specification awaiting final review"; `a0cbfda` (05:53) upgraded it. No decision record exists on `main` or under `docs/decisions` on any branch. Spec section 16 requires a recorded owner decision for a material change to the first milestone.
6. **Review independence inside PR #1.** All 20 build-fabric assignments used provider Codex for both builder and reviewer (10 builder, 10 reviewer sessions), with Codex as integrator; the code enforces only distinct profile id, profile hash, and session id (`tools/helios_build/review.py` 243 to 265, `integrate.py` 79 and 102), never worker or provider distinctness.

Reproduce:

```bash
mkdir -p /tmp/scratch && git clone -q . /tmp/scratch/clone && cd /tmp/scratch/clone
git show origin/recovery/misplaced-chat-p0-p1a-2026-08-27:docs/recovery/artifacts/helios-takeoff-core-2026-08-27.bundle > rec.bundle
sha256sum rec.bundle && git bundle verify rec.bundle
git fetch rec.bundle refs/heads/feat/p1a-execution-spine:refs/heads/recovered-p1a
git rev-list --count recovered-p1a                                  # 17
git diff --name-status recovered-p1a 8c9900e | cut -c1 | sort | uniq -c   # 36 A, 17 M
for m in 001 002 003 004 005 006 007 008 009 010 011 012 013; do
  cmp <(git show recovered-p1a:src/helios_takeoff_core/migrations/${m}_*.sql) \
      <(git show 8c9900e:src/helios_takeoff_core/migrations/${m}_*.sql) && echo "$m identical" || echo "$m differs"
done
git cat-file -t 6d50910727b14fedfe28ce5c1e6171b8fab9fc0c            # fatal: not a valid object name
git show origin/feat/enriched-build-fabric:docs/superpowers/specs/2026-09-01-enriched-build-fabric-design.md | sed -n '3p;15p;705p'
git diff 6da0d70 a0cbfda -- docs/superpowers/specs/2026-09-01-enriched-build-fabric-design.md | grep '^[-+]\*\*Status'
```

## 4. PR #1 test suite from a clean export

Environment: Linux x86_64, CPython 3.12.11 in a fresh virtualenv, `PYTHONPATH=src python -m unittest discover -s tests`, no network during the tests. Not run on macOS or Windows.

| Setup | Tests | Result |
|---|---|---|
| Runtime only, venv without `pip` or `jsonschema` | 191 | 3 failures, 10 errors (all 10 errors are `ModuleNotFoundError: jsonschema`; two of the failures are the wheel-boundary tests that need `pip`) |
| With `jsonschema` 4.26.0, `pip` 26.2.1, `setuptools` 84.0.0, `wheel` 0.48.0 | 279 | 1 failure, 1 error; 277 pass |

This confirms the 2026-09-01 headline. The two remaining defects are code-level and do not depend on the environment:

| Test | Kind | Root cause from code | Fix that would be needed |
|---|---|---|---|
| `test_build_authority_boundary...test_build_tools_contain_no_p0_or_p1a_table_access` | Failure | Scanner false positive. The test's quoted-name regex matches the JSON dictionary subscript `packet["conflicts"]` at `tools/helios_build/research.py:235` and `tools/helios_build/source_registry.py:622`; `conflicts` is a P0 table name from migration 004. No SQL pattern fires, no SQL keyword exists anywhere under `tools/helios_build`, and the sibling import-boundary test (forbidding `helios_takeoff_core`, `sqlite3`, `sqlalchemy`) passes. There is no authority-boundary breach. | Make the scanner ignore quoted dictionary keys, or rename the packet field. Separately, the scanner builds its table list only from `src/helios_takeoff_core/migrations`, so the engine v2 store's tables are outside the boundary it claims to enforce. |
| `test_build_preflight...test_doctor_reports_each_committed_profile_without_claiming_unconfigured_availability` | Error | `tools/helios_build/doctor.py:276-278` globs every `build_control/worker_profiles/*.json` and loads each through `load_worker_profile`, which validates against `worker-profile-v1.schema.json`. `athena.v1.json` is protocol `helios.build.athena-worker-profile/v1`; it validates cleanly against its own committed schema and fails the worker-profile-v1 schema on five extra properties and eight missing required ones. All eight worker profiles validate cleanly. | Dispatch on the profile's `protocol` field in `doctor.py`. |

How the defects reached the PR head with green receipts: the build fabric's gates are bounded module lists, not the full suite, so a receipt's GREEN claim is scoped to the modules it lists. The earlier build-fabric core task gates did include `tests.test_build_preflight` and `tests.test_build_authority_boundary`; the later ATHENA intake task, which added a file to the directory `doctor.py` scans, never re-ran those consumers, and its handoff receipt reports "GREEN: 12 of 12 tests passed". All relevant files land in the single commit `1a804ed`, so no intermediate green full-suite state is recorded. The repository's own declared verification command (`README.md` line 64, full discovery) is red. PR #1 has zero GitHub check runs.

Other verified facts about PR #1:

- No PDF handling of any kind exists; the five `pdf` hits are disclaimer strings and a path used as a citation locator. No `.pdf` fixture is tracked.
- No content-addressed vault for document bytes, no `content_objects`, `documents`, `project_documents`, `ingest_events`, `sheets`, `scales`, `job_runs`, `source_records`, `evidence_objects`, `corrections`, or `audit_events` table. Of the 13 records in spec section 6, only `projects` and `document_revisions` exist, and `document_revisions` stores a caller-supplied SHA-256 with no bytes, vault key, length, media type, or page records. A content-addressed artifact store exists for P1A worker artifacts (`agentic/artifacts.py`) but not for documents, and it has no orphan reconciliation or backup verification.
- No network client imports in `src`, `tools`, or `tests` (only `urllib.parse`); the suite ran without network. No dynamic zero-network assertion exists.
- No reference to the predecessor, no model identifiers or unpinned `latest` pins, no secrets.
- `requires-python = ">=3.12"`. `[tool.helios] database_schema_version = "17"` matches the 17 P0/P1A/P1B migrations but is asserted only as a literal in one test and read by no runtime code; the engine v2 store's own migration is not covered by it.
- 279 `def test_` functions across 39 modules, equal to the discovered count.
- The two wheel-boundary tests build in place and write `build/` and `src/helios_takeoff_core.egg-info/` into the checkout (gitignored). One test in the Division 23 kernel module imports `jsonschema`, so the "14/14" kernel gate needs the `build-fabric` optional extra.
- The authority-boundary scan covers the P0, P1A, and P1B tables only; the seven engine v2 store tables (`engine_packs`, `engine_store_migrations`, and five index and event tables in `engine/v2/migrations/001_engine_registry.sql`) are outside it. Inspection found no violation there, so this is a scope gap in the test, not a defect in the code.
- Package identity remains `helios-takeoff-core==0.1.0`, which the custody record says must not be published as canonical; only `database_schema_version` was corrected (0 to 17). Renaming the package would touch 78 tracked files (81 counting the dashed distribution name), including every migration data path, three console scripts, tests, and the build-control receipts and patches.
- ATHENA research requests restrict `data_class` to `PUBLIC` and `AUTHORIZED_LICENSED`; no schema carries a per-submission purpose or policy decision as spec section 5 requires, and no real submission exists on the branch.
- Deterministic-controller primitives exist as repository tooling (`tools/helios_build`: attempt timeouts, ownership collision detection, idempotent replay, correction budgets, a `CORRECTION_BUDGET_EXHAUSTED` stop condition) but with `cost_microusd: 0` in every receipt, no leases or heartbeats, and no runtime job model.
- The enriched cycle's deliverables C to F (ML registry, dataset intake, local service and SDK, CLI parity, the P0 extraction-claim adapter) are absent; `PROJECT_BOUND` evaluation raises "not implemented".

Reproduce:

```bash
git archive origin/feat/enriched-build-fabric | tar -x -C /tmp/scratch/pr1 && cd /tmp/scratch/pr1
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python jsonschema==4.26.0 pip setuptools wheel
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests 2>&1 | tail -3   # Ran 279 tests ... failures=1, errors=1
grep -n 'conflicts' tools/helios_build/research.py tools/helios_build/source_registry.py
sed -n 270,280p tools/helios_build/doctor.py
grep -rli pdf src tools tests; grep -n 'CREATE TABLE' src/helios_takeoff_core/migrations/*.sql
```

## 5. PR #1 against spec section 14

| Section 14 item or acceptance bullet | State in PR #1 at `1a804ed` |
|---|---|
| Item 1: repository policy, dependency manifests, formatting, typing, test harness | Partial: `pyproject.toml` and a `unittest` harness; no formatter or type-checker configuration, no CI, no repository policy artifact |
| Item 2: versioned SQLite schema and forward migrations | Present for the P0/P1A/P1B record set (17 migrations plus one engine migration), not for the section 6 record set |
| Item 3: content-addressed evidence vault with atomic writes, orphan reconciliation, backup and restore verification | Absent for documents; not scheduled by any PR #1 plan |
| Item 4: deterministic PDF intake, metadata extraction, per-page records | Absent; the design defers drawing intake indefinitely |
| Item 5: typed domain and error contracts | Present for its own domain; not the section 6 vocabulary |
| Item 6: audit events, finite job state, idempotency, crash-safe retry | Partial: P1A job lifecycle with timeouts and idempotent replay; no `audit_events` or `job_runs` table as specified, no leases or heartbeats |
| Item 7: unit, integration, migration, corruption, duplicate-ingest, restart tests | Partial: corruption and restart tests cover worker artifacts and engine packs, not document intake |
| Acceptance bullets 1 to 8 (PDF fixture ingested twice, stable hashes, one content object, two intake attempts, page facts, migration procedure, interrupted-ingest restart, typed outcomes, manifest retrieval) | Not demonstrable in the ingest-twice scenario: no PDF fixture, no intake, no vault. Bullet 5 has partial standalone coverage (`tests/test_database.py` migrates from an empty database); no rollback or recovery procedure |
| Bullet 9: zero external network calls in the default test path | Supported statically only |
| Bullet 10: all declared automated checks passing from a clean checkout | Fails today (section 4) |

## 6. The Option A plan as written

The reconciliation plan (`docs/superpowers/plans/2026-08-27-recovery-reconciliation.md`, byte-identical on its branch and on PR #3 apart from a two-line mirror note) covers every section 14 item with at least one task, but it is not executable in its written order. Verified by simulation with the plan's own `discover_migrations()` code and by cross-referencing every task's file list:

| Defect | Where | Effect |
|---|---|---|
| Task 4 creates migration `0004` before Tasks 5 and 6 create `0002` and `0003`; the Task 2 runner rejects sequence gaps | plan lines 252 to 266, 366, 440, 519 | `MigrationSequenceError` at the Task 4 and Task 5 commits; every test that applies packaged migrations fails until Task 6 |
| Task 1's platform probe opens `tests/fixtures/valid-two-page.pdf` through the PDFium adapter; both are created in Task 5 | lines 155, 162, 438, 442 | Task 1 cannot pass its own steps 5 and 6 |
| Task 3's identity test calls `service.ingest()` with a PDF fixture; intake arrives in Task 5 and the service in Task 7 | lines 306 to 311, 356 | Task 3 cannot reach "zero failures" |
| The two Task 2 tests need contradictory initial states of the shared `migration_dir` fixture | lines 204 to 221 | One of the two always fails or raises the wrong exception |
| Migration `0004_foundation_recovery_guards.sql` has no described content | lines 366, 424 | Unspecified production behaviour |
| Nine pytest fixtures, `SimulatedInterruption`, the failpoint hooks, and `VALID_EVIDENCE` are never assigned to a file, while the plan forbids touching undeclared paths | lines 25, 194, 306 to 315, 378 to 386, 460 to 474, 534 to 558, 603 to 610 | Tasks 3 to 7 cannot complete without violating the plan's own rule |
| `vault.corrupt_for_test()` and `repository.associate_foreign_revision_for_test()` place mutation hooks in production modules | lines 315, 386 | Contradicts spec section 13 (vault append-only to application code) |
| Fixture generation (valid, near-duplicate, encrypted, corrupt PDFs) has no producer, tool, or license record | lines 107, 441 to 445, 453 to 455 | Provenance gap; the venv has no PDF library |
| The PDF backend is named PDFium in the tech stack, the file name, and Task 1 before the proof that is supposed to select it | lines 9, 52, 155, 175, 438 | A platform failure has no fallback |
| `python -m build`, wheel install, and "install into an empty environment" have no declared dependency, backend, or offline index | lines 129, 666, 680, 683 | Requires network inside the acceptance path, contrary to bullet 9 |
| Acceptance is bound to a "dependency lock" that no task creates | lines 88, 103, 165, 662 | Unsatisfiable as written |
| No task creates any CI workflow, although "Windows CI" is a mandatory proof target | lines 9, 20, 157 to 165 | Every platform proof is a manual run on the owner's machines |
| Acceptance bullets without a task or test: migration rollback/recovery procedure (plan is forward-only), zero-network mechanism (subprocess CLI tests are invisible to an in-process guard), units and derivative lineage (page identities only; no derivative artifact exists), the near-duplicate typed outcome (never defined), the "evidence manifest" (never defined), "all declared automated checks" (declared only in prose) | lines 19, 21, 285, 373, 429, 465, 486 to 495, 616, 641, 666, 674 to 683 | Bullets 4, 5, 7, 8, 9, 10 need additions |
| Task 9's regressions reference entities (RFIs, releases, estimates, currency, approval actors, preflight receipts, egress policy) that exist in no canonical migration | lines 710 to 726 | Cannot be written "against canonical contracts" without new schema or the recovered bundle as an explicit input |
| Spec section 13 items absent from the plan: encrypted backups, audit-event chaining or export | lines 415, 524, 529, 569 | Section 13 gaps |

Dependency analysis: as written the nine tasks are strictly serial and Tasks 1, 3, and 4 depend on later artifacts. After renumbering migrations by commit order, moving the fixture and adapter probe into Task 1, and assigning fixture ownership, the critical path is T1, T2, T3, T5, T7, T8, T9, with T4 (vault and backup) parallel to T3 and T6 (jobs and audit) parallel to T5. The plan yields 9 commits, 14 written test functions, and roughly 54 tests once every "Expected" list is implemented. No step cherry-picks recovered code.

Reproduce: `docs/roadmap/plan-of-record-audit.md` carries the simulation scripts and the full mapping.

## 7. Tooling reality in the Claude Code Remote harness

Measured in this session (CLAUDECODE=1, CLAUDE_CODE_REMOTE=true, working directory `/home/user/Heleos-spark`):

| Assumed by roadmap revision 1 | Present here |
|---|---|
| Seven reference repositories under `/home/user` (superpowers, ECC, council-of-high-intelligence, loop-engineering, graph-engineering, codebase-memory-mcp, claude-cookbooks) | None. `/home/user` contains only `Heleos-spark` |
| `superpowers:*` sub-skills (brainstorming, executing-plans, test-driven-development, verification-before-completion, subagent-driven-development) | Absent. The only "superpowers" is a single account-synced custom `SKILL.md`, not the obra/superpowers plugin; no plugin is installed (`claude plugin list`: none) |
| ECC agents and commands (tdd-guide, python-reviewer, database-reviewer, security-reviewer, silent-failure-hunter, rag-pipeline-reviewer, performance-optimizer, code-reviewer, pr-test-analyzer, `/code-review`, eval-harness, benchmark) | Absent (the ECC-generated repo files on `main` are not the agent pack) |
| `/council` | Absent |
| graph-engineering `/kg-*` prompts, loop-engineering templates | Absent |
| Codebase Memory MCP, Codex Security, `gh` CLI | Absent |
| GitHub Actions workflows | None existed in the audited snapshot; this revision adds the workflows documented in `docs/policies/github-automation.md`; the runner service is available |
| Python 3.12 floor | Default `python3` is 3.11.15; 3.12.3 at `/usr/bin/python3.12`; 3.12.11 installable through `uv` |

Present and usable from a fresh clone: `uv` 0.8.17, `git` 2.43, `node` 22, `ruff`, `mypy`, and `pytest` as `uv` tools (running on 3.11), the harness connectors Context7, GitHub MCP, Exa, and Hugging Face MCP, the Claude Code `Workflow` tool with its `workflow-authoring` skill, the `Agent` tool, `EnterWorktree` and `ExitWorktree`, Claude Code Remote sessions and triggers, and the built-in `code-review`, `security-review`, `simplify`, `loop`, and `run` skills. The account-synced `pdf` and `xlsx` skills are Anthropic-provided skills, not Claude Code built-ins.

The PR #3 hooks run in this environment: 27 hook tests pass under both `pytest` and the documented `unittest` command.

## 8. What the PR #3 lane guard would do to a session like this one

Scratch simulation with a bare origin holding `main` at PR #3's content, the lane branch, and a session branch:

| Case | Result |
|---|---|
| Clean web session on the session branch, default environment | `session_start.sh` checks out the lane branch, prints "[LaneStatus] Remote session moved to the lane branch", and sets `core.hooksPath`; the session's own harness branch is left behind unpushed |
| Same, with `HELEOS_LANE_AUTOSWITCH=0` | Every `Edit`, `git commit`, shell redirect, and `git push -u origin <session-branch>` is denied (exit 2); only `git checkout <lane>` and read-only commands pass |
| Dirty tree | Not switched; stays off-lane and read-only |
| Git-layer hooks alone, for a git process that inherits `CLAUDECODE` or `CLAUDE_CODE_REMOTE` (both are set in this session's environment) | `pre-commit` blocks the commit; `pre-push` refuses the session branch. Whether the harness's own push flow inherits those variables was not demonstrated |
| Lane branch deleted on `origin` after merge | The next clean web session recreates the lane from the session branch's HEAD |
| On the lane | Pushing the session branch, `git worktree add`, and `git checkout -b` are denied |
| Owner-created worktree | Edits denied when the guard runs from the worktree's hooks; commits inside the worktree blocked by `pre-commit` |

Context: Claude Code on the web creates a `claude/<slug>-<id>` branch for every session (this one was cut from `main` by the harness), and the lane name `claude/heleos-spark-branch-60gd5e` itself follows that pattern. The auto-switch is PR #3's documented reconciliation for clean web sessions. What remains blocked: a session with a dirty tree or `HELEOS_LANE_AUTOSWITCH=0`, the harness's own push flow from a session branch, and the worktrees that spec section 10 requires for isolated workers. Decision 3 in `ROADMAP.md` addresses this.

## 9. The ECC-generated files on `main`

- The bundle was generated at 2026-09-01T08:48:02Z from PR #1's branch at `6da0d70`, two to three minutes after those commits landed. Every repository signal in the skill, instincts, identity file, and readiness score describes PR #1's tree; `main` has no code.
- Several signals are wrong even for PR #1: functions are not camelCase, tests are `tests/test_*.py` not `*.test.ts`, `unittest` is the framework, and the tree holds two packages.
- The skill is live: Claude Code advertises `.claude/skills/Heleos-spark/SKILL.md` by name and description in every session on this repository and loads its body when it is invoked, and `main` has no `CLAUDE.md` to override it. `openai.yaml` enables implicit invocation for Codex.
- `.codex/config.toml`, run as-is, fetches five MCP servers unpinned through `npx -y` (two pinned to `@latest`), connects to the remote Exa endpoint, enables live web search, attaches Playwright to the owner's running browser through the Playwright bridge extension when that extension is installed (`--extension`), persists a Memory knowledge graph outside the repository, and spawns up to six parallel agents with an unpinned model alias and no budget. Nothing in it distinguishes data classes.
- `.codex/AGENTS.md` cites a root `AGENTS.md` that exists on no branch.
- The ECC Tools GitHub App holds at least `contents:write` and `pull_requests:write`: it authored the 12 commits and opened PR #2.

## 10. Not verifiable from the repository

GitHub-side states beyond what the API returned for PR #1 (open, draft, `mergeable_state: clean`, 0 check runs, 293 changed files at `1a804ed` when queried on 2026-09-02, 302 at `11a3595`); the Amazon Q review on PR #3; the P0 release ZIP and the unpublished security audit named in the custody record; the commit pins of external repositories named in revision 1's register (absent locally, no network used); whether the PR #1 suite passes on macOS or Windows.
