export const meta = {
  name: 'verify-findings-a',
  description: 'Adversarially verify group A audit findings (evidence, Option A, Option B) with independent skeptics',
  phases: [
    { title: 'Refute', detail: 'skeptics try to refute each finding from primary sources' },
    { title: 'Assess', detail: 'second lens: materiality, recommendation, duplicates' },
  ],
}

const S = '<scratch>'
const group = args.group
const items = args.items
const bm = items.filter(i => i.sev !== 'minor')
const mn = items.filter(i => i.sev === 'minor')
function chunk(arr, n) { const out = []; for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n)); return out }
const bmChunks = chunk(bm, 10)
const mnChunks = chunk(mn, 12)

const COMMON = `You are an independent skeptic verifying audit findings about the Heleos-spark repository (a clean-room, evidence-first HVAC / Division 23 takeoff system in its foundation-design phase). The findings were produced by other agents; your job is to try to REFUTE them from primary sources. Assume nothing they say is true until you have seen it yourself.

Repository working tree: <repo> on branch claude/dynamic-workflow-roadmap-156gdn (identical to origin/main). DO NOT modify files, check out, switch branches, or commit there; read-only git commands only. All origin branches are fetched as refs/remotes/origin/*.
Writable scratchpad: ${S}/work/<your-label>/ (create it). Branch exports (full trees) are at ${S}/branches/<name>:
- main     = origin/main d85bd86 (spec, README, ECC-generated tooling bundle)
- pr3      = origin/claude/heleos-spark-branch-60gd5e 861fcfe (PR #3: ROADMAP.md, CLAUDE.md, docs/roadmap/*, docs/workspace/*, lane guard hooks, tests/hooks, mirrored plan and custody record)
- pr1      = origin/feat/enriched-build-fabric 1a804ed (PR #1: 294-file implementation; src/helios_takeoff_core, tools/helios_build, build_control, tests, docs)
- recon    = origin/docs/recovery-reconciliation-2026-08-27 7d7c97c (the 2026-08-27 reconciliation plan, "Option A")
- recovery = origin/recovery/misplaced-chat-p0-p1a-2026-08-27 2c9de80 (custody record and git bundle of the recovered repository)
The spec (architecture authority): ${S}/branches/main/docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md. The roadmap draft under review: ${S}/branches/pr3/ROADMAP.md and its companions.
Python 3.12.11 venv: ${S}/venv312/bin/python (jsonschema, pip, setuptools, wheel already installed there); uv at /root/.local/bin/uv. Today is 2026-09-02. No network is needed. Never attempt to locate or inspect the quarantined predecessor repository (HELEO_HELIOS_2.0).

The findings are in ${S}/findings/wf1-findings.json, a JSON array of objects with fields uid, audit, id, category, severity, claim, location, evidence, recommendation, confidence. Load ONLY your assigned uids (for example with python3 -c or jq). Then, for each one, open the cited files at the cited lines (and the surrounding context), re-run any cited command that is cheap, and decide.

Verdict rules: 'confirmed' = the claim is true as stated and the cited evidence supports it; 'corrected' = the claim is substantially true but a detail is wrong, overstated, or missing (put the accurate statement in correction); 'refuted' = the claim is false, or you could not find the evidence at the cited location and could not find it anywhere else after a reasonable search (say what you looked at). Do not soften a refutation to be polite, and do not confirm out of laziness: both errors cost the roadmap. confidence is 0 to 1. materiality: 'structural' = should change a roadmap phase, gate, decision, or plan of record; 'wording' = should change a sentence, number, or table cell; 'none' = not worth carrying into the roadmap. resolution = the one-line change the roadmap (or evidence page, or plan) should make. duplicates_of = uids in your assignment that say essentially the same thing (empty array if none). Return only the structured output.`

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

const refutePrompt = (c) => `\n\nLENS: FACT CHECK. Try to refute the factual claim of each finding by opening the cited sources. Where a finding cites a command, run it (read-only; in the scratch exports, never in the working tree) and compare numbers exactly. Where a finding says something is 'absent' from a document, grep the whole document set for the concept under several plausible wordings before agreeing. Where a finding interprets code behaviour, read the code path yourself.\n\nYour assigned findings (uids): ${JSON.stringify(c.map(i => i.uid))}`

const assessPrompt = (c) => `\n\nLENS: MATERIALITY AND RECOMMENDATION. For each finding, first check the cited evidence briefly (open the cited lines), then judge: (1) is the recommendation correct under the spec (the spec is the authority; a recommendation that contradicts the spec, or that invents a requirement the spec does not contain, is 'refuted' with the correct recommendation in correction); (2) is it proportionate and in the roadmap's remit (a roadmap schedules and gates work; it does not write code); (3) is the severity right (say in correction if it should move up or down and why); (4) does it duplicate another assigned finding. Mark 'refuted' when the finding is immaterial, out of scope, duplicative, or its recommendation is wrong; 'corrected' when the recommendation or severity needs adjusting; 'confirmed' otherwise.\n\nYour assigned findings (uids): ${JSON.stringify(c.map(i => i.uid))}`

const tasks = []
bmChunks.forEach((c, i) => {
  tasks.push(() => agent(COMMON + refutePrompt(c), { label: `${group}-bm${i}-refute`, phase: 'Refute', schema: SCHEMA, effort: 'high' }).then(r => r && ({ lens: 'refute', chunk: `bm${i}`, ...r })))
  tasks.push(() => agent(COMMON + assessPrompt(c), { label: `${group}-bm${i}-assess`, phase: 'Assess', schema: SCHEMA, effort: 'high' }).then(r => r && ({ lens: 'assess', chunk: `bm${i}`, ...r })))
})
mnChunks.forEach((c, i) => {
  tasks.push(() => agent(COMMON + refutePrompt(c), { label: `${group}-mn${i}-refute`, phase: 'Refute', schema: SCHEMA, effort: 'medium' }).then(r => r && ({ lens: 'refute', chunk: `mn${i}`, ...r })))
})
log(`group ${group}: ${bm.length} blocker/major in ${bmChunks.length} chunks (2 lenses each), ${mn.length} minor in ${mnChunks.length} chunks (1 lens); ${tasks.length} agents`)

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
const missing = items.filter(i => !byUid[i.uid]).map(i => i.uid)
log(`group ${group}: ${JSON.stringify(counts)}; ${results.length}/${tasks.length} agents returned; ${missing.length} findings without any verdict`)
return { group, counts, table, missing }