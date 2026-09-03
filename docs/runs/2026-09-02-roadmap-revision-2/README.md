# Run record: roadmap revision 2 (2026-09-02)

**Status:** COMPLETE (every workflow returned; three runs were cut by a session usage limit and completed by a cache-replaying resume, stated per run below)
**Produced:** `ROADMAP.md` revision 2 and its companion pages on branch `claude/dynamic-workflow-roadmap-156gdn`
**Base commit:** `d85bd86` (`origin/main`)
**Harness:** Claude Code Remote (web session), `Workflow` tool; no token ceiling directive was set, so the ceilings in the workflow policies were advisory for these runs
**Session:** https://claude.ai/code/session_01B2JFyezFSWnrMMbaC7nZ1U
**Record format:** this README is the interim format until `tools/wf/record.py` exists (Phase 0, task P0.5). It lists every workflow run, what it read, what it returned, and where the returned data is kept.

## Runs

Token counts are the harness's per-run subagent totals (output tokens). A run with a resume shows the first attempt plus the resume; a resume replays completed agents from cache and re-runs only the agents the session limit killed, so its token count is the cost of the re-run agents.

| # | Workflow (inline script) | Run id | Agents | Output tokens | Tool uses | Wall clock | Returned |
|---|---|---|---|---|---|---|---|
| 1 | `verify-roadmap-evidence` (ten parallel auditors: branch evidence, PR #1 test re-run, spec coverage for sections 2 to 6, 7 to 11, 12 to 16, Option A audit, Option B audit, roadmap consistency, ECC artifacts, tooling reality) | `wf_63a84d06-5c8` | 10 | 1,397,270 | 196 | 50 min | 230 findings (11 blocker, 110 major, 76 minor, 33 info) and 372 verified facts with commands |
| 2 | `design-operating-model` (judge panel: three designs from the angles spec-fidelity, throughput, risk-first; three judges with the lenses spec author, operator, adversary; one synthesis) | `wf_2374141a-936` | 7 | 1,202,105 | 92 | 56 min | ranking risk-first 94, spec-fidelity 92, throughput 83 (of 120); synthesis became `docs/roadmap/dynamic-workflow-operating-model.md` after editing |
| 3 | `verify-findings-a` (skeptics over the evidence, Option A, and Option B findings: 54 findings, two lenses for blockers and majors, one for minors) | `wf_d2ead64b-2bb` | 10 (7 completed, 3 killed by the session limit; resume re-ran 3) | 888,472 + 320,502 | 125 + 45 | 23 + 13 min | 25 confirmed, 29 corrected, 0 refuted, 0 contested |
| 4 | `verify-findings-b` (skeptics over the spec-coverage findings: 94 findings) | `wf_c346eb0b-a5a` | 16 (14 completed, 2 killed by the session limit; resume re-ran 2) | 1,460,278 + 187,757 | 127 + 20 | 23 + 2 min | 44 confirmed, 50 corrected, 0 refuted, 0 contested |
| 5 | `verify-findings-c` (skeptics over the consistency, ECC, and tooling findings: 49 findings) | `wf_4aeb5d89-ebb` | 10 | 1,099,732 | 115 | 21 min | 21 confirmed, 28 corrected, 0 refuted, 0 contested |
| 6 | `coverage-fill-revision-2` (three completeness critics mapping the requirement checklists onto the revision 2 drafts) | `wf_f5fd156d-81b` | 3 (2 completed, 1 killed by the session limit; resume re-ran 1) | 343,387 + 114,502 | 26 + 11 | 6 + 4 min | 182 rows: 151 covered, 28 weak, 2 missing, 1 misplaced, 0 contradicted |
| 7 | `publication-recheck-revision-2` (two independent re-checkers over the 31 rows run 6 graded below covered, after the fixes; one publication critic over the assembled pages) | `wf_ff0aa308-cf6` | 3 | 489,924 | 71 | 12 min | 31 rows re-checked: 29 covered, 1 weak (S11-1, fixed after the run), 0 missing; 25 critic findings (2 blocker, 12 major, 11 minor) |

Total across the seven runs: 7,503,929 output tokens, 828 tool uses, about 3 hours 30 minutes of workflow wall clock, over one working day.

## What every agent was given

- Read-only access to the working tree at `origin/main` and to full exports of every `origin` branch (`main`, PR #3, PR #1 at `1a804ed`, the reconciliation branch, the recovery branch), plus a Python 3.12.11 virtualenv for the PR #1 test re-run. Run 7 read the assembled pages in the working tree of this branch.
- The instruction never to modify the working tree, switch branches, commit, or attempt to locate the quarantined predecessor repository.
- A JSON schema for its return, so every result is a validated object rather than prose.

No external provider was called by any agent; the only network use was the `uv` install of `jsonschema`, `pip`, `setuptools`, and `wheel` into the scratch virtualenv before run 1's test re-run (recorded in the evidence page) and one `git fetch` of PR #1's branch by the author after run 3 reported that its head had moved.

## Data kept with this record

| File | Content |
|---|---|
| `findings.json` | The 230 findings of run 1 with the skeptic verdicts of runs 3 to 5 attached under `verification` (`status`: confirmed, corrected, or not verified; `materiality`: structural or wording; every lens's `checked`, `correction`, and `resolution`). The 33 informational findings were not sent to the skeptics by design and carry `not verified` |
| `coverage-rows.json` | The 195 requirement checklist rows of run 1 (spec sections 2 to 16) with the revision 1 treatment; 13 rows with an empty id are audit meta-facts and were skipped by run 6 |
| `judgments.md` | The three judges' scores, strengths, weaknesses, errors, grafts, and "missing everywhere" lists from run 2 |
| `scripts/` | The inline workflow scripts exactly as run (the generalized versions of runs 1, 3 to 5, and 2 live under `.claude/workflows/` as `spec-coverage-audit`, `verify-findings`, and `judge-panel`; runs 6 and 7 are one-off scripts not yet generalized) |

The harness journals (`journal.jsonl`, one line per agent return) live in the session's transcript directory on the launching machine and are not committed; the files above are extracted from them. In the scripts and data files, `<scratch>` stands for the session's scratch directory (branch exports lived at `<scratch>/branches/<name>`, findings and verdicts at `<scratch>/findings/`) and `<repo>` for the checkout; these two substitutions are the only edits made to the scripts after they ran.

## How the results were used

1. Every finding that reached the skeptics survived as confirmed or corrected; none was refuted or contested. Corrections (wrong counts, overstated severity, a fix that already existed in the drafts) were folded into `docs/roadmap/phase-0-evidence.md` section 2, the repair list in `docs/roadmap/plan-of-record-audit.md`, and the phase exit criteria in `ROADMAP.md`. Section 4 gives the counts by group.
2. The operating-model synthesis was edited by hand: a scope preface, the note that "the lane" means the session branch under Decision 3, the web-session substitute for `gh`, the Phase 0 versus Phase 1 staging, the bakeoff hardware fields, and the renumbering of Decision 8 to Decision 10.
3. The completeness critics' weak, missing, and misplaced rows (31) were each given the fix they proposed, then re-checked independently in run 7; `docs/roadmap/spec-coverage-matrix.md` carries the draft grade, the fix, and the re-check verdict per row.
4. The publication critic's findings from run 7 were applied before the commit; section 4 lists the ones that were not.

## 4. Verification and completeness runs

Verification design: every finding of severity blocker or major was checked by two skeptics that never shared a context, a `refute` lens (try to show the finding false from the sources) and an `assess` lens (materiality and whether the proposed fix is proportionate); minors got the `refute` lens only; informational findings were not verified. A finding is `confirmed` when every lens agrees with it, `corrected` when a lens changed a fact, a severity, or a resolution while the substance stood, `contested` when lenses disagree, and `refuted` when a lens showed it false with a reproducible command.

| Group | Findings | Confirmed | Corrected | Contested | Refuted | Structural corrections | Wording corrections |
|---|---|---|---|---|---|---|---|
| A: evidence, Option A, Option B | 54 | 25 | 29 | 0 | 0 | 24 | 5 |
| B: spec coverage | 94 | 44 | 50 | 0 | 0 | 29 | 21 |
| C: consistency, ECC, tooling | 49 | 21 | 28 | 0 | 0 | 16 | 12 |
| Informational (not sent) | 33 | | | | | | |

Session-limit interruption: at about 10:00 UTC the harness's session usage limit stopped three agents of run 3, two of run 4, and one of run 6 with an error before they returned. After the limit reset (13:30 UTC) each run was resumed with `resumeFromRunId`; completed agents replayed from cache and only the killed agents re-ran. The resumes returned at 14:37 (run 4), 14:39 (run 6), and 14:47 (run 3). No agent was re-run by hand and no result was edited.

Completeness (run 6) by spec range: sections 2 to 6, 70 rows, 52 covered, 17 weak, 1 missing; sections 7 to 11, 52 rows, 44 covered, 7 weak, 1 missing; sections 12 to 16, 60 rows, 55 covered, 4 weak, 1 misplaced. The gaps clustered in four places: the thirteen section 6 records named only by reference, the section 7 quantity-state tests announced but not bound by the Phase 4 exit, the section 10 worker roster and the CrewAI, n8n, and cloud posture clauses absent, and the section 12 shell-adapter and iPhone elements absent from Phase 5. Each was given a named exit criterion, test, register row, or decision-draft field.

Re-check (run 7): two re-checkers read the published pages without the author's patch list and graded the 31 rows that run 6 had marked weak, missing, or misplaced. 29 are covered, including the misplaced row S12-7 (Decision 7 is now provisional on the Phase 1 platform proof). One row stayed weak: S11-1, the six repository-owned skills of spec section 11, because the table in ROADMAP section 11 named `schedule-reconcile` and `tools/wf/clean_room_check.py` nowhere else. Both were added after the run (Phase 3 workflows and the section 11 table; task P0.7 and the CI paragraph) and were not re-verified by an agent.

Publication critic (run 7): 25 findings, none a spec contradiction and none a prose-rule violation. Applied: PR #1 had moved two commits after the audits read it (the inventory rows, the roadmap status table, and the evidence page now carry both heads and a freshness rule, and the Option B gate re-derives every figure at decision time); wording that overstated verification ("every finding" where the 33 informational findings were never sent) corrected on the roadmap and the evidence page; identifier drift between pages corrected (repairs A22 versus A27, 22 versus 27 repairs, four versus six runs, Decision 4 versus Decision 6 for branch protection, probe ids colliding with exit-check ids, gap ids B1 to B9 reused as task labels, the intake outcome enumerations and the platform list in the operating model differing from the roadmap, the summary table's 6 of 10 that should read 4 of 10, the example contract's migration path); the `/council` reference replaced by `judge-panel`; the token-ceiling sentences in ROADMAP section 11 and CLAUDE.md now state that the read-only audit workflows ran without a ceiling; the Draft 5 GitHub API endpoint replaced by one a user login can call. Adapted rather than applied: the critic read this record while it was still a template and read `findings.json` before the run 3 resume returned, so its blocker about 17 unverified findings describes an earlier state; the counts in this record are the resumed ones and no finding is left unverified except the 33 informational ones.
