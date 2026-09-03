export const meta = {
  name: 'publication-recheck-revision-2',
  description: 'Re-check the rows the completeness critics graded below covered after the fixes were applied, and run one publication critic over the assembled pages',
  phases: [{ title: 'Recheck', detail: 'independent re-check of fixed coverage rows and one publication critic' }],
}

const S = '<scratch>'
const REPO = '<repo>'

const RECHECK_SCHEMA = {
  type: 'object',
  properties: {
    rows: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          status: { type: 'string', enum: ['covered', 'weak', 'missing'] },
          evidence: { type: 'string', description: 'file and section plus a short quote of the sentence that now binds the requirement; or what is still absent' },
          remaining_fix: { type: 'string', description: 'when not covered: the exact sentence to add and where; empty otherwise' },
        },
        required: ['id', 'status', 'evidence', 'remaining_fix'],
      },
    },
    counts: { type: 'object', properties: { covered: { type: 'number' }, weak: { type: 'number' }, missing: { type: 'number' } }, required: ['covered', 'weak', 'missing'] },
  },
  required: ['rows', 'counts'],
}

const CRITIC_SCHEMA = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          file: { type: 'string' },
          location: { type: 'string', description: 'section heading or table row, plus the line number' },
          severity: { type: 'string', enum: ['blocker', 'major', 'minor'] },
          category: { type: 'string', enum: ['dangling-reference', 'internal-contradiction', 'spec-contradiction', 'unverified-claim', 'misleading-to-owner', 'prose-rule'] },
          claim: { type: 'string' },
          evidence: { type: 'string', description: 'the quoted text and the command or second location that shows the problem' },
          fix: { type: 'string', description: 'the exact replacement text' },
        },
        required: ['file', 'location', 'severity', 'category', 'claim', 'evidence', 'fix'],
      },
    },
    checked: { type: 'array', items: { type: 'string' }, description: 'the cross-reference families that were checked and found consistent' },
    summary: { type: 'string' },
  },
  required: ['findings', 'checked', 'summary'],
}

const recheckPrompt = (keys, label) => `You are an independent re-checker for revision 2 of the Heleos-spark build roadmap. Read-only: do not modify any file, do not run git commands that change state, never attempt to locate or inspect the quarantined predecessor repository.

Earlier completeness critics graded some spec requirements as weak, missing, or misplaced in the drafts, and proposed fixes. The author then applied fixes. Your job: for each row assigned to you, decide from the published text alone whether the requirement is now covered (a phase, exit criterion, decision, test, record, register row, or worker rule names it in a way that can be checked at a gate), weak (mentioned, but nothing binds it at a gate), or missing (absent). Do not trust the author's claim that a fix was applied; find the sentence.

Inputs:
- Your rows: ${S}/findings/recheck-input.json, a JSON object keyed by audit key; use the keys ${JSON.stringify(keys)}. Each row has id, requirement, draft_status, fix_applied (the fix the earlier critic proposed; the author may have worded it differently).
- The spec: ${REPO}/docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md (for the requirement's meaning).
- The published pages: ${REPO}/ROADMAP.md (read in full), ${REPO}/CLAUDE.md, ${REPO}/README.md, ${REPO}/docs/roadmap/phase-0-evidence.md, ${REPO}/docs/roadmap/plan-of-record-audit.md, ${REPO}/docs/roadmap/dynamic-workflow-operating-model.md, ${REPO}/docs/roadmap/skills-and-plugins.md, ${REPO}/docs/roadmap/decision-drafts.md. Grep them under several plausible wordings before calling anything missing; the roadmap often binds several requirements in one long exit sentence.

Output one row per input row (all of them), with a quote of the binding sentence and its file and section, or the exact sentence still needed. Counts must match the rows. Label: ${label}.`

