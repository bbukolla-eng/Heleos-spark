export const meta = {
  name: 'design-operating-model',
  description: 'Judge panel: three independent designs for the dynamic-workflow operating model, scored, then synthesized',
  phases: [
    { title: 'Design', detail: 'three designers, distinct angles' },
    { title: 'Judge', detail: 'three judges score every design' },
    { title: 'Synthesize', detail: 'winner plus grafts into one document' },
  ],
}

const S = '<scratch>'

const CONTEXT = `Project: Heleos-spark, a clean-room, evidence-first HVAC / Division 23 takeoff system in its foundation-design phase. Repository working tree: <repo> (read-only for you; do not modify, check out, or commit). Writable scratchpad: ${S}/work/<your-label>/.

Read these before designing (all are plain files):
- The spec, which is the architecture authority: ${S}/branches/main/docs/superpowers/specs/2026-08-26-heleos-spark-foundation-design.md. Read sections 3 (principles), 4 (three lanes), 5 (data classes and egress), 10 (AI worker and automation boundaries), 11 (skills and plugin policy), 13 (failure behaviour and security controls), 14 (Foundation 0.1 and its acceptance list), 15 (subsequent sequence), 16 (approval) in full.
- The roadmap draft under review (PR #3): ${S}/branches/pr3/ROADMAP.md (all of it; note its 'Task graph', 'Graphs', 'Loops' and 'Session checklist' sections), ${S}/branches/pr3/CLAUDE.md, ${S}/branches/pr3/docs/roadmap/skills-and-plugins.md, ${S}/branches/pr3/docs/workspace/claude-code-lane.md.
- Option A, the reconciliation plan (nine test-driven tasks for Foundation 0.1): ${S}/branches/pr3/docs/superpowers/plans/2026-08-27-recovery-reconciliation.md (skim: Global Constraints, File Structure, the task list and each task's Files and Steps).
- Option B exists as PR #1 at ${S}/branches/pr1 (a 294-file implementation with its own 'build fabric' under tools/helios_build and build_control/; read ${S}/branches/pr1/build_control/README.md and skim ${S}/branches/pr1/docs/superpowers/specs/2026-09-01-enriched-build-fabric-design.md to know what a competing controller design looks like; do not copy it).

The harness this project will run in is Claude Code (web sessions and local CLI). Its relevant built-in capabilities, which the roadmap draft does not yet use:
1. The Workflow tool runs a plain-JavaScript script that orchestrates subagents deterministically. The script is written by a session (or stored in the repository under .claude/workflows/<name>.js and invoked by name), and the harness executes it. Primitives: agent(prompt, {label, phase, schema, model, effort, isolation: 'worktree', agentType}) spawns one subagent and returns its final text or, with a JSON schema, a validated object; parallel([thunks]) runs tasks concurrently as a barrier; pipeline(items, stage1, stage2, ...) runs each item through stages independently with no barrier; log() and phase() report progress; budget.total / budget.spent() / budget.remaining() expose a hard output-token ceiling for the run, and agent() throws once it is reached; args passes parameters in. Constraints: Date.now(), Math.random(), and new Date() are unavailable (the run must be replayable), no filesystem or Node API in the script itself (only agents touch files), concurrency is capped at min(16, CPUs minus 2) per workflow, at most 1000 agents per run, at most 4096 items per parallel or pipeline call. Every run persists its script and a journal.jsonl recording each agent's return value; a run can be resumed after a script edit with unchanged agent calls served from cache. Subagents can use every tool the session can (files, shell, git, the GitHub MCP server), which means the script and prompts, not the tool, must forbid merging, approving, or pushing to other branches; mechanical enforcement is the lane guard hook from PR #3 and GitHub branch protection.
2. isolation: 'worktree' gives an agent its own git worktree so parallel agents that edit files do not collide; unchanged worktrees are removed automatically.
3. The Agent tool spawns a single subagent ad hoc (read-only Explore and Plan agent types exist alongside general-purpose).
4. GitHub Actions is available for CI (no workflow files exist yet on any branch). GitHub branch protection can require pull requests and status checks.
5. Established patterns the Workflow tool supports: adversarial verification (N independent skeptics prompted to refute a finding; kill it when a majority refutes), perspective-diverse verification (each verifier gets a different lens), judge panel (N independent attempts scored by independent judges and synthesized), loop-until-dry (keep spawning finders until K consecutive rounds return nothing new), multi-modal sweep, completeness critic, and the rule that any bound on coverage must be logged rather than silent.

Spec section 10 requires that a deterministic controller, not an LLM, owns permissions, budgets, leases, timeouts, idempotency, state transitions, and stop conditions; that every worker receives a frozen base commit, exact objective, allowed paths and tools, acceptance tests, forbidden changes, and time/action/cost limits; that workers return patches and reports and never push to main, approve themselves, merge, modify credentials, or change production truth. Section 11 treats skills and workflows as executable supply chain: versioned, tested, reviewed, pinned. Section 13 requires that untrusted content (PDFs, web pages, datasets, model cards, issue text) is data, never instructions.`

