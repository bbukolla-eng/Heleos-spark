export const meta = {
  name: 'coverage-fill-revision-2',
  description: 'Map every spec requirement from the audit checklists onto roadmap revision 2 and its companion drafts; flag what is still missing',
  phases: [{ title: 'Map', detail: 'one mapper per spec section range' }],
}

const S = '<scratch>'
const RANGES = [
  { key: 'spec-coverage-2-6', label: '2-6', sections: '2 to 6' },
  { key: 'spec-coverage-7-11', label: '7-11', sections: '7 to 11' },
  { key: 'spec-coverage-12-16', label: '12-16', sections: '12 to 16' },
]

const SCHEMA = {
  type: 'object',
  properties: {
    rows: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          requirement: { type: 'string', description: 'short restatement of the spec requirement' },
          status: { type: 'string', enum: ['covered', 'weak', 'missing', 'misplaced', 'contradicted'] },
          treatment: { type: 'string', description: 'where revision 2 covers it: file, section, phase, exit criterion, or record' },
          fix: { type: 'string', description: 'when not covered: the exact sentence or table row to add and where; empty otherwise' },
        },
        required: ['id', 'requirement', 'status', 'treatment', 'fix'],
      },
    },
    summary: { type: 'string' },
    counts: { type: 'object', properties: { covered: { type: 'number' }, weak: { type: 'number' }, missing: { type: 'number' }, misplaced: { type: 'number' }, contradicted: { type: 'number' } }, required: ['covered', 'weak', 'missing', 'misplaced', 'contradicted'] },
  },
  required: ['rows', 'summary', 'counts'],
}

const prompt = (r) => `You are a completeness critic for revision 2 of the Heleos-spark build roadmap. Read-only: do not modify any file. Never attempt to locate or inspect the quarantined predecessor repository.

Inputs:
- The spec (architecture authority): ${S}/branches/main/docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md. Read sections ${r.sections} in full.
- The requirement checklist for those sections, produced by an earlier audit: ${S}/findings/coverage-rows.json, a JSON array of [audit_key, id, requirement_text, revision_1_treatment]. Use only the rows whose audit_key is "${r.key}" and whose id is non-empty (skip rows with an empty id; they are audit meta-facts).
- Revision 2 drafts (the documents under review): ${S}/draft/ROADMAP.md, ${S}/draft/docs/roadmap/phase-0-evidence.md, ${S}/draft/docs/roadmap/plan-of-record-audit.md, ${S}/draft/docs/roadmap/dynamic-workflow-operating-model.md, ${S}/draft/docs/roadmap/skills-and-plugins.md, ${S}/draft/docs/roadmap/decision-drafts.md, ${S}/draft/CLAUDE.md, ${S}/draft/README.md. Read ROADMAP.md in full; grep the others.

Task: for every checklist row of your range, decide how revision 2 treats the requirement: covered (a phase, exit criterion, decision, test, record, or workflow in revision 2 names it in a way that can be checked at a gate), weak (mentioned, but no exit criterion, record, decision, or test binds it), missing (not present anywhere in the drafts), misplaced (scheduled in a phase inconsistent with the spec's order or its "may not delay" rule), contradicted (revision 2 says something the spec forbids). Before calling a requirement missing, grep every draft for the concept under several plausible wordings (the roadmap often groups requirements into one sentence with the spec's own words). A roadmap schedules and gates; a spec rule that needs no phase output (a pure prohibition already binding through the spec and CLAUDE.md) counts as covered when CLAUDE.md or the operating model restates it as a rule for workers. For every row that is not covered, write the exact fix: the sentence or table row to add and the file and section it belongs in. Keep treatment strings short (file:section, phase, exit criterion). Output only the structured object; counts must match the rows.`

phase('Map')
const results = (await parallel(RANGES.map(r => () =>
  agent(prompt(r), { label: `fill-${r.label}`, phase: 'Map', schema: SCHEMA, effort: 'high' }).then(x => x && ({ range: r.label, key: r.key, ...x }))
))).filter(Boolean)
const dropped = RANGES.map(r => r.label).filter(l => !results.some(x => x.range === l))
const totals = { covered: 0, weak: 0, missing: 0, misplaced: 0, contradicted: 0 }
for (const x of results) for (const k of Object.keys(totals)) totals[k] += (x.counts[k] || 0)
log(`${results.length}/${RANGES.length} ranges mapped; totals ${JSON.stringify(totals)}; dropped: ${dropped.join(', ') || 'none'}`)
return { status: dropped.length ? 'PARTIAL' : 'COMPLETE', results, totals, dropped }