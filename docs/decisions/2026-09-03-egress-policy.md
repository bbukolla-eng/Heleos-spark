# Decision 12: Egress policy for prompts, connectors, and sessions

**Status:** APPROVED

**Decided by:** Bekim Bukolla **Date:** 2026-09-06

**Prepared by:** Claude Code session `session_01B2JFyezFSWnrMMbaC7nZ1U`, on the owner's instruction of 2026-09-03 to record Decision 12 so the provider secret can be added.
**Drafts:** Decision 12 of `ROADMAP.md`, listed at `docs/roadmap/decision-drafts.md` Draft 12, delivering task P0.9.
**Authority:** section 5 of `docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md`.

## How to turn this into a decision

The egress gate in `tools/egress/record.py` looks for `docs/decisions/2026-09-03-egress-policy.md` and requires it to be signed. Existence alone does not open the gate: the file must carry a line reading exactly `**Status:** APPROVED` and a `**Decided by:**` line with a real name on it. That is deliberate, so that a prepared draft can never authorize a submission. The gate blanks out every fenced block, HTML comment, and indented code block before it looks, so an example of a signature is never mistaken for one.

To decide: copy this file to `docs/decisions/2026-09-03-egress-policy.md`, answer the six determinations in section 6, replace the draft banner with `**Status:** APPROVED`, sign the `**Decided by:**` line, and commit. Only then does the gate open, and only then may `ANTHROPIC_API_KEY` be added.

A note on how this draft was made. A session prepared it; the owner decides it. Sections 1 to 5 are research and a recommendation, and every factual claim in them names the page or file it came from. Section 6 is the owner's, and its six determinations are blank on purpose. This ordering is the repository's authority rule, that workers propose and human approval determines truth, applied to itself.

## 1. The question

What may leave this machine and this repository, to whom, from which kind of session, and with what record?

The immediate occasion is narrower. `.github/workflows/claude-review.yml` and `.github/workflows/claude.yml` are merged and inert. They begin sending repository content to Anthropic the moment a provider secret exists. `CLAUDE.md` forbids sending `INTERNAL` material to any external service, and spec section 5 requires a seven-field record for every external submission. Neither condition is satisfied today, so adding the secret without this decision would put the repository in breach of its own rules on the first pull request.

## 2. What actually leaves, established from the pinned action's source

Both workflows pin `anthropics/claude-code-action` at `833fb0f8c9f6686b33d963a8bae0a94f4936ab2a` (v1.0.211). The following was read from that commit, not inferred.

**This section was re-read after a version bump.** The research run behind this record read v1.0.99 at `c3d45e8e941e1b2ad7b278c57482d9c5bf1f35b3`. Dependabot then bumped both workflows to v1.0.211 and the owner merged that as pull request #5, so the evidence was fetched again at the new commit and every row below reflects it. One finding changed materially and is marked. The run record at `docs/runs/2026-09-03-decision-12/` keeps the v1.0.99 reading as the historical record of what that run saw.

| Question | Finding | Source |
|---|---|---|
| What does `claude-review.yml` send first? | Agent mode. Nothing is prefetched from GitHub. The first call carries the literal prompt from the workflow file, the system prompt preset, and the tool schemas. Unchanged at v1.0.211 | `src/modes/agent/index.ts` 86 to 90, which writes `context.inputs.prompt` and nothing else |
| What does `claude.yml` send first? | Tag mode. `fetchGitHubData` runs before the first call and prefetches the pull request or issue title, body, all labels, up to 100 comment bodies, up to 100 reviews with up to 100 inline comments each, the changed file list as metadata, and the triggering comment verbatim | `src/modes/tag/index.ts` 53; `src/github/api/queries/github.ts` 26, 55, 70, 87 |
| **Changed at v1.0.211:** does the prefetch carry patch text? | **Yes, now it does.** At v1.0.99 the prefetch carried no file bytes and no patch text. v1.0.211 adds `diffHunk` to every prefetched review comment and renders it into the prompt as a fenced `diff` block, so up to 10,000 diff hunks of this repository's own code can leave before the model asks for anything. `claude-review.yml` is unaffected, because agent mode prefetches nothing | `src/github/api/queries/github.ts` 90 (`diffHunk`, absent at v1.0.99); `src/github/data/formatter.ts` 125 to 127 |
| How does file content reach the provider? | As tool results, pulled by the model, never pushed by the action. Each result then rides in the next call | `docs/configuration.md` 223 to 244 |
| How much content is that in practice? | `claude-review.yml` names `CLAUDE.md`, `ROADMAP.md`, and the spec in its prompt, so those are near certain to be read in full: about 96 KB of governed prose per run, before the diff | `wc -c` on the three files: 7,120, 65,363, 23,616 bytes |
| How wide is the reachable set? | `claude-review.yml` checks out with `fetch-depth: 0`, so `git log` and `git diff` can reach any commit and any historical file version. `claude.yml` uses `fetch-depth: 1` | `.github/workflows/claude-review.yml` line 25; `.github/workflows/claude.yml` line 40 |