const DESIGN_TASK = `Deliverable: a complete operating-model design, in markdown, titled by you, that says exactly how Heleos-spark work is run as dynamic multi-agent workflows in this harness, for every roadmap phase (Phase 0 approve-and-prepare; Phase 1 Foundation 0.1 under either Option A or Option B; Phase 2 knowledge lane and bakeoff harness; Phase 3 sheets, scale, schedules, tags; Phase 4 airside takeoff, corrections, exports; Phase 5 expansion). It must cover, with concrete mechanisms rather than aspirations:
(a) The controller question: which of section 10's controller responsibilities the Workflow script itself satisfies (it is deterministic, non-LLM control flow with a hard token budget and replayable journal), which the harness satisfies (permission modes, tool allow-lists, worktree isolation), which the lane guard and GitHub branch protection satisfy, and which remain unsatisfied and need repository code (for example leases, heartbeats, idempotency keys, per-task action budgets) with a proposal for the minimal repository-owned piece that closes each gap and in which phase it is built.
(b) The worker contract: how a task contract (frozen base commit, objective, allowed paths, allowed tools, acceptance tests, forbidden changes, limits) is expressed as a workflow args object and injected into every worker prompt; how a worker is prevented from reading or acting on instructions embedded in untrusted inputs; how patches come back (branch on the lane plus a report object with a schema) and never as merges.
(c) The task diamond for one Foundation 0.1 task: planner, parallel workers in isolated worktrees (say how many and why), verifier in a separate context that runs the task's acceptance commands and can only pass or fail, adversarial reviewers, merge owner (a human until the controller exists), owner gate; the exact schema of each stage's return object; what gets recorded and where (a run record under docs/verification/ or docs/runs/, with the run id, script hash, base commit, budget spent, verifier output).
(d) Repository-owned workflows: which named workflows live under .claude/workflows/ from Phase 0 onward (for example roadmap-review, spec-coverage-audit, foundation-task, pr-review, provenance-audit, bakeoff-run, research-triage), each with purpose, inputs (args), stages, schemas, budget, stop rule, verifier, and the human gate; how they are versioned, tested, and pinned per section 11; how a change to a workflow script is reviewed.
(e) Budgets, stop rules, kill switches: per-workflow token ceilings, the agent count cap, the rule for what happens at 80 percent of budget, how a run is paused or killed, how a partial run is reported (never as complete), and how repeated failures escalate to the owner.
(f) Verification discipline: which findings and patches require adversarial verification and with how many independent votes; how verifiers are kept blind to worker context; how a verifier proves 'from a clean checkout' and 'zero external network calls'; how CI (GitHub Actions on macos-14 and windows-latest) relates to workflow verifiers; how the ten section 14 acceptance bullets become verifier checks.
(g) Egress and data classes: which workflows may touch PROJECT_CONFIDENTIAL content, which may call external MCP servers (Context7, Exa, Hugging Face), and how a workflow declares and logs that per section 5.
(h) Reporting and provenance: how each run's evidence (journal, schemas, verifier output, command output, hashes) is turned into the repository record the spec requires, and how the owner reads the state of the project from those records alone.
(i) What is deliberately NOT automated (owner-only actions per sections 13 and 16), stated as a list.
(j) A sequenced adoption plan: what is in place at Phase 0 exit, Phase 1 exit, and so on, with the exit checks that prove it.
Keep it executable from a fresh clone of the repository: no dependency on files outside the repository except pinned upstream URLs. Prefer concrete schemas (as JSON-like blocks), prompts fragments, file paths, and commands to prose. Length: as long as needed to be complete; do not pad. Output ONLY the structured object; put the full design in design_markdown.`

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

