// Heleos-spark repository-owned workflow: spec-coverage-audit
// Run by the Claude Code Workflow tool by name ({ name: "spec-coverage-audit", args: {...} }).
// Version 1.0.0. Provenance: generalized from the audit run that produced roadmap revision 2
// (docs/runs/2026-09-02-roadmap-revision-2/README.md). Read-only: no agent may edit the repository.
//
// args = {
//   spec_path: "docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md",
//   targets: ["ROADMAP.md", "docs/roadmap/phase-0-evidence.md", ...],   // documents that must cover the spec
//   section_ranges: [{ label: "2-6", sections: "2 to 6", focus: "..." }, ...],
//   context: "one paragraph the auditors need (branch, exports, dates)",
//   require_ceiling: false,          // true refuses to run without a +Nk token ceiling
//   effort: "high"
// }
export const meta = {
  name: 'spec-coverage-audit',
  description: 'Map every checkable requirement of chosen spec sections to where the target documents cover it; emit a finding for every gap',
  phases: [{ title: 'Audit', detail: 'one auditor per section range, in parallel' }],
}

const A = args || {}
if (!A.spec_path || !Array.isArray(A.targets) || !A.targets.length || !Array.isArray(A.section_ranges) || !A.section_ranges.length) {
  return { status: 'ABORTED_BAD_ARGS', reason: 'spec_path, targets[] (at least one), section_ranges[] (at least one) are required' }
}
if (A.require_ceiling && budget.total === null) return { status: 'ABORTED_NO_CEILING' }
if (budget.total === null) log('no token ceiling was set for this run; the policy ceiling is advisory only')

const FINDINGS = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          category: { type: 'string' },
          severity: { type: 'string', enum: ['blocker', 'major', 'minor', 'info'] },
          claim: { type: 'string' },
          location: { type: 'string' },
          evidence: { type: 'string' },
          recommendation: { type: 'string' },
          confidence: { type: 'number' },
        },
        required: ['id', 'category', 'severity', 'claim', 'location', 'evidence', 'recommendation', 'confidence'],
      },
    },
    verified_facts: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          fact: { type: 'string', description: 'requirement id and text' },
          value: { type: 'string', description: 'treatment: covered (where), missing, misplaced, contradicted, or weak' },
          command: { type: 'string' },
          status: { type: 'string', enum: ['verified', 'discrepant', 'unverifiable'] },
        },
        required: ['fact', 'value', 'command', 'status'],
      },
    },
    summary: { type: 'string' },
  },
  required: ['findings', 'verified_facts', 'summary'],
}

const COMMON = `You are a read-only auditor for the Heleos-spark repository. Do not modify, check out, or commit anything; use only read commands. Never attempt to locate or inspect the quarantined predecessor repository. ${A.context || ''}

Standards: be exhaustive and precise; quote file paths and line numbers; every finding carries the evidence you actually looked at; mark what you cannot verify as unverifiable. Severity: blocker = makes the target document wrong or unexecutable; major = a real gap or contradiction to fix before the owner relies on it; minor = correctness or clarity; info = a verified fact worth recording. Your output is consumed by a program: return only the structured object.`

const prompt = (r) => `${COMMON}

Task: Spec-coverage audit for sections ${r.sections} of the spec at ${A.spec_path}. Read those sections in full. Enumerate every concrete, checkable requirement (number them S<section>-<n>). For each, determine how the target documents treat it, reading ALL of: ${A.targets.join(', ')}. Classify each requirement: covered (which phase or section and where), missing (scheduled nowhere), misplaced (scheduled inconsistently with the spec's ordering or its "may not delay" rules), contradicted (the target says something the spec forbids), weak (mentioned with no exit criterion, owner action, record, or test). Before calling anything missing, grep the whole target set for the concept under several plausible wordings. Emit a finding for every requirement that is not covered, with the spec quote, the target quote or "absent", and a concrete recommendation naming the phase and the exit criterion or record to add. Record the full checklist, covered items included, in verified_facts (fact = requirement id and text, value = treatment and location, command = "n/a", status = "verified"). ${r.focus ? 'Pay particular attention to: ' + r.focus : ''}`

phase('Audit')
const results = (await parallel(A.section_ranges.map(r => () =>
  agent(prompt(r), { label: `coverage-${r.label}`, phase: 'Audit', schema: FINDINGS, effort: A.effort || 'high' })
    .then(x => x && ({ range: r.label, ...x }))
))).filter(Boolean)

const dropped = A.section_ranges.map(r => r.label).filter(l => !results.some(x => x.range === l))
const total = results.reduce((n, x) => n + x.findings.length, 0)
log(`${results.length}/${A.section_ranges.length} ranges audited; ${total} findings; dropped: ${dropped.join(', ') || 'none'}`)
return { status: dropped.length ? 'PARTIAL' : 'COMPLETE', audits: results, dropped, spent: budget.spent() }
