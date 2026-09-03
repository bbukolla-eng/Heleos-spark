export const meta = {
  name: 'decision-12-egress',
  description: 'Establish what the Claude workflows transmit, how the provider handles it, and how to record each submission, so Decision 12 can be recorded on evidence',
  phases: [{ title: 'Establish', detail: 'transmission surface, provider terms, ledger design' }],
}

const REPO = '<repo>'
const PIN = 'c3d45e8e941e1b2ad7b278c57482d9c5bf1f35b3'

const COMMON = `You are establishing facts for a governance decision in the Heleos-spark repository at ${REPO}, on branch claude/dynamic-workflow-roadmap-156gdn at commit 0400963.

Context. The repository is private, clean-room, and evidence-first. Its foundation design spec is at ${REPO}/docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md; read its section 5 (data classification and external egress) before you start. Section 5 defines four data classes and requires that EVERY external submission record seven fields: provider, purpose, data class, source hashes, policy decision, time, and result reference. ${REPO}/CLAUDE.md forbids sending INTERNAL or PROJECT_CONFIDENTIAL material to any external service until a policy permits it.

The repository just merged two GitHub Actions workflows that call anthropics/claude-code-action pinned at commit ${PIN} (tag v1.0.99): ${REPO}/.github/workflows/claude-review.yml and ${REPO}/.github/workflows/claude.yml. Both are inert today because the CLAUDE_CODE_OAUTH_TOKEN secret does not exist. The owner wants to add that secret. Doing so starts sending repository content to Anthropic, which is what the pending Decision 12 governs. ${REPO}/docs/policies/github-automation.md section "Egress, and why the secret waits" states the current position.

Rules for you:
- READ ONLY on the repository. Do not modify, create, or delete any file in ${REPO}. Do not run git commands that change state. Do not commit, push, or comment. Never attempt to locate or inspect the quarantined predecessor repository the spec names.
- Every factual claim you return needs its source: a file and line, a command you ran and its output, or a URL you actually fetched. If you cannot reach a source, say exactly that in the field and lower your confidence rather than filling the gap from memory. An unverifiable claim recorded as fact is the specific failure this repository exists to prevent.
- Prose style for anything that may be quoted into a repository document: plain sentences, no em dashes and no en dashes, tables for parallel facts.`

const SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          question: { type: 'string', description: 'the question this finding answers' },
          answer: { type: 'string' },
          confidence: { type: 'number' },
          sources: { type: 'array', items: { type: 'string' }, description: 'file and line, command and output, or URL fetched; one entry per source' },
          unverifiable: { type: 'string', description: 'what you could not establish and why; empty if nothing' },
        },
        required: ['question', 'answer', 'confidence', 'sources', 'unverifiable'],
      },
    },
    recommendation: { type: 'string', description: 'what you would put in the decision record on your area, in the repository prose style' },
    notes: { type: 'string' },
  },
  required: ['findings', 'recommendation', 'notes'],
}