const ANGLES = [
  { key: 'design-spec-fidelity', angle: 'SPEC-FIDELITY FIRST. Start from every sentence of spec sections 10, 11 and 13 and derive the operating model as the minimal set of mechanisms that satisfies each one. Be strict and literal about what the Workflow tool cannot guarantee and never claim more than the harness provides; where a requirement is unmet, say so and specify the repository-owned code that closes it. Prefer fewer moving parts.' },
  { key: 'design-throughput', angle: 'THROUGHPUT FIRST. Design for the shortest wall-clock path from an approved plan to accepted milestones: dependency graphs over plan tasks, pipelines instead of barriers, isolated worktrees for parallel workers, generous but bounded parallelism, caching and resume. Then make it safe: every parallel shortcut must show how it still satisfies the authority and verification rules. Quantify expected agent counts and budgets per task and per phase.' },
  { key: 'design-risk-first', angle: 'RISK FIRST. Enumerate the ways a multi-agent build of this system goes wrong (prompt injection from PDFs and web content, worker drift off the lane, a verifier that shares context with a worker, silent truncation of coverage, self-approval, budget blowups, non-reproducible runs, provenance gaps, external egress of confidential drawings, model identifiers drifting, hallucinated test results) and design the operating model so each failure mode has a named control, a detector, and an escalation. Verification-heavy: adversarial reviewers, blind verifiers, diverse lenses, completeness critics. Then trim anything that adds cost without closing a risk.' },
]

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
          errors: { type: 'array', items: { type: 'string' }, description: 'claims in the design that are false about the spec or the harness' },
        },
        required: ['design', 'spec_fidelity', 'executability', 'completeness', 'clarity', 'total', 'strengths', 'weaknesses', 'errors'],
      },
    },
    winner: { type: 'string' },
    grafts: { type: 'array', items: { type: 'object', properties: { from: { type: 'string' }, idea: { type: 'string' }, why: { type: 'string' } }, required: ['from', 'idea', 'why'] } },
    missing_everywhere: { type: 'array', items: { type: 'string' }, description: 'requirements or risks none of the designs handles' },
  },
  required: ['scores', 'winner', 'grafts', 'missing_everywhere'],
}

phase('Design')
const designs = (await parallel(ANGLES.map(a => () =>
  agent(`${CONTEXT}\n\nYour design angle: ${a.angle}\n\n${DESIGN_TASK}`, { label: a.key, phase: 'Design', schema: DESIGN_SCHEMA, effort: 'high' })
    .then(d => d && ({ key: a.key, ...d }))
))).filter(Boolean)
log(`${designs.length}/${ANGLES.length} designs produced`)
if (designs.length === 0) return { error: 'no designs produced' }

phase('Judge')
const bundle = designs.map(d => `\n\n==================== DESIGN "${d.key}" — ${d.title} (angle: ${d.angle}) ====================\n${d.design_markdown}\n\nKnown limitations stated by its author:\n- ${d.known_limitations.join('\n- ')}`).join('\n')
const JUDGE_LENSES = [
  { key: 'judge-spec', lens: 'Judge primarily as the spec\'s author: does each design honour sections 3, 5, 10, 11, 13, 16 literally? Flag every claim that overstates what a mechanism guarantees. Score spec_fidelity with the most weight.' },
  { key: 'judge-operator', lens: 'Judge primarily as the operator who must run this from a fresh clone next week with only Claude Code, git, GitHub, and Python: is every step concrete, is anything dependent on files outside the repository, are the schemas and commands usable as written, are the budgets realistic? Score executability with the most weight.' },
  { key: 'judge-adversary', lens: 'Judge primarily as an adversary trying to make the build produce wrong production truth or leak confidential drawings: which design leaves the fewest openings (self-approval, verifier contamination, prompt injection, egress, silent truncation, non-reproducible runs)? Score completeness with the most weight and list every opening you find as a weakness or an item in missing_everywhere.' },
]
const judgments = (await parallel(JUDGE_LENSES.map(j => () =>
  agent(`${CONTEXT}\n\nYou are one of three independent judges. ${j.lens}\n\nScore each design 0 to 10 on spec_fidelity, executability, completeness, clarity (total = sum, max 40). List strengths, weaknesses, and factual errors per design. Name a winner. List grafts: specific ideas from non-winning designs that the final document should adopt. List what is missing from every design. Judge only what is written; do not reward length.\n${bundle}`, { label: j.key, phase: 'Judge', schema: JUDGE_SCHEMA, effort: 'high' })
    .then(r => r && ({ key: j.key, ...r }))
))).filter(Boolean)
log(`${judgments.length}/${JUDGE_LENSES.length} judgments returned`)

