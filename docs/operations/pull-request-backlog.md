# Open PR recovery queue

Owner request: address all 14 open PRs. Snapshot: 2026-09-23 UTC.
This is the GitHub delivery queue, not a replacement for the product controller's
CURRENT_STATUS.md. Product work in the main checkout remains untouched.

Evidence: [GitHub snapshot](pull-request-backlog-snapshot.json), including exact
heads, checks, unresolved thread URLs and the active ruleset. Snapshot counts are
observations, not verified defect counts; duplicate or stale review findings must
be assessed against candidate bytes before changes or thread resolution.

## Finish line

Each PR ends in one of: (1) independently reviewed and merged with required checks
and approvals on the latest compatible candidate, then resulting main CI verified;
(2) closed as duplicate or obsolete after explicit disposition and preservation of
useful work; or (3) a named blocker with an executable next action. A green review
job does not equal an approving review. Do not self-approve or dismiss findings to
make the queue green. Ordinary merge commits only; preserve branches and history.

## Per-PR work and endpoints

| PR | Current finding / disposition | Next action and completion condition |
| --- | --- | --- |
| [25](https://github.com/bbukolla-eng/Heleos-spark/pull/25) | One-line main CI repair; exact same change is in 27. CI green but review threads/approval outstanding. | Prefer the smallest reviewed recovery change. Settle its three review threads and required approval; merge only through protections. If 27 lands the identical repair first, verify main contains it before closing 25 as duplicate. |
| [27](https://github.com/bbukolla-eng/Heleos-spark/pull/27) | Current merge-policy correction. CI green on snapshot head; changes requested; documentation still described active Cursor. | Correct policy and disabled-Cursor status (local candidate prepared). Reconcile provider/submission evidence and backlog enrollment findings; independent latest-head review and required approvals before merge. GitHub-only automatic fixes remain a separate unfinished capability. |
| [19](https://github.com/bbukolla-eng/Heleos-spark/pull/19) | Checkout action major-version bump; prose failure and missing Copilot acknowledgement. | After shared repair lands, update by merge without rewriting history; verify action pin, runner compatibility, provenance table and actual affected jobs. Obtain current Copilot review and mandatory approval. |
| [20](https://github.com/bbukolla-eng/Heleos-spark/pull/20) | Claude action bump; prose failure and missing Copilot acknowledgement. | Verify changed action prefetch/egress behavior against the existing approved record before acceptance; update provenance, incorporate shared repair and get current checks/review. An action version bump is not automatically safe. |
| [13](https://github.com/bbukolla-eng/Heleos-spark/pull/13) | CI/egress regression tests; shared prose failure and unresolved coverage findings. | Verify root-relative policy AND decision hashes and malformed registry handling against actual tests. Preserve useful tests, fix valid gaps, rerun focused suite after shared repair, then independent review. |
| [14](https://github.com/bbukolla-eng/Heleos-spark/pull/14) | More CI/gate regression tests; shared prose failure and cleanup/coverage findings. | Inspect temporary-directory lifecycle and assertion strength before accepting resource-leak reports; fix confirmed issues, run focused tests and verify compatibility with 13. |
| [17](https://github.com/bbukolla-eng/Heleos-spark/pull/17) | Review-gate regression tests; shared prose failure and API-fixture/CLI coverage findings. | Verify force-push payload against actual GitHub API evidence, cover CLI failure and Copilot safety branches, then run focused tests and review. Do not count fabricated payloads as API validation. |
| [16](https://github.com/bbukolla-eng/Heleos-spark/pull/16) | Older automatic-label/Amazon Q proposal; many unresolved threads; overlaps 21 and 27. | Compare proposed behavior with the approved GitHub-only rollout. Preserve useful tested changes; do not merge broad auto-enrollment or inaccurate policy claims. Owner disposition needed before closing the superseded proposal. |
| [21](https://github.com/bbukolla-eng/Heleos-spark/pull/21) | Older draft-PR/auto-merge proposal. Shared prose failure, API request-method and trusted-workflow findings. | Decide supersession versus repair with 16/27. If retained: fix GET listing, helper availability and trusted execution before enabling any write capability. Required checks and independent security review must pass. |
| [22](https://github.com/bbukolla-eng/Heleos-spark/pull/22) | Child of 21. Its PR lookup still returns HTTP 403; owner changes-requested review. | Depends on 21 disposition. Fix initial lookup method and test error handling if retained; do not enable Actions approval rights merely to hide the error. If 21 is withdrawn, close this child with it after preservation review. |
| [24](https://github.com/bbukolla-eng/Heleos-spark/pull/24) | Active product integration; large moving branch; unresolved technical/authority findings and CodeQL high gate. | Controller owns candidate writes. Investigate CodeQL annotation at tests/continuity/test_apply_github_app_decisions.py:123 without exposing fixture contents as credentials. Verify Rust CI coverage and source/provenance findings. Freeze a candidate, repair valid findings, then full required review/CI. Do not mutate the controller's staged work from this side task. |
| [3](https://github.com/bbukolla-eng/Heleos-spark/pull/3) | Old draft lane/guard proposal with merge conflicts and no current checks. | Reconcile lane and hook ownership with current 24 before any integration. Preserve genuinely needed guard behavior; close if explicitly superseded, otherwise isolate compatible changes and test them. |
| [1](https://github.com/bbukolla-eng/Heleos-spark/pull/1) | Old conflicting Python build proposal, stale verification claims and security/correctness findings. | Establish whether this branch is in the prohibited predecessor scope before inspecting implementation. Do not import it into the current Rust build. Obtain explicit retain/withdraw disposition; retained work needs fresh scoped acceptance, not stale PR-body claims. |
| [11](https://github.com/bbukolla-eng/Heleos-spark/pull/11) | Draft child of 1, no checks, disputed blanket boundary-test exemption. | Follows 1 disposition. Do not merge a weakened boundary test to make CI pass. Retain only independently justified fixes if the source boundary permits review. |

## Verified shared blockers

The failed checks logs for PRs 13, 14, 17, 19, 20, 21 and 22 all report
`prose: docs/decisions/README.md:13: em or en dash`. Runs inspected:
34061575102, 34061576619, 34109836356, 34375499568, 35120791191,
35158265071 and 35158662146. These are not seven independent test defects.
PR19 run 34375499638 and PR20 run 35120791291 timed out missing Copilot.
PR22 run 35158657168 reports the PR lookup HTTP 403. Do not rerun these unchanged
without recording the changed prerequisite.

Active ruleset Sparky-9 requires checks, Amazon Q Developer,
copilot-pull-request-reviewer and ai-reviewers, one approving review, last-push
approval, stale-review dismissal, resolved threads, strict base compatibility and
CodeQL. No bypass actors. Native merge method is merge only. Missing required
publisher contexts remain blockers even if the visible completed jobs are green.

At snapshot, 25 and 20 had pending native MERGE requests; 13, 14 and 16 had
legacy automerge labels. The new workflow guard does not itself cancel prior
requests. No backlog enrollment or protection change was performed during this
triage. These requests must be individually reconciled before unattended rollout.

## This pass

- Inventoried all 14 PRs and captured current remote heads/checks/threads.
- Corrected 27 policy wording and recorded that Cursor is disabled and unattended
  GitHub-only fixes remain unfinished.
- Verified 25's one-line repair is identical to the repair already carried by 27.
- No PR merged, closed, approved or declared accepted by this audit.
- Next executable action: settle the small main-CI recovery PR's review blockers;
  independently resolve 27's remaining evidence findings while closure decisions
  are pending. Do not restart product capabilities to service this delivery queue.

NotebookLM disposition: administrative_no_new_claims. This pass records live
GitHub state and corrects documentation; it introduces no mechanical rules or new
provider implementation. The older setup research disposition remains in its
operations record. GitHub CLI reads submitted repository identifiers only. The
candidate documentation will be visible to the already enabled repository review
integrations when pushed; no private drawings, secrets or new provider are added.