**Classification.** Under spec section 5 this content is `INTERNAL`: the repository's own design documents, roadmap, policies, and CI configuration. It is not `PUBLIC`, and it is not `PROJECT_CONFIDENTIAL`, because no bid drawing, specification, quote, or customer datum exists in the repository yet.

That classification has an expiry. What leaves is decided by what sits in the checkout, not by which workflow runs, so the first `PROJECT_CONFIDENTIAL` file committed to this repository silently changes the class of everything these workflows submit. Section 3 of `docs/policies/egress.md` states what must happen on that day, and section 6 of this record asks the owner to confirm it.

## 3. The two credentials are two different bargains

The workflows accept either a Claude subscription OAuth token or a Console API key. This is not an implementation detail. The two run under different contracts.

| | `CLAUDE_CODE_OAUTH_TOKEN` (Pro or Max subscription) | `ANTHROPIC_API_KEY` (Console, commercial) |
|---|---|---|
| Governing terms | Consumer Terms of Service, effective 2025-10-08 | Commercial Terms of Service, effective 2025-06-17 |
| Model training | Opt out, not excluded: "We may use Materials to provide, maintain, and improve the Services and to develop other products and services, including training our models, unless you opt out of training through your account settings" | Excluded by contract: "Anthropic may not train models on Customer Content from Services" |
| Training after opting out | Still occurs for flagged content: Materials are used "when ... your Materials are flagged for safety review" | Not applicable |
| Retention | 30 days when the model improvement setting is off; a 5-year retention period when it is on; up to 2 years separately for content the automated trust and safety systems flag | "we automatically delete inputs and outputs on our backend within 30 days of receipt or generation" |
| Confidentiality | The Consumer Terms page does not contain the word "confidential" | "Customer Content is Customer's Confidential Information", with a need-to-know limit |
| Data processing agreement | None applies to a subscription | Commercial Terms section C incorporates the DPA, whose sections C.1 and C.3 give sub-processor authorization, notice, and a fifteen day objection window |
| Zero data retention | Unavailable. ZDR covers "eligible Anthropic APIs, Anthropic products that use your Commercial organization API key ... and Claude Code for Enterprise plans" | Available on request, subject to Anthropic's approval |

Sources, each fetched and returning HTTP 200 on 2026-09-03: `anthropic.com/legal/consumer-terms`, `anthropic.com/legal/commercial-terms`, `anthropic.com/legal/privacy`, `anthropic.com/legal/data-processing-addendum`, `docs.claude.com/en/docs/claude-code/data-usage`, `docs.claude.com/en/docs/build-with-claude/zero-data-retention`, `docs.claude.com/en/docs/build-with-claude/data-residency`, and the retention and training articles on `privacy.anthropic.com`. The action's own `docs/setup.md` at the pinned commit confirms the OAuth token is the subscription credential.

**Three things could not be verified and are not asserted here.** The factory default of the model improvement toggle for a new account, which no published page states, so the owner must read it in the account that would issue the token. Whether this owner would qualify for zero data retention, since Anthropic publishes no eligibility criteria beyond "subject to Anthropic's approval". And the names on the sub-processor list, because `trust.anthropic.com/subprocessors` renders in JavaScript and returned an empty shell to every retrieval method tried; that a list exists and is contractually incorporated through DPA Schedule 4 is verified, its contents are not.

## 4. Recommendation

**Use `ANTHROPIC_API_KEY` under the Commercial Terms. Do not add `CLAUDE_CODE_OAUTH_TOKEN`.** This reverses the instruction the session gave the owner on 2026-09-02, which named the OAuth token; that instruction predated this research and was wrong for this repository.

The change is one line in each workflow, from `claude_code_oauth_token` to `anthropic_api_key`, which `docs/policies/github-automation.md` already describes.

Four supports are published terms, quoted in section 3: training prohibited rather than toggled, content treated as confidential, a 30 day retention floor that does not depend on a setting, and a processor agreement with sub-processor rights. The retention difference is the weakest of the four, because a consumer account with model improvement off is also documented at 30 days; the argument rests on that being a toggle rather than a term.

One support is judgement, and is labelled as such. On the OAuth path this repository's egress posture becomes a property of one person's personal account setting, changeable at any time, invisible from inside the repository, and impossible to cite in a run record. A repository whose stated purpose is that cited evidence determines truth should not rest a data class decision on an unobservable toggle. The API key makes the same question an owner-controlled organization property. That is an argument about this repository's governance, not a claim about Anthropic.

Neither credential is sufficient for `PROJECT_CONFIDENTIAL` content. The choice here does not decide that question and must not be read as deciding it.