const AGENTS = [
  {
    key: 'surface',
    label: 'transmission-surface',
    prompt: `${COMMON}

Your area: exactly what leaves this repository when those two workflows run, and to where.

Read the pinned action's own source and documentation. The session's GitHub API access may not cover that repository, but raw.githubusercontent.com worked in an earlier run: fetch from https://raw.githubusercontent.com/anthropics/claude-code-action/${PIN}/<path>, starting with action.yml, README.md, docs/security.md, docs/usage.md, docs/configuration.md, and the entrypoint and mode sources under src/ that the earlier run found useful (src/entrypoints/run.ts, src/modes/tag/index.ts, src/modes/agent/index.ts, src/mcp/install-mcp-server.ts).

Answer, each as its own finding:
1. For claude-review.yml (automation mode, prompt is set, fetch-depth 0): what repository content is placed in the model context? Name it concretely: the diff, specific files, pull request metadata, comment bodies, anything else. Does the action send whole files or only what the model requests through tools?
2. For claude.yml (tag mode, no prompt, triggered by an @claude mention): same question.
3. What network destinations does the action contact, and which of them receive repository content rather than only GitHub API traffic?
4. Does either workflow, as configured in this repository, transmit anything that spec section 5 would class as SECRET? Consider the token itself, GITHUB_TOKEN, and anything in the environment. Check whether the action redacts secrets from what it sends.
5. Is the volume bounded, and by what? Does the run make a fixed number of provider calls or an open-ended agentic loop?

Then classify: given spec section 5's four classes and the actual content of this repository (design documents, roadmap, CI configuration, no bid drawings and no customer data yet), what data class does the transmitted content fall in? Justify from the class definitions, and note explicitly whether that classification would change once Phase 3 or 4 brings PROJECT_CONFIDENTIAL drawings into the repository, since the decision must survive that.`,
  },
  {
    key: 'provider',
    label: 'provider-terms',
    prompt: `${COMMON}

Your area: how Anthropic handles the data these workflows would send, under each credential option, as of today (2026-09-03).

The two workflows accept either CLAUDE_CODE_OAUTH_TOKEN (a Claude subscription token produced by "claude setup-token") or, with a one-line change, ANTHROPIC_API_KEY (a Console API key). The owner must choose knowing the difference.

Fetch Anthropic's own published terms and documentation. Likely sources include the commercial terms of service, the consumer terms, the privacy policy, the usage policy, the Claude Code documentation and its data usage or privacy page, the trust or security portal, and any published data retention documentation. Use the live pages; do not answer from memory. Quote the sentence you rely on for each answer.

Answer, each as its own finding:
1. Under a Claude subscription (the OAuth token path), is customer content used to train Anthropic's generative models by default? What is the retention period for inputs and outputs? Is there a setting that changes it, and who controls it?
2. Under a Console API key, are the answers different? State both, plainly, so the owner can compare.
3. What is the zero data retention option, if one exists, who qualifies, and how is it requested?
4. Is there any published commitment about human review of submitted content, and under what circumstances?
5. Where does processing happen, and is there any published sub-processor list relevant to code content?

Then give your recommendation on which credential this repository should use for these two workflows, given that it is a private, clean-room, evidence-first repository that will later hold PROJECT_CONFIDENTIAL bid drawings. Be explicit about which parts of your recommendation rest on published terms you quoted and which rest on judgement. If a page you needed was unreachable, say which one and mark the affected answer unverified rather than guessing: the owner is going to act on this.`,
  },
  {
    key: 'ledger',
    label: 'submission-ledger',
    prompt: `${COMMON}

Your area: designing the mechanism that makes the seven-field submission record real, not merely promised.

Spec section 5 requires every external submission to record provider, purpose, data class, source hashes, policy decision, time, and result reference. Right now nothing in this repository produces such a record, and a GitHub Actions run that calls Anthropic would be an unrecorded external submission. That is the gap that makes adding the secret a policy violation today, so the decision needs a mechanism, not a promise.

Read: ${REPO}/.github/workflows/claude-review.yml, ${REPO}/.github/workflows/claude.yml, ${REPO}/.github/workflows/ci.yml, ${REPO}/tools/ci/checks.py and ${REPO}/tests/test_ci_checks.py for the house style, ${REPO}/docs/policies/github-automation.md, ${REPO}/ROADMAP.md sections 5 and 12 (tasks P0.9 and Decision 12), ${REPO}/docs/roadmap/dynamic-workflow-operating-model.md sections on run records and the egress ledger, and ${REPO}/docs/runs/2026-09-02-roadmap-revision-2/README.md for how run records look here.

Design and answer, each as its own finding:
1. Where should an egress record live so that it is durable and reviewable: committed under docs/runs/, a workflow artifact, a job summary, or something else? Weigh that a GitHub Actions run on a pull request cannot always push to the branch, and that branch protection is coming. Recommend one and say what it costs.
2. What exactly can be filled in automatically for each of the seven fields from inside a GitHub Actions run, and what cannot? Be concrete: for source hashes, what is hashed, given that the action decides at runtime which files it reads. If a field genuinely cannot be captured faithfully, say so, because an inaccurate ledger is worse than an admitted gap.
3. Write the mechanism: the smallest addition to the two workflows plus a Python helper in the house style (standard library only, snake_case, a test alongside it) that emits the record. Give the actual file content you propose, ready to be reviewed, and the workflow step that calls it. Keep it minimal and make sure it cannot leak the token or any secret into the record.
4. What should the recurring review look like, so the ledger is read rather than merely written? Tie it to something that already exists in this repository.
5. State any residual gap that the mechanism does not close, so the decision record can name it honestly.

Also draft the structure of ${REPO}/docs/policies/egress.md that task P0.9 calls for: the section headings and what each section must state, covering all four data classes, which sessions may see what, which workflows may call which providers, and the seven-field record. Do not write the whole policy, just its skeleton and the rules it must contain, since the owner's decision determines the substance.`,
  },
]

phase('Establish')
const results = (await parallel(AGENTS.map(a => () =>
  agent(a.prompt, { label: a.label, phase: 'Establish', schema: SCHEMA, effort: 'high' })
    .then(r => r && ({ area: a.key, ...r }))
))).filter(Boolean)

const dropped = AGENTS.map(a => a.key).filter(k => !results.some(r => r.area === k))
const all = results.flatMap(r => r.findings.map(f => ({ area: r.area, ...f })))
const unverified = all.filter(f => f.unverifiable && f.unverifiable.trim())
log(`${all.length} findings across ${results.length}/${AGENTS.length} areas; ${unverified.length} carry an unverifiable note; dropped: ${dropped.join(', ') || 'none'}`)
return { status: dropped.length ? 'PARTIAL' : 'COMPLETE', findings: all, recommendations: results.map(r => ({ area: r.area, recommendation: r.recommendation, notes: r.notes })), unverified_count: unverified.length, dropped }