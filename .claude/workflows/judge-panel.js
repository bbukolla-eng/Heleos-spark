// Heleos-spark repository-owned workflow: judge-panel
// Run by the Claude Code Workflow tool by name ({ name: "judge-panel", args: {...} }).
// Version 1.0.0. Provenance: generalized from the operating-model design run that produced roadmap
// revision 2 (docs/runs/2026-09-02-roadmap-revision-2/README.md). Read-only: designers write only
// their returned text; nothing is committed by this workflow.
//
// Independent designs from distinct angles, scored by independent judges with distinct lenses,
// then one synthesis from the winner with grafts from the others. Replaces a multi-persona
// council for decisions; the owner still decides.
//
// args = {
//   context: "what every agent must read first (paths, constraints, facts)",
//   deliverable: "what each designer must produce (requirements list)",
//   angles: [{ key: "spec-fidelity", angle: "..." }, ...],          // 2 to 5
//   lenses: [{ key: "judge-spec", lens: "..." }, ...],              // 2 to 5
//   synthesis: "instructions for the final document (title, sections, style)",
//   require_ceiling: false, effort: "high"
// }
export const meta = {
  name: 'judge-panel',
  description: 'Independent designs from distinct angles, scored by independent judges, synthesized from the winner plus grafts',
  phases: [
    { title: 'Design', detail: 'one designer per angle' },
    { title: 'Judge', detail: 'one judge per lens scores every design' },
    { title: 'Synthesize', detail: 'winner plus grafts' },
  ],
}

const A = args || {}
if (!A.context || !A.deliverable || !Array.isArray(A.angles) || A.angles.length < 2 || !Array.isArray(A.lenses) || A.lenses.length < 2 || !A.synthesis) {
  return { status: 'ABORTED_BAD_ARGS', reason: 'context, deliverable, angles[>=2], lenses[>=2], synthesis are required' }
}
if (A.require_ceiling && budget.total === null) return { status: 'ABORTED_NO_CEILING' }
if (budget.total === null) log('no token ceiling was set for this run; the policy ceiling is advisory only')
const EFFORT = A.effort || 'high'

const DESIGN_SCHEMA = {
  type: 'object',
  properties: {
    title: { type: 'string' },
    angle: { type: 'string' },
    summary: { type: 'string' },
    design_markdown: { type: 'string' },
    key_mechanisms: { type: 'array', items: { type: 'string' } },
    known_limitations: { type: 'array', items: { type: 'string' } },
  },
  required: ['title', 'angle', 'summary', 'design_markdown', 'key_mechanisms', 'known_limitations'],
}
const JUDGE_SCHEMA = {
  type: 'object',
  properties: {
    scores: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          design: { type: 'string' },
          spec_fidelity: { type: 'number' },
          executability: { type: 'number' },
          completeness: { type: 'number' },
          clarity: { type: 'number' },
          total: { type: 'number' },
          strengths: { type: 'array', items: { type: 'string' } },
          weaknesses: { type: 'array', items: { type: 'string' } },
          errors: { type: 'array', items: { type: 'string' } },
        },
        required: ['design', 'spec_fidelity', 'executability', 'completeness', 'clarity', 'total', 'strengths', 'weaknesses', 'errors'],
      },
    },
    winner: { type: 'string' },
    grafts: { type: 'array', items: { type: 'object', properties: { from: { type: 'string' }, idea: { type: 'string' }, why: { type: 'string' } }, required: ['from', 'idea', 'why'] } },
    missing_everywhere: { type: 'array', items: { type: 'string' } },
  },
  required: ['scores', 'winner', 'grafts', 'missing_everywhere'],
}
const SYNTH_SCHEMA = {
  type: 'object',
  properties: {
    markdown: { type: 'string' },
    adopted_grafts: { type: 'array', items: { type: 'string' } },
    resolved_missing: { type: 'array', items: { type: 'string' } },
    open_questions_for_owner: { type: 'array', items: { type: 'string' } },
  },
  required: ['markdown', 'adopted_grafts', 'resolved_missing', 'open_questions_for_owner'],
}

const READONLY = 'Do not modify, check out, or commit anything in the repository; read-only commands only. Never attempt to locate or inspect the quarantined predecessor repository.'