## 5. The mechanism, so the record is real

Spec section 5's seven-field requirement is met by `tools/egress/record.py`, added with this decision and covered by nine tests.

It runs in two modes. `check` runs **before** the provider step and fails the job when `docs/policies/egress.md` or this decision record is missing from the checkout, or when the declared class is not one the caller may send. `write` runs after, with `if: always()`, and emits one JSON line carrying exactly the seven fields, to the job summary and to a workflow artifact.

Three properties are worth stating. The record is a gate, not a log: with no decision record in the tree, the provider step never runs. Secret safety is structural rather than filtered, because the script reads only an eight-name allowlist of `GITHUB_*` variables and never reads the token at all, and it refuses to serialise a record containing a credential-shaped value. And sources are named by hash, never by content, so no prompt text, model output, or comment body enters the ledger.

`tools/ci/checks.py` validates every committed ledger line on every pull request, so the ledger is read by a required check and not merely written.

Section 6 of `docs/policies/egress.md` names what the mechanism does not achieve, in five numbered gaps. The first is the most important and the owner should read it before signing: the ledger records what was **available** to the provider, not what it read, because the model chooses what to read and that choice is not observable from inside the run. Every line is an upper bound.

## 6. Decision

The owner fills this section in. Until then this record authorizes nothing, `docs/policies/egress.md` is not in force, and the provider secret must not be added.

**a. Is `docs/policies/egress.md` version 1 approved as the repository's egress policy?**

Decision: Yes. `docs/policies/egress.md` version 1 is approved as the repository's egress policy.

**b. Which credential do the two Claude workflows use?** Recommended: `ANTHROPIC_API_KEY` under the Commercial Terms.

Decision: `ANTHROPIC_API_KEY` under the Commercial Terms.

**c. Is Anthropic admitted as a provider for `INTERNAL` repository content submitted by an automated caller, and are `.github/workflows/claude-review.yml` and `.github/workflows/claude.yml` the only automated callers admitted?**

Decision: Yes. Anthropic is admitted for `INTERNAL` via automated callers; only `.github/workflows/claude-review.yml` and `.github/workflows/claude.yml` are admitted.

**d. Is the upper-bound ledger of section 5 accepted as satisfying spec section 5's record requirement, with gaps 1 to 5 of the policy's section 6 accepted as known? Gap 6 belongs to determination (f).**

Decision: Yes. Upper-bound ledger accepted; gaps 1 to 5 known.

**e. On the day `PROJECT_CONFIDENTIAL` content first enters this repository, the two Claude workflows are disabled or path-restricted before that content is committed, and their continued use needs a new decision.** Confirm:

Decision: Confirmed. Disable or path-restrict both Claude workflows before the first `PROJECT_CONFIDENTIAL` commit; continued use needs a new decision.

**f. Interactive Claude Code sessions run by the owner are a separate case from (c): a person is present, no workflow submits, and `tools/egress/record.py` does not run. Are they covered by `docs/policies/egress.md` on the terms in its section 3, `INTERNAL` and `PUBLIC` only, never `PROJECT_CONFIDENTIAL` in a web session, with the session's run record under `docs/runs/` as the record and gap 6 of the policy's section 6 accepted as known?** Recommended: yes, because this draft was written in such a session and a policy that pretended otherwise would be false on its first line.

Decision: Yes, on those terms.

**Decided by:** Bekim Bukolla **Date:** 2026-09-06

## 7. Reproduce

```bash
# What the action sends, from the pinned commit
for f in docs/setup.md docs/configuration.md src/modes/detector.ts src/modes/agent/index.ts \
         src/modes/tag/index.ts src/github/data/fetcher.ts src/github/data/formatter.ts \
         src/github/api/queries/github.ts src/create-prompt/index.ts; do
  curl -s "https://raw.githubusercontent.com/anthropics/claude-code-action/833fb0f8c9f6686b33d963a8bae0a94f4936ab2a/$f"
done

# The two contracts
curl -s https://www.anthropic.com/legal/consumer-terms
curl -s https://www.anthropic.com/legal/commercial-terms
curl -s https://docs.claude.com/en/docs/claude-code/data-usage

# Volume of governed prose named in the review prompt
wc -c CLAUDE.md ROADMAP.md docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md

# The mechanism
python3 -m unittest discover -s tests -p 'test_*.py' -v
# The gate. Exits 1 today, because no signed decision record exists; exits 0 once one does.
python3 tools/egress/record.py check --provider anthropic --purpose 'demonstration' \
  --data-class INTERNAL --decision docs/decisions/2026-09-03-egress-policy.md --rule egress-3-claude-review
```

The full research run, its three agents, their sources, and the eighteen items they marked unverifiable are recorded at `docs/runs/2026-09-03-decision-12/`.
