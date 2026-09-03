# Run record: Decision 12, the egress policy (2026-09-03)

**Status:** COMPLETE. Three agents, all returned.
**Produced:** `docs/decisions/2026-09-03-egress-policy.md` (awaiting the owner's determination), `docs/policies/egress.md` version 1, `tools/egress/record.py` and its tests, the ledger check in `tools/ci/checks.py`, and the egress steps in the two Claude workflows.
**Base commit:** `0400963` on `main`.
**Occasion:** the owner asked for Decision 12 to be recorded so the provider secret could be added. The decision could not be recorded honestly without establishing what the workflows actually transmit, how the provider handles it, and how a run can produce the record spec section 5 requires.

## The run

| Workflow | Run id | Agents | Output tokens | Tool uses | Wall clock |
|---|---|---|---|---|---|
| `decision-12-egress` (three parallel establishers: transmission surface, provider terms, submission ledger) | `wf_2f13da88-165` | 3 | 445,622 | 152 | 22 min |

Each agent was read-only on the repository, was required to name a file and line, a command and its output, or a URL it actually fetched for every claim, and was told to mark anything it could not reach as unverifiable rather than filling the gap from memory. All three did so: the eighteen findings carry eighteen unverifiable notes between them, and the material ones are quoted in section 3 of the decision record.

## What each agent established

**Transmission surface.** Read `anthropics/claude-code-action` at the pinned commit `c3d45e8e941e1b2ad7b278c57482d9c5bf1f35b3` through `raw.githubusercontent.com`. Found that `claude-review.yml` runs in agent mode and prefetches nothing, while `claude.yml` runs in tag mode and prefetches issue and pull request text, up to 100 comments, and up to 100 reviews before the first call. In both, file content reaches the provider only as tool results the model pulls, never pushed by the action. Because the review workflow names three documents in its prompt, about 96 KB of governed prose is near certain to be read per run, and `fetch-depth: 0` puts the entire history within reach of `git log` and `git diff`.

**Provider terms.** Fetched Anthropic's consumer terms, commercial terms, privacy policy, data processing addendum, Claude Code data usage page, zero data retention documentation, and the retention and training articles, all returning HTTP 200 on 2026-09-03. Established that the two credentials the workflows accept run under different contracts, and quoted the sentences that differ. This is the finding that changed the recommendation.

**Submission ledger.** Designed the mechanism, wrote it, and ran it before proposing it. Established what can and cannot be captured faithfully from inside a GitHub Actions run, and named five residual gaps rather than claiming the record is complete.

## What could not be verified

Eighteen notes in `findings.json`. Three matter to the decision and are stated in the record rather than hidden:

1. No published page states the factory default of the model improvement toggle for a new Anthropic account, so the owner must read it in the account that would issue a subscription token. This is one reason the recommendation avoids that path.
2. Anthropic publishes no eligibility criteria for zero data retention beyond "subject to Anthropic's approval", so whether this owner would qualify is not determinable.
3. `trust.anthropic.com/subprocessors` renders in JavaScript and returned an empty shell to every retrieval method tried. That a sub-processor list exists and is contractually incorporated through DPA Schedule 4 is verified; its contents are not.

One further caveat from the transmission agent, carried into the policy: it could not confirm that the permission pattern form `Bash(git diff *)` used in `claude-review.yml` actually matches, since the action's documentation uses a colon prefix form. If it does not match, the review runs on read tool output alone. This is the same open question raised on pull request #4 and it remains untested until a secret exists.

## Data kept with this record

| File | Content |
|---|---|
| `findings.json` | The eighteen findings with their sources, confidence, and unverifiable notes |
| `scripts/` | The workflow script exactly as run |

In the script and the findings, `<scratch>` stands for the session's scratch directory and `<repo>` for the checkout; those two substitutions are the only edits made after the run.

## Verification of what this run produced

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v   # 18 tests, OK
python3 tools/ci/checks.py                                # 49 files, 0 failures
python3 tools/egress/record.py check --provider anthropic --purpose demo --data-class INTERNAL \
  --decision docs/decisions/2026-09-03-egress-policy.md --rule egress-3-claude-review   # exit 0
python3 tools/egress/record.py check --provider anthropic --purpose demo --data-class INTERNAL \
  --decision docs/decisions/does-not-exist.md --rule x                                  # exit 1
```

The last two commands are the point of the mechanism: with the decision record present the gate opens, and without it the gate closes before any provider call.