phase('Design')
const designs = (await parallel(A.angles.map(a => () =>
  agent(`${A.context}\n\n${READONLY}\n\nYour design angle: ${a.angle}\n\n${A.deliverable}\n\nOutput only the structured object; put the full design in design_markdown.`, { label: a.key, phase: 'Design', schema: DESIGN_SCHEMA, effort: EFFORT })
    .then(d => d && ({ key: a.key, ...d }))
))).filter(Boolean)
log(`${designs.length}/${A.angles.length} designs produced`)
if (designs.length < 2) return { status: 'FAILED', reason: 'fewer than two designs', designs }

phase('Judge')
const bundle = designs.map(d => `\n\n==================== DESIGN "${d.key}" (${d.title}; angle: ${d.angle}) ====================\n${d.design_markdown}\n\nKnown limitations stated by its author:\n- ${d.known_limitations.join('\n- ')}`).join('\n')
const judgments = (await parallel(A.lenses.map(j => () =>
  agent(`${A.context}\n\n${READONLY}\n\nYou are one of ${A.lenses.length} independent judges. ${j.lens}\n\nScore each design 0 to 10 on spec_fidelity, executability, completeness, clarity (total = sum, max 40). List strengths, weaknesses, and factual errors per design. Name a winner. List grafts: specific ideas from non-winning designs the final document should adopt. List what is missing from every design. Judge only what is written; do not reward length.\n${bundle}`, { label: j.key, phase: 'Judge', schema: JUDGE_SCHEMA, effort: EFFORT })
    .then(r => r && ({ key: j.key, ...r }))
))).filter(Boolean)
log(`${judgments.length}/${A.lenses.length} judgments returned`)
if (!judgments.length) return { status: 'FAILED', reason: 'no judgments', designs }

const totals = {}
for (const j of judgments) for (const s of j.scores) totals[s.design] = (totals[s.design] || 0) + s.total
const ranking = Object.entries(totals).sort((a, b) => b[1] - a[1])
log(`ranking: ${ranking.map(([k, v]) => `${k}=${v}`).join(', ')}`)
const winnerKey = ranking.length ? ranking[0][0] : designs[0].key
const winner = designs.find(d => d.key === winnerKey || d.title === winnerKey) || designs[0]

phase('Synthesize')
const judgeNotes = judgments.map(j => `--- ${j.key} ---\nwinner: ${j.winner}\n` + j.scores.map(s => `${s.design}: total ${s.total} (spec ${s.spec_fidelity}, exec ${s.executability}, complete ${s.completeness}, clarity ${s.clarity})\n  strengths: ${s.strengths.join(' | ')}\n  weaknesses: ${s.weaknesses.join(' | ')}\n  errors: ${s.errors.join(' | ')}`).join('\n') + `\ngrafts: ${j.grafts.map(g => `[from ${g.from}] ${g.idea} (${g.why})`).join(' || ')}\nmissing everywhere: ${j.missing_everywhere.join(' | ')}`).join('\n\n')
const synthesis = await agent(`${A.context}\n\n${READONLY}\n\nYou are the synthesizer. ${designs.length} designs were produced and judged. The winner by total score is "${winner.key}" (${winner.title}). Start from the winner, correct every factual error the judges found, adopt every graft that closes a gap, and close every "missing everywhere" item with a concrete mechanism or an explicit statement that it stays owner-only or needs work in a named phase. ${A.synthesis}\n\nOutput only the structured object.\n\n==================== JUDGMENTS ====================\n${judgeNotes}\n${bundle}`, { label: 'synthesize', phase: 'Synthesize', schema: SYNTH_SCHEMA, effort: EFFORT })

const dropped = []
if (designs.length < A.angles.length) dropped.push(`${A.angles.length - designs.length} design(s)`)
if (judgments.length < A.lenses.length) dropped.push(`${A.lenses.length - judgments.length} judgment(s)`)
if (!synthesis) dropped.push('synthesis')
if (dropped.length) log(`dropped: ${dropped.join(', ')}`)
return {
  status: dropped.length ? 'PARTIAL' : 'COMPLETE',
  winner: winner.key, ranking,
  designs: designs.map(d => ({ key: d.key, title: d.title, summary: d.summary, key_mechanisms: d.key_mechanisms, known_limitations: d.known_limitations })),
  judgments, synthesis, spent: budget.spent(),
}