const criticPrompt = `You are the publication critic for revision 2 of the Heleos-spark build roadmap, the last reader before the pages are committed and shown to the repository owner. Read-only: do not modify any file, do not run git commands that change state, never attempt to locate or inspect the quarantined predecessor repository.

Pages under review, all in ${REPO}: ROADMAP.md, CLAUDE.md, README.md, docs/roadmap/phase-0-evidence.md, docs/roadmap/plan-of-record-audit.md, docs/roadmap/dynamic-workflow-operating-model.md, docs/roadmap/skills-and-plugins.md, docs/roadmap/decision-drafts.md, docs/decisions/README.md, docs/runs/2026-09-02-roadmap-revision-2/README.md, .claude/workflows/README.md, .claude/workflows/REGISTRY.json. The architecture authority is docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md; read its sections 2, 5, 10, 11, 13, 14, and 16. Reference exports of the other branches are under ${S}/branches/{main,pr3,pr1,recon,recovery} if a fact about them needs checking.

Check, in this order, and report only real problems:
1. Dangling references: every "Decision N" (1 to 14) means the same thing in ROADMAP.md section 12, docs/roadmap/decision-drafts.md, CLAUDE.md, and the operating model; every task id P0.1 to P0.10, repair id A1 to A27, gap id B1 to B13, and Option B gate item cited anywhere exists in the page that defines it and says what the citation implies; every workflow, skill, or loop name cited exists in .claude/workflows/REGISTRY.json, in docs/roadmap/skills-and-plugins.md, or is explicitly scheduled to be built in a named phase; every relative link and every "section N of the spec" citation points at a real target.
2. Internal contradictions: two pages giving different numbers, dates, statuses, commit counts, or owners for the same fact (for example the PR #3 commit count, the PR #1 file counts, the test re-run result, the run counts and token totals in the run record versus the operating model or ROADMAP section 11).
3. Spec contradictions: any sentence that would let a worker do something the spec forbids (section 2 clean room, section 5 data classes, section 10 prohibitions, section 13 controls, the section 14 closing rule and item order, section 16 approval), or that schedules production implementation before the owner's approval.
4. Unverified claims: any fact presented as verified without a command, run id, or file reference; any claim about tooling that is machine-local.
5. Misleading to the owner: wording that overstates what was done (for example "verified" for something only proposed, "complete" for a partial run), or that hides a limitation the run record admits.
6. Prose rule: em or en dashes in the pages listed (the spec and the generated ECC skill files are out of scope).

Return at most 25 findings, most severe first, each with the exact replacement text. Do not report style preferences, length, or repetition. List in "checked" the reference families you verified as consistent so the author knows what was covered.`

phase('Recheck')
const [r26, r716, critic] = await parallel([
  () => agent(recheckPrompt(['spec-coverage-2-6'], 'recheck-2-6'), { label: 'recheck-2-6', phase: 'Recheck', schema: RECHECK_SCHEMA, effort: 'high' }),
  () => agent(recheckPrompt(['spec-coverage-7-11', 'spec-coverage-12-16'], 'recheck-7-16'), { label: 'recheck-7-16', phase: 'Recheck', schema: RECHECK_SCHEMA, effort: 'high' }),
  () => agent(criticPrompt, { label: 'publication-critic', phase: 'Recheck', schema: CRITIC_SCHEMA, effort: 'high' }),
])
const rechecks = [r26, r716].filter(Boolean)
const counts = { covered: 0, weak: 0, missing: 0 }
for (const x of rechecks) for (const k of Object.keys(counts)) counts[k] += (x.counts[k] || 0)
const dropped = [['recheck-2-6', r26], ['recheck-7-16', r716], ['publication-critic', critic]].filter(([, v]) => !v).map(([n]) => n)
log(`recheck counts ${JSON.stringify(counts)}; critic findings ${critic ? critic.findings.length : 'none (dropped)'}; dropped: ${dropped.join(', ') || 'none'}`)
return { status: dropped.length ? 'PARTIAL' : 'COMPLETE', counts, rows: rechecks.flatMap(x => x.rows), critic, dropped }