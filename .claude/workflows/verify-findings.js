// Heleos-spark repository-owned workflow: verify-findings
// Run by the Claude Code Workflow tool by name ({ name: "verify-findings", args: {...} }).
// Version 1.0.0. Provenance: generalized from the three verification runs that produced roadmap
// revision 2 (docs/runs/2026-09-02-roadmap-revision-2/README.md). Read-only.
//
// Adversarial verification: every blocker or major finding gets two independent skeptics with
// different lenses (fact check; materiality and recommendation); every minor finding gets one.
// A finding survives only if no skeptic refutes it; disagreement is reported as "contested".
//
// args = {
//   findings_file: "<absolute path to a JSON array of findings; each has uid, claim, location, evidence, recommendation, severity, confidence>",
//   items: [{ uid: "audit/id", sev: "blocker|major|minor" }, ...],   // the subset to verify
//   context: "one paragraph the skeptics need (branch, exports, dates)",
//   chunk_major: 10, chunk_minor: 12,
//   require_ceiling: false, effort_major: "high", effort_minor: "medium"
// }
export const meta = {
  name: 'verify-findings',
  description: 'Independent skeptics try to refute each audit finding from primary sources and assess its materiality; survivors are reported with corrections',
  phases: [
    { title: 'Refute', detail: 'fact-check lens' },
    { title: 'Assess', detail: 'materiality and recommendation lens' },
  ],
}

const A = args || {}
if (!A.findings_file || !Array.isArray(A.items) || !A.items.length) return { status: 'ABORTED_BAD_ARGS', reason: 'findings_file and items[] are required' }
if (A.require_ceiling && budget.total === null) return { status: 'ABORTED_NO_CEILING' }
if (budget.total === null) log('no token ceiling was set for this run; the policy ceiling is advisory only')

const bm = A.items.filter(i => i.sev !== 'minor')
const mn = A.items.filter(i => i.sev === 'minor')
function chunk(arr, n) { const out = []; for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n)); return out }
const chunkMajor = A.chunk_major ?? 10
const chunkMinor = A.chunk_minor ?? 12
if (!Number.isInteger(chunkMajor) || chunkMajor <= 0 || !Number.isInteger(chunkMinor) || chunkMinor <= 0) return { status: 'ABORTED_BAD_ARGS', reason: 'chunk_major and chunk_minor must be positive integers' }
const bmChunks = chunk(bm, chunkMajor)
const mnChunks = chunk(mn, chunkMinor)

const SCHEMA = {
  type: 'object',
  properties: {
    verdicts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          uid: { type: 'string' },
          verdict: { type: 'string', enum: ['confirmed', 'corrected', 'refuted'] },
          confidence: { type: 'number' },
          checked: { type: 'string', description: 'what you actually opened or ran' },
          correction: { type: 'string', description: 'accurate statement when corrected or refuted; empty when confirmed' },
          materiality: { type: 'string', enum: ['structural', 'wording', 'none'] },
          resolution: { type: 'string' },
          duplicates_of: { type: 'array', items: { type: 'string' } },
        },
        required: ['uid', 'verdict', 'confidence', 'checked', 'correction', 'materiality', 'resolution', 'duplicates_of'],
      },
    },
  },
  required: ['verdicts'],
}

const COMMON = `You are an independent skeptic verifying audit findings about the Heleos-spark repository. The findings were produced by other agents; your job is to try to REFUTE them from primary sources. Assume nothing they say is true until you have seen it yourself. Do not modify, check out, or commit anything; read-only commands only. Never attempt to locate or inspect the quarantined predecessor repository. ${A.context || ''}

The findings are in ${A.findings_file}, a JSON array of objects with uid, claim, location, evidence, recommendation, severity, confidence. Load ONLY your assigned uids. For each, open the cited files at the cited lines and their context, re-run any cited command that is cheap, and decide.

Verdict rules: confirmed = true as stated and supported by the cited evidence; corrected = substantially true but a detail is wrong, overstated, or missing (put the accurate statement in correction); refuted = false, or the evidence could not be found at the cited location or anywhere else after a reasonable search (say what you looked at). Do not soften a refutation to be polite and do not confirm out of laziness. materiality: structural = should change a phase, gate, decision, or plan; wording = a sentence, number, or table cell; none = not worth carrying. resolution = the one-line change the target document should make. duplicates_of = assigned uids that say the same thing. Return only the structured object.`