const totals = {}
for (const j of judgments) for (const s of j.scores) totals[s.design] = (totals[s.design] || 0) + s.total
const ranking = Object.entries(totals).sort((a, b) => b[1] - a[1])
log(`ranking: ${ranking.map(([k, v]) => `${k}=${v}`).join(', ')}`)
const winnerKey = ranking.length ? ranking[0][0] : designs[0].key
const winner = designs.find(d => d.key === winnerKey || d.title === winnerKey) || designs[0]

phase('Synthesize')
const judgeNotes = judgments.map(j => `--- ${j.key} ---\nwinner: ${j.winner}\n` + j.scores.map(s => `${s.design}: total ${s.total} (spec ${s.spec_fidelity}, exec ${s.executability}, complete ${s.completeness}, clarity ${s.clarity})\n  strengths: ${s.strengths.join(' | ')}\n  weaknesses: ${s.weaknesses.join(' | ')}\n  errors: ${s.errors.join(' | ')}`).join('\n') + `\ngrafts: ${j.grafts.map(g => `[from ${g.from}] ${g.idea} (${g.why})`).join(' || ')}\nmissing everywhere: ${j.missing_everywhere.join(' | ')}`).join('\n\n')
const SYNTH_SCHEMA = {
  type: 'object',
  properties: {
    markdown: { type: 'string', description: 'the complete operating-model document' },
    adopted_grafts: { type: 'array', items: { type: 'string' } },
    resolved_missing: { type: 'array', items: { type: 'string' } },
    open_questions_for_owner: { type: 'array', items: { type: 'string' } },
  },
  required: ['markdown', 'adopted_grafts', 'resolved_missing', 'open_questions_for_owner'],
}
const synthesis = await agent(`${CONTEXT}\n\nYou are the synthesizer. Three designs were produced and judged. The winner by total score is "${winner.key}" (${winner.title}). Write the single, final operating-model document for the repository, to be saved as docs/roadmap/dynamic-workflow-operating-model.md and referenced from ROADMAP.md. Start from the winner, correct every factual error the judges found, adopt every graft that closes a gap, and close every 'missing everywhere' item with a concrete mechanism or, if it cannot be closed in this harness, an explicit statement that it stays owner-only or needs repository code in a named phase. Requirements for the document: markdown; a status line 'Proposed for owner review', date 2026-09-02, authority line pointing at the spec; sections in this order: Purpose; What the harness provides and what it does not (a table mapping every section 10 controller responsibility to its mechanism and status); The worker contract (with the args schema as a JSON block); The task diamond (with a mermaid flowchart using plain flowchart syntax and the return schema of each stage); Repository-owned workflows (a table, then one subsection per workflow with inputs, stages, budget, stop rule, verifier, human gate); Budgets, stop rules, kill switches; Verification discipline (including how the ten section 14 acceptance bullets become verifier checks and how CI relates); Egress and data classes; Run records and provenance (exact file layout under docs/runs/ and what each field means); Owner-only actions; Adoption by phase (table with exit checks); Open questions for the owner. Use no em dashes. Prefer tables, JSON blocks, and commands over prose. Do not reference any file outside the repository except by pinned URL. Do not invent harness capabilities beyond those listed in the context. Output only the structured object.\n\n==================== JUDGMENTS ====================\n${judgeNotes}\n${bundle}`, { label: 'synthesize', phase: 'Synthesize', schema: SYNTH_SCHEMA, effort: 'high' })

return { winner: winner.key, ranking, designs: designs.map(d => ({ key: d.key, title: d.title, summary: d.summary, key_mechanisms: d.key_mechanisms, known_limitations: d.known_limitations })), judgments, synthesis }