const refutePrompt = (c) => `${COMMON}

LENS: FACT CHECK. Try to refute the factual claim of each finding by opening the cited sources. Where a finding cites a command, run it read-only and compare numbers exactly. Where a finding says something is absent, grep the whole document set for the concept under several plausible wordings before agreeing. Where a finding interprets code behaviour, read the code path yourself.

Your assigned findings (uids): ${JSON.stringify(c.map(i => i.uid))}`

const assessPrompt = (c) => `${COMMON}

LENS: MATERIALITY AND RECOMMENDATION. For each finding, check the cited evidence briefly, then judge: (1) is the recommendation correct under the spec (a recommendation that contradicts the spec or invents a requirement the spec does not contain is refuted, with the correct recommendation in correction); (2) is it proportionate and within the target document's remit; (3) is the severity right; (4) does it duplicate another assigned finding. Mark refuted when immaterial, out of scope, duplicative, or wrong; corrected when the recommendation or severity needs adjusting; confirmed otherwise.

Your assigned findings (uids): ${JSON.stringify(c.map(i => i.uid))}`

const tasks = []
bmChunks.forEach((c, i) => {
  tasks.push(() => agent(refutePrompt(c), { label: `bm${i}-refute`, phase: 'Refute', schema: SCHEMA, effort: A.effort_major || 'high' }).then(r => r && ({ lens: 'refute', chunk: `bm${i}`, ...r })))
  tasks.push(() => agent(assessPrompt(c), { label: `bm${i}-assess`, phase: 'Assess', schema: SCHEMA, effort: A.effort_major || 'high' }).then(r => r && ({ lens: 'assess', chunk: `bm${i}`, ...r })))
})
mnChunks.forEach((c, i) => {
  tasks.push(() => agent(refutePrompt(c), { label: `mn${i}-refute`, phase: 'Refute', schema: SCHEMA, effort: A.effort_minor || 'medium' }).then(r => r && ({ lens: 'refute', chunk: `mn${i}`, ...r })))
})
log(`${bm.length} blocker/major findings in ${bmChunks.length} chunks (two lenses each); ${mn.length} minor in ${mnChunks.length} chunks (one lens); ${tasks.length} agents`)

const results = (await parallel(tasks)).filter(Boolean)
const byUid = {}
for (const r of results) for (const v of r.verdicts) { (byUid[v.uid] = byUid[v.uid] || []).push({ lens: r.lens, ...v }) }
const table = Object.entries(byUid).map(([uid, vs]) => {
  const verdicts = vs.map(v => v.verdict)
  let status
  if (verdicts.includes('refuted')) status = (verdicts.includes('confirmed') || verdicts.includes('corrected')) ? 'contested' : 'refuted'
  else if (verdicts.includes('corrected')) status = 'corrected'
  else status = 'confirmed'
  const materiality = vs.some(v => v.materiality === 'structural') ? 'structural' : (vs.some(v => v.materiality === 'wording') ? 'wording' : 'none')
  return { uid, status, materiality, verdicts: vs }
})
const counts = {}
for (const t of table) counts[t.status] = (counts[t.status] || 0) + 1
const missing = A.items.filter(i => !byUid[i.uid]).map(i => i.uid)
log(`${JSON.stringify(counts)}; ${results.length}/${tasks.length} agents returned; ${missing.length} findings without any verdict`)
return { status: missing.length || results.length < tasks.length ? 'PARTIAL' : 'COMPLETE', counts, table, missing, spent: budget.spent() }